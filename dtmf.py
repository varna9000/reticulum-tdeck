# DTMF receiver for destination hashes played by tools/dtmf_send.py.
#
# Wire format: 32 hash hex digits + 4-digit CRC-16/CCITT-FALSE of the 16 hash
# bytes, one DTMF tone per hex digit (a-d -> A-D, e -> *, f -> #), 80 ms on /
# 40 ms off, sent twice. No start/end markers: the decoder keeps the last 36
# digits it heard and reports a hash the moment their CRC checks out, so it can
# join mid-way through the first copy and complete on the second.
#
# Input is the mic path's 8 kHz mono signed 16-bit PCM (sound.read_mic_chunk),
# cut into 20 ms blocks. Each block runs 8 Goertzel filters (viper, integer
# fixed-point on the device; an identical plain-Python twin on the host) and
# is classified as one digit or none. A tone's first block opens a 3-block
# vote (2 votes to win); the next digit may start no sooner than the format's
# 120 ms digit period allows, and only after a block that isn't a strong copy
# of the last one -- which is what separates repeated digits across the 40 ms
# gap, even with room echo still ringing in it.

import array
import math

RATE = 8000
BLOCK = 160               # 20 ms at 8 kHz
FRAME = 36                # 32 hash digits + 4 CRC digits

_LOW = (697, 770, 852, 941)
_HIGH = (1209, 1336, 1477, 1633)
_KEYS = ("123A", "456B", "789C", "*0#D")
_HEX2KEY = "0123456789ABCD*#"

# (low index * 4 + high index) -> hex digit
_PAIR = [""] * 16
for _r in range(4):
    for _c in range(4):
        _PAIR[_r * 4 + _c] = "0123456789abcdef"[_HEX2KEY.index(_KEYS[_r][_c])]

# Goertzel coefficients 2*cos(w) in Q12. Q12 and a <=10-bit input (see the
# adaptive shift) keep coeff*s1 under 2^31 at the lowest (most resonant) tone.
_COEFFS = array.array("i", [int(round(2 * math.cos(2 * math.pi * f / RATE) * 4096))
                            for f in _LOW + _HIGH])

# Tuning. Ratios are power ratios.
_MIN_RMS = 20             # raw mic units; below this a block is silence
_PAIR_FRACTION = 0.15     # the two tones must hold this share of block energy
_RUNNER_UP = 2.0          # winner >= 2x (3 dB) the next tone in its group
_TWIST = 40.0             # low/high tone within +-16 dB (speaker tilt, room nulls)
_STRONG = 0.1             # a repeat of the held digit re-arms below -10 dB
_VOTE_BLOCKS = 3          # blocks voted on from a tone's first one (60 of its 80 ms)
_VOTE_MIN = 2             # votes the winner needs
_MIN_SPACING = 5          # blocks between digit starts; the format sends one per 6
                          # (120 ms), so one sooner is a mid-tone fade, not a digit
_REF_HOLD = 25            # blocks (0.5 s) after a digit before its level is forgotten


def crc16(data):
    """CRC-16/CCITT-FALSE, == binascii.crc_hqx(data, 0xFFFF) on CPython."""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return crc


def _goertzel_py(buf, off, n, out):
    """Plain-Python twin of _goertzel_v (host tests). Same integer maths."""
    xs = [int.from_bytes(buf[off + 2 * i:off + 2 * i + 2], "little", signed=True)
          for i in range(n)]
    peak = 0
    for x in xs:
        if x < 0:
            x = -x
        if x > peak:
            peak = x
    sh = 0
    while (peak >> sh) > 1023:
        sh += 1
    xs = [x >> sh for x in xs]
    e = 0
    for x in xs:
        e += x * x
    for k in range(8):
        ck = _COEFFS[k]
        s1 = 0
        s2 = 0
        for x in xs:
            s0 = x + ((ck * s1) >> 12) - s2
            s2 = s1
            s1 = s0
        out[2 * k] = s1
        out[2 * k + 1] = s2
    out[16] = e
    return sh


_goertzel = _goertzel_py
try:
    import micropython

    @micropython.viper
    def _goertzel_v(buf, off: int, n: int, out) -> int:
        """8 Goertzel filters over n samples of 16-bit LE PCM at byte offset
        off. Input is shifted right until its peak fits 10 bits (returned),
        so the Q12 recurrence can't overflow. (Viper takes at most 4
        arguments, hence the global coefficient table.) out[2k], out[2k+1] = s1, s2 of
        filter k; out[16] = sum of squared (shifted) samples."""
        b = ptr16(buf)
        c = ptr32(_COEFFS)
        o = ptr32(out)
        base = off >> 1
        peak = 0
        for i in range(n):
            x = int(b[base + i])
            if x >= 0x8000:
                x -= 0x10000
            if x < 0:
                x = 0 - x
            if x > peak:
                peak = x
        sh = 0
        while (peak >> sh) > 1023:
            sh += 1
        e = 0
        for i in range(n):
            x = int(b[base + i])
            if x >= 0x8000:
                x -= 0x10000
            x = x >> sh
            e += x * x
        for k in range(8):
            ck = c[k]
            s1 = 0
            s2 = 0
            for i in range(n):
                x = int(b[base + i])
                if x >= 0x8000:
                    x -= 0x10000
                s0 = (x >> sh) + ((ck * s1) >> 12) - s2
                s2 = s1
                s1 = s0
            o[2 * k] = s1
            o[2 * k + 1] = s2
        o[16] = e
        return sh

    _goertzel = _goertzel_v
except ImportError:
    pass


class Decoder:
    """Feed 8 kHz PCM; get back a 32-hex hash string once one checks out."""

    def __init__(self):
        self._out = array.array("i", [0] * 17)
        self._carry = bytearray(BLOCK * 2)
        self.reset()

    def reset(self):
        self._n_carry = 0
        self._win = ""
        self._vote = None     # digit -> (blocks, summed power) while a tone is voted on
        self._vn = 0          # blocks in the current vote
        self._armed = True    # may start a vote (a gap has been seen since the last digit)
        self._held = None     # last emitted digit
        self._ref = 0.0       # its tone power
        self._since = 99      # blocks since the last vote started
        self.digits_heard = 0

    def feed(self, pcm, n):
        """pcm: n samples of signed 16-bit LE. Returns the hash or None."""
        off = 0
        end = n * 2
        got = None
        if self._n_carry:
            take = min(end, BLOCK * 2 - self._n_carry)
            self._carry[self._n_carry:self._n_carry + take] = pcm[:take]
            self._n_carry += take
            off = take
            if self._n_carry == BLOCK * 2:
                self._n_carry = 0
                got = self._block(self._carry, 0)
        while not got and end - off >= BLOCK * 2:
            got = self._block(pcm, off)
            off += BLOCK * 2
        if got:
            self.reset()
            return got
        rest = end - off
        if rest:
            self._carry[:rest] = pcm[off:end]
            self._n_carry = rest
        return None

    def _classify(self, buf, off):
        """(digit, tone power) for one block; digit None when there is none."""
        o = self._out
        sh = _goertzel(buf, off, BLOCK, o)
        e = o[16]
        scale = float(1 << (2 * sh))
        if e * scale < _MIN_RMS * _MIN_RMS * BLOCK:
            return None, 0.0
        p = []
        for k in range(8):
            s1 = float(o[2 * k])
            s2 = float(o[2 * k + 1])
            p.append(s1 * s1 + s2 * s2 - _COEFFS[k] * s1 * s2 / 4096.0)
        li = hi = 0
        for k in range(1, 4):
            if p[k] > p[li]:
                li = k
            if p[4 + k] > p[4 + hi]:
                hi = k
        pl = p[li]
        ph = p[4 + hi]
        for k in range(4):
            if k != li and p[k] * _RUNNER_UP > pl:
                return None, 0.0
            if k != hi and p[4 + k] * _RUNNER_UP > ph:
                return None, 0.0
        if pl > ph * _TWIST or ph > pl * _TWIST:
            return None, 0.0
        # a pure pair gives (pl + ph) == e * BLOCK / 2
        if pl + ph < _PAIR_FRACTION * e * BLOCK / 2:
            return None, 0.0
        return _PAIR[li * 4 + hi], (pl + ph) * scale

    def _block(self, buf, off):
        d, p = self._classify(buf, off)
        self._since += 1
        if self._since > _REF_HOLD:
            self._ref = 0.0
        strong = d is not None and p >= self._ref * _STRONG
        if self._vote is not None:
            # a tone started: let the next blocks vote, so one faded or
            # smeared block doesn't cost the digit
            self._vn += 1
            if strong:
                c = self._vote.get(d)
                self._vote[d] = (c[0] + 1, c[1] + p) if c else (1, p)
            if self._vn < _VOTE_BLOCKS:
                return None
            best = None
            for k, v in self._vote.items():
                if best is None or v > self._vote[best]:
                    best = k
            n, pw = self._vote[best]
            self._vote = None
            if n < _VOTE_MIN:
                self._since = _MIN_SPACING    # not a digit; free to try again
                return None
            return self._emit(best, pw / n)
        if not self._armed:
            if strong and d == self._held:
                if p > self._ref:
                    self._ref = p
                return None
            self._armed = True
        if strong and self._since >= _MIN_SPACING:
            self._vote = {d: (1, p)}
            self._vn = 1
            self._since = 0
        return None

    def _emit(self, d, p):
        self._armed = False
        self._held = d
        self._ref = p
        self.digits_heard += 1
        w = self._win + d
        self._win = w = w[-FRAME:]
        if len(w) == FRAME and crc16(bytes.fromhex(w[:32])) == int(w[32:], 16):
            return w[:32]
        return None
