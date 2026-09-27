#!/usr/bin/env python3
"""Play a Reticulum destination hash as DTMF tones for the T-Deck to hear.

On the T-Deck: press m (Find), then 0 (the mic key) on an empty query, and
hold it near the Mac's speaker. It fills the hash in; Enter adds it.

    dtmf_send.py a1b2c3d4e5f60718293a4b5c6d7e8f90
    echo "<a1b2...>" | dtmf_send.py        # first 32-hex run in stdin
    dtmf_send.py                           # ...or on the clipboard
    dtmf_send.py HASH --wav out.wav        # write, don't play

Format (dtmf.py decodes it): the 32 hash digits plus a 4-digit
CRC-16/CCITT-FALSE of the 16 hash bytes, one DTMF tone per hex digit
(a-d -> A-D, e -> *, f -> #), 80 ms on / 40 ms off, high tone +3 dB,
played twice. Hold the T-Deck: lying on a desk, its case can notch out the
A-D tone (1633 Hz).

Python stdlib only, so it runs from a Shortcuts Quick Action as-is.
"""

import argparse
import binascii
import math
import os
import re
import struct
import subprocess
import sys
import tempfile
import wave

LOW = (697, 770, 852, 941)
HIGH = (1209, 1336, 1477, 1633)
_KEYS = ("123A", "456B", "789C", "*0#D")
_HEX2KEY = "0123456789ABCD*#"

# hex digit -> (low Hz, high Hz)
TONES = {}
for _r, _row in enumerate(_KEYS):
    for _c, _k in enumerate(_row):
        TONES["0123456789abcdef"[_HEX2KEY.index(_k)]] = (LOW[_r], HIGH[_c])

TONE_MS = 80
GAP_MS = 40
LEAD_MS = 300
COPY_GAP_MS = 600
RAMP_MS = 5
# Standard DTMF forward twist: high tone +3 dB over the low one. Measured on
# a T-Deck, the mic path is weaker at 1.2-1.7 kHz than at 700-950 Hz, and the
# speaker's 2nd harmonic of the low tone (e.g. 2x770 = 1540 Hz) lands next to
# the 1633 Hz tone; the twist keeps the high tone clear of both.
LOW_LEVEL = 0.30
HIGH_LEVEL = LOW_LEVEL * 10 ** (3 / 20)    # pair peak 0.72 FS (-3 dBFS)

_HASH_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32}(?![0-9a-fA-F])")


def extract_hash(text):
    """First run of exactly 32 hex characters in text, lowercased, or None."""
    m = _HASH_RE.search(text or "")
    return m.group(0).lower() if m else None


def frame(hash_hex):
    """36 hex digits: the hash and its CRC-16/CCITT-FALSE."""
    return hash_hex + "%04x" % binascii.crc_hqx(bytes.fromhex(hash_hex), 0xFFFF)


def render(digits, rate=44100, copies=2):
    """Samples (floats, int16 scale) for a hex-digit string, per the format."""
    tone_n = TONE_MS * rate // 1000
    ramp_n = RAMP_MS * rate // 1000
    gap = [0.0] * (GAP_MS * rate // 1000)
    env = [1.0] * tone_n
    for i in range(ramp_n):
        e = 0.5 - 0.5 * math.cos(math.pi * i / ramp_n)
        env[i] = env[tone_n - 1 - i] = e
    a_lo = LOW_LEVEL * 32767
    a_hi = HIGH_LEVEL * 32767
    block = []
    for ch in digits:
        lo, hi = TONES[ch]
        wl = 2 * math.pi * lo / rate
        wh = 2 * math.pi * hi / rate
        block += [env[i] * (a_lo * math.sin(wl * i) + a_hi * math.sin(wh * i))
                  for i in range(tone_n)]
        block += gap
    out = [0.0] * (LEAD_MS * rate // 1000)
    for c in range(copies):
        if c:
            out += [0.0] * (COPY_GAP_MS * rate // 1000)
        out += block
    return out + [0.0] * (LEAD_MS * rate // 1000)


def encode(hash_hex, rate=44100, copies=2):
    return render(frame(hash_hex), rate, copies)


def write_wav(samples, rate, path):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(round(s))) for s in samples))


def _notify(msg):
    try:
        subprocess.run(["osascript", "-e",
                        'display notification "%s" with title "Transfer via sound"' % msg],
                       timeout=5, check=False)
    except Exception:
        pass


def _input_text(arg):
    if arg:
        return arg
    if not sys.stdin.isatty():
        text = sys.stdin.read()
        if text.strip():
            return text
    try:
        return subprocess.run(["pbpaste"], capture_output=True, text=True,
                              timeout=5).stdout
    except Exception:
        return ""


def main(argv=None):
    p = argparse.ArgumentParser(description="Play a 32-hex hash as DTMF for the T-Deck.")
    p.add_argument("text", nargs="?", help="hash (or text containing one); "
                   "default: stdin, then clipboard")
    p.add_argument("--wav", help="write the audio to this WAV file instead of playing")
    p.add_argument("--once", action="store_true", help="one copy instead of two")
    p.add_argument("--rate", type=int, default=44100, help=argparse.SUPPRESS)
    a = p.parse_args(argv)

    h = extract_hash(_input_text(a.text))
    if not h:
        print("No 32-hex address found.", file=sys.stderr)
        _notify("No 32-hex address in selection")
        return 1

    samples = encode(h, a.rate, 1 if a.once else 2)
    if a.wav:
        write_wav(samples, a.rate, a.wav)
        print(h, "->", a.wav)
        return 0
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        write_wav(samples, a.rate, path)
        print("Playing", h)
        subprocess.run(["afplay", path], check=False)
    finally:
        os.unlink(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
