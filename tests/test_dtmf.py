# Host-side test for DTMF address transfer: tools/dtmf_send.py (what the Mac
# plays) through dtmf.Decoder (what the T-Deck runs on its 8 kHz mic stream).
# The decoder here is the plain-Python Goertzel twin of the device's viper
# one -- same integer arithmetic, so a pass here is a pass there.
#
# Run:  python3 tests/test_dtmf.py

import binascii
import math
import os
import random
import struct
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "tools"))

import dtmf
import dtmf_send

H = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


def pcm(samples):
    """Float/int samples -> 16-bit LE PCM bytes, clipped."""
    return b"".join(struct.pack("<h", max(-32768, min(32767, int(s)))) for s in samples)


def run(samples, chunk=640):
    """Feed samples in mic-sized chunks; return the first hash decoded."""
    d = dtmf.Decoder()
    buf = pcm(samples)
    step = chunk * 2
    for i in range(0, len(buf), step):
        part = buf[i:i + step]
        got = d.feed(part, len(part) // 2)
        if got:
            return got
    return None


def resample(samples, src, dst):
    """Linear-interpolation resample, e.g. the Mac's 44.1 kHz to the mic's 8 kHz."""
    n = int(len(samples) * dst / src)
    out = []
    for i in range(n):
        t = i * src / dst
        j = int(t)
        f = t - j
        a = samples[j]
        b = samples[j + 1] if j + 1 < len(samples) else a
        out.append(a + (b - a) * f)
    return out


def noise(n, rms, seed=1):
    r = random.Random(seed)
    return [r.gauss(0, rms) for _ in range(n)]


def mix(*tracks):
    n = max(len(t) for t in tracks)
    return [sum(t[i] for t in tracks if i < len(t)) for i in range(n)]


def echo(samples, delay_ms, gain, rate=8000):
    d = delay_ms * rate // 1000
    return [s + (samples[i - d] * gain if i >= d else 0) for i, s in enumerate(samples)]


def scale(samples, k):
    return [s * k for s in samples]


# --- wire format ------------------------------------------------------------

def test_crc16_matches_binascii():
    r = random.Random(7)
    for _ in range(200):
        data = bytes(r.getrandbits(8) for _ in range(16))
        assert dtmf.crc16(data) == binascii.crc_hqx(data, 0xFFFF)


def test_frame_is_hash_plus_crc():
    f = dtmf_send.frame(H)
    assert len(f) == 36
    assert f[:32] == H
    assert int(f[32:], 16) == binascii.crc_hqx(bytes.fromhex(H), 0xFFFF)


def test_extract_hash():
    x = dtmf_send.extract_hash
    assert x(H) == H
    assert x(H.upper()) == H
    assert x("  <" + H + ">\n") == H
    assert x("peer " + H + " (Alice)") == H
    assert x("deadbeef") is None
    assert x(H + "0") is None          # 33 hex chars is not a hash
    assert x("") is None


def test_keys_for_other_generators():
    # what to type into any DTMF generator: 36 keys, hex a-d/e/f as A-D/*/#
    k = dtmf_send.keys(H)
    assert len(k) == 36
    assert k[:32] == H.upper().replace("E", "*").replace("F", "#")
    assert set(k) <= set("0123456789ABCD*#")
    assert dtmf_send.main([H, "--digits"]) == 0


def test_player_per_platform():
    which = lambda have: (lambda cmd: "/usr/bin/" + cmd if cmd in have else None)
    p = dtmf_send.player_cmd
    assert p("darwin", which({"afplay"}))[0] == "afplay"
    assert p("linux", which({"pw-play", "paplay", "aplay"}))[0] == "pw-play"
    assert p("linux", which({"paplay", "aplay"}))[0] == "paplay"
    assert p("linux", which({"aplay"}))[0] == "aplay"
    assert p("linux", which({"ffplay"}))[0] == "ffplay"
    assert p("linux", which(set())) is None


def test_clipboard_per_platform():
    which = lambda have: (lambda cmd: "/usr/bin/" + cmd if cmd in have else None)
    c = dtmf_send.paste_cmd
    assert c("darwin", which({"pbpaste"}), False) == ["pbpaste"]
    assert c("linux", which({"wl-paste", "xclip"}), False)[0] == "wl-paste"
    assert c("linux", which({"xclip"}), False) == ["xclip", "-o", "-selection", "clipboard"]
    assert c("linux", which({"xclip"}), True) == ["xclip", "-o", "-selection", "primary"]
    assert c("linux", which({"wl-paste"}), True) == ["wl-paste", "--no-newline", "--primary"]
    assert c("linux", which({"xsel"}), True) == ["xsel", "-o", "-p"]
    assert c("linux", which(set()), False) is None


# --- decoding ---------------------------------------------------------------

def test_clean_8k():
    assert run(dtmf_send.encode(H, rate=8000)) == H


def test_from_44k1():
    s = dtmf_send.encode(H, rate=44100)
    assert run(resample(s, 44100, 8000)) == H


def test_every_digit_and_repeats():
    for h in ("0123456789abcdef0123456789abcdef",
              "fedcba9876543210fedcba9876543210",
              "00000000000000000000000000000000",
              "aaaaaaaaaaaaaaaabbbbbbbbbbbbbbbb",
              "ffffffffffffffffffffffffffffffff"):
        assert run(dtmf_send.encode(h, rate=8000)) == h, h


def test_quiet_and_loud():
    s = dtmf_send.encode(H, rate=8000)
    assert run(scale(s, 0.02)) == H     # ~-40 dBFS
    assert run(scale(s, 1.9)) == H      # near full scale


def test_noise():
    s = dtmf_send.encode(H, rate=8000)
    # tone peak is 0.5 FS = 16384; rms 2000 noise is ~12 dB SNR per tone
    assert run(mix(s, noise(len(s), 2000))) == H


def test_echo():
    s = dtmf_send.encode(H, rate=8000)
    assert run(echo(s, 15, 0.5)) == H
    assert run(echo(echo(s, 23, 0.4), 37, 0.3)) == H


def test_rate_error_2pct():
    # rendered at 8160/7840 Hz but consumed as 8000: every tone 2% off
    # frequency and every duration 2% off
    for r in (8160, 7840):
        assert run(dtmf_send.encode(H, rate=r)) == H, r


def test_start_mid_first_copy():
    s = dtmf_send.encode(H, rate=8000)
    for cut in (0.12, 0.3, 0.45):
        assert run(s[int(len(s) * cut):]) == H, cut


def test_single_copy():
    assert run(dtmf_send.encode(H, rate=8000, copies=1)) == H


def test_corrupt_digit_rejected():
    # same wrong digit in both copies -> CRC never matches
    bad = "b" + H[1:]
    f = dtmf_send.frame(H)
    fake = bad + f[32:]
    s = dtmf_send.render(fake, rate=8000)
    assert run(s) is None


def _synth_pair(lo, hi, ms, rate, a_lo, a_hi):
    n = ms * rate // 1000
    return [a_lo * math.sin(2 * math.pi * lo * i / rate) +
            a_hi * math.sin(2 * math.pi * hi * i / rate) for i in range(n)]


def test_twist():
    # one tone of each pair 6 dB down (speaker/mic response tilt)
    f = dtmf_send.frame(H)
    for a_lo, a_hi in ((4000, 8000), (8000, 4000)):
        out = [0.0] * 2400
        for ch in f:
            lo, hi = dtmf_send.TONES[ch]
            out += _synth_pair(lo, hi, 80, 8000, a_lo, a_hi) + [0.0] * 320
        assert run(out) == H, (a_lo, a_hi)


def test_high_tone_is_louder():
    # standard forward twist: the high tone +3 dB over the low one, so the
    # T-Deck's weaker 1.2-1.7 kHz response doesn't bury it
    assert abs(dtmf_send.HIGH_LEVEL / dtmf_send.LOW_LEVEL - 10 ** (3 / 20)) < 0.01
    assert dtmf_send.HIGH_LEVEL + dtmf_send.LOW_LEVEL <= 0.75     # peak <= -2.5 dBFS


def test_low_tone_harmonic():
    # speaker/case distortion: the low tone's 2nd harmonic (2x770 = 1540 Hz
    # sits between 1477 and 1633) at -12 dB, seen on hardware
    f = dtmf_send.frame(H)
    out = [0.0] * 2400
    for ch in f:
        lo, hi = dtmf_send.TONES[ch]
        n = 640
        out += [8000 * math.sin(2 * math.pi * lo * i / 8000) +
                2000 * math.sin(2 * math.pi * 2 * lo * i / 8000) +
                8000 * math.sin(2 * math.pi * hi * i / 8000) for i in range(n)]
        out += [0.0] * 320
    assert run(out) == H


def test_no_false_hits():
    rate = 8000
    r = random.Random(3)
    n = 20 * rate
    # white noise, loud and quiet
    assert run(noise(n, 6000, seed=5)) is None
    assert run(noise(n, 50, seed=6)) is None
    # speech-like: gliding harmonic series with syllable envelope
    sp = []
    f0 = 140.0
    for i in range(n):
        if i % 1600 == 0:
            f0 = r.uniform(90, 260)
        env = 0.5 + 0.5 * math.sin(2 * math.pi * 4 * i / rate)
        sp.append(env * sum(3000 / k * math.sin(2 * math.pi * f0 * k * i / rate)
                            for k in range(1, 8)))
    assert run(sp) is None
    # music-like: random two/three-note chords, including DTMF frequencies
    notes = list(dtmf_send.LOW) + list(dtmf_send.HIGH) + [440, 523, 659, 880, 1046]
    mu = []
    chord = []
    for i in range(n):
        if i % 960 == 0:
            chord = r.sample(notes, r.choice((2, 3)))
        mu.append(sum(5000 * math.sin(2 * math.pi * f * i / rate) for f in chord))
    assert run(mu) is None


def test_digits_heard_counts():
    d = dtmf.Decoder()
    s = pcm(dtmf_send.encode(H, rate=8000, copies=1)[:8000])   # first second
    d.feed(s, len(s) // 2)
    assert 3 <= d.digits_heard <= 8


def test_reset():
    d = dtmf.Decoder()
    s = pcm(dtmf_send.encode(H, rate=8000, copies=1))
    half = len(s) // 2 & ~1
    d.feed(s[:half], half // 2)
    d.reset()
    assert d.digits_heard == 0
    rest = s[half:]
    assert d.feed(rest, len(rest) // 2) is None


def test_odd_chunk_sizes():
    s = dtmf_send.encode(H, rate=8000)
    assert run(s, chunk=333) == H
    assert run(s, chunk=160) == H


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok  ", name)
            except AssertionError as e:
                fails += 1
                print("FAIL", name, e)
    sys.exit(1 if fails else 0)
