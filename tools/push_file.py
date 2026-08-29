#!/usr/bin/env python3
"""Copy files to a MicroPython device safely, and verify what is already there.

`mpremote cp` sends a file as one long transfer. On the T-Deck Pro's USB-JTAG
bridge a long transfer fails often, and it fails destructively: the remote file
is opened for writing before the transfer dies, so a failed copy leaves a
truncated file behind. Retrying makes it worse, not better -- a loop of failed
`cp` calls will happily shorten ui.py to 5KB and leave the device unable to
boot the app. This is the whole reason tools/deploy_pro.sh does not use `cp`.

What this does instead, per file:

  1. Hash the device's copy. If it already matches, do nothing -- so a redeploy
     that changed one file costs one file, and a redeploy that changed nothing
     is a verification pass.
  2. Otherwise write to a temporary name in small chunks, retrying each chunk
     on its own, and verify the SHA-256 on the device.
  3. Swap it in by renaming the old file aside first, so there is never an
     instant with no copy of it on the device. A run interrupted mid-swap is
     recovered on the next run.

    tools/push_file.py PORT SRC DST [SRC DST ...]
    tools/push_file.py --check PORT SRC DST [...]   # verify only, write nothing
"""

import base64
import hashlib
import os
import subprocess
import sys

MPREMOTE = os.path.expanduser("~/.local/bin/mpremote")
CHUNK = 4096          # raw bytes per chunk; ~5.5KB once base64'd
ATTEMPTS = 6

_SHA_ON_DEVICE = """
import hashlib, binascii
try:
    h = hashlib.sha256()
    f = open(%r, 'rb')
    while True:
        b = f.read(512)
        if not b:
            break
        h.update(b)
    f.close()
    print('SHA', binascii.hexlify(h.digest()).decode())
except OSError:
    print('SHA missing')
"""


def run(port, code, timeout=120):
    """One mpremote exec. Returns (ok, output)."""
    try:
        p = subprocess.run([MPREMOTE, "connect", port, "exec", code],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "timeout"
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def retry(port, code, what, timeout=120):
    for attempt in range(1, ATTEMPTS + 1):
        ok, out = run(port, code, timeout)
        if ok:
            return out
        if attempt == ATTEMPTS:
            print("    %s failed after %d attempts: %s" % (what, ATTEMPTS, out[:200]))
            return None
    return None


def device_sha(port, path):
    """The device's SHA-256 of path, 'missing', or None if the read failed."""
    out = retry(port, _SHA_ON_DEVICE % path, "hash of %s" % path)
    if out is None:
        return None
    for line in out.splitlines():
        if line.startswith("SHA "):
            return line.split()[1]
    return None


def ensure_dir(port, path):
    """mkdir -p for the parent of a device path."""
    parts = path.strip("/").split("/")[:-1]
    if not parts:
        return True
    grown = ""
    for part in parts:
        grown += "/" + part
        if retry(port, "import os\ntry:\n    os.mkdir(%r)\nexcept OSError:\n    pass"
                       % grown, "mkdir %s" % grown) is None:
            return False
    return True


def recover(port, target):
    """Undo an interrupted swap: target gone but its .bak still there.

    The swap renames the old file aside before renaming the new one into
    place, so this is the one window where target does not exist -- and the
    old contents are always still on the device under .bak.
    """
    return retry(port,
                 "import os\n"
                 "try:\n"
                 "    os.stat(%r)\n"
                 "except OSError:\n"
                 "    try:\n"
                 "        os.rename(%r, %r)\n"
                 "        print('RECOVERED')\n"
                 "    except OSError:\n"
                 "        pass\n" % (target, target + ".bak", target),
                 "recover %s" % target)


def push(port, src, dst, check_only=False):
    data = open(src, "rb").read()
    want = hashlib.sha256(data).hexdigest()
    target = "/" + dst.lstrip("/")
    tmp = target + ".part"

    out = recover(port, target)
    if out and "RECOVERED" in out:
        print("  %s: restored from an interrupted earlier run" % target)

    have = device_sha(port, target)
    if have is None:
        print("  %s: could not read the device" % target)
        return False
    if have == want:
        print("  %s up to date (%d bytes)" % (target, len(data)))
        return True
    if check_only:
        print("  %s MISMATCH: device %s, host %s"
              % (target, "missing" if have == "missing" else have[:16], want[:16]))
        return False

    print("  %s -> %s (%d bytes, %d chunks)%s"
          % (src, target, len(data), (len(data) + CHUNK - 1) // CHUNK,
             "" if have != "missing" else "  [new]"))

    if not ensure_dir(port, target):
        return False

    # Start clean. The temporary name means the real file is never in a
    # half-written state, however badly this goes.
    if retry(port, "import os\ntry:\n    os.remove(%r)\nexcept OSError:\n    pass\n"
                   "open(%r,'wb').close()" % (tmp, tmp), "open") is None:
        return False

    for i in range(0, len(data), CHUNK):
        piece = base64.b64encode(data[i:i + CHUNK]).decode()
        code = ("import binascii\n"
                "f=open(%r,'ab')\n"
                "f.write(binascii.a2b_base64(%r))\n"
                "f.close()" % (tmp, piece))
        if retry(port, code, "chunk at %d" % i) is None:
            return False
        done = min(i + CHUNK, len(data))
        sys.stdout.write("\r    %d/%d bytes" % (done, len(data)))
        sys.stdout.flush()
    print()

    got = device_sha(port, tmp)
    if got != want:
        print("    checksum mismatch: device %s, host %s"
              % ((got or "?")[:16], want[:16]))
        return False

    # Swap. The old file is renamed aside rather than deleted, so at no point
    # is there no copy of it on the device -- an interrupted swap leaves the
    # old contents under .bak and recover() puts them back on the next run.
    if retry(port,
             "import os\n"
             "try:\n"
             "    os.remove(%r)\n"          # a stale .bak from an older run
             "except OSError:\n"
             "    pass\n"
             "try:\n"
             "    os.rename(%r, %r)\n"      # target -> target.bak
             "except OSError:\n"
             "    pass\n"
             "os.rename(%r, %r)\n"          # tmp -> target
             "try:\n"
             "    os.remove(%r)\n"          # drop the backup
             "except OSError:\n"
             "    pass\n"
             % (target + ".bak", target, target + ".bak", tmp, target,
                target + ".bak"),
             "swap") is None:
        return False
    print("    ok, sha256 %s" % want[:16])
    return True


def main():
    argv = sys.argv[1:]
    check_only = False
    if argv and argv[0] == "--check":
        check_only = True
        argv = argv[1:]
    if len(argv) < 3 or len(argv) % 2 == 0:
        print(__doc__)
        return 2
    port = argv[0]
    pairs = list(zip(argv[1::2], argv[2::2]))

    failed = []
    for src, dst in pairs:
        if not push(port, src, dst, check_only):
            failed.append(dst)
    print()
    if failed:
        print("%s: %d of %d file(s) %s"
              % ("CHECK FAILED" if check_only else "FAILED", len(failed),
                 len(pairs), "do not match" if check_only else "did not push"))
        for f in failed:
            print("  %s" % f)
        return 1
    print("%d file(s) %s" % (len(pairs),
                             "verified" if check_only else "pushed and verified"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
