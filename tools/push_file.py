#!/usr/bin/env python3
"""Copy a file to a MicroPython device in verified chunks.

`mpremote cp` sends a file as one long transfer. On the T-Deck Pro's USB-JTAG
bridge a long transfer fails often, and it fails destructively: the remote file
is opened for writing before the transfer dies, so a failed copy leaves a
truncated file behind. Retrying makes it worse, not better -- a loop of failed
`cp` calls will happily shorten ui.py to 5KB and leave the device unable to
boot the app.

This writes to a temporary name in small base64 chunks, retries each chunk on
its own, verifies the SHA-256 on the device, and only then renames over the
target. A failure at any point leaves the original file untouched.

    tools/push_file.py /dev/cu.usbmodem101 ui.py ui.py
    tools/push_file.py /dev/cu.usbmodem101 lib/eink_shim.py lib/eink_shim.py
"""

import base64
import hashlib
import os
import subprocess
import sys

MPREMOTE = os.path.expanduser("~/.local/bin/mpremote")
CHUNK = 1024          # raw bytes per chunk; ~1.4KB once base64'd
ATTEMPTS = 6


def run(port, code, timeout=60):
    """One mpremote exec. Returns (ok, output)."""
    try:
        p = subprocess.run([MPREMOTE, "connect", port, "exec", code],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "timeout"
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def retry(port, code, what, timeout=60):
    for attempt in range(1, ATTEMPTS + 1):
        ok, out = run(port, code, timeout)
        if ok:
            return out
        if attempt == ATTEMPTS:
            print("    %s failed after %d attempts: %s" % (what, ATTEMPTS, out[:200]))
            return None
    return None


def push(port, src, dst):
    data = open(src, "rb").read()
    want = hashlib.sha256(data).hexdigest()
    tmp = "/" + os.path.basename(dst) + ".part"
    target = "/" + dst.lstrip("/")

    print("  %s -> %s (%d bytes, %d chunks)"
          % (src, target, len(data), (len(data) + CHUNK - 1) // CHUNK))

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

    out = retry(port, "import hashlib, binascii\n"
                      "h=hashlib.sha256()\n"
                      "f=open(%r,'rb')\n"
                      "while True:\n"
                      "    b=f.read(512)\n"
                      "    if not b: break\n"
                      "    h.update(b)\n"
                      "f.close()\n"
                      "print('SHA', binascii.hexlify(h.digest()).decode())" % tmp,
                "verify", timeout=120)
    if out is None:
        return False
    got = ""
    for line in out.splitlines():
        if line.startswith("SHA "):
            got = line.split()[1]
    if got != want:
        print("    checksum mismatch: device %s, host %s" % (got[:16], want[:16]))
        return False

    if retry(port, "import os\ntry:\n    os.remove(%r)\nexcept OSError:\n    pass\n"
                   "os.rename(%r, %r)" % (target, tmp, target), "rename") is None:
        return False
    print("    ok, sha256 %s" % got[:16])
    return True


def main():
    if len(sys.argv) < 4 or len(sys.argv) % 2 != 0:
        print(__doc__)
        return 2
    port = sys.argv[1]
    pairs = list(zip(sys.argv[2::2], sys.argv[3::2]))
    failed = []
    for src, dst in pairs:
        if not push(port, src, dst):
            failed.append(src)
    if failed:
        print("FAILED: %s" % ", ".join(failed))
        return 1
    print("all files pushed and verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
