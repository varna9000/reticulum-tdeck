# The T-Deck v1's ES7210 microphone ADC stays usable only while its master
# clock (a PWM output) and the I2S clocks run; both stop in light sleep, and
# afterwards the ADC reads a flat constant. Bench 2026-10-10: the old re-warm
# (3 s of reads, then a clock kick, three rounds) took 12 s and failed one
# time in three; recreating the MCLK PWM, re-initialising the ADC and then
# polling for life with a kick every 1.5 s recovered it in 0.4-7 s, 4 of 4.
# These tests pin that procedure with fakes: what it touches, in what order,
# how it kicks, and that it honours an abort and a bound.
#
# Run:  python3 tests/test_mic_recovery.py

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_clock = [0]


class _Pin:
    def __init__(self, n, *a, **k):
        self.n = n


class _PWM:
    made = []

    def __init__(self, pin, freq=0, duty_u16=0):
        self.pin = pin.n
        self.freq_v = freq
        self.alive = True
        _PWM.made.append(self)

    def deinit(self):
        self.alive = False


class _I2S:
    TX = 1
    RX = 0
    MONO = 0
    STEREO = 1
    # Script of the fake ADC: it reads flat (dead) until `live_after_kicks`
    # kicks have happened, or until `live_after_reads` reads, whichever the
    # test sets.
    live_after_kicks = None
    live_after_reads = None
    reads = 0

    def __init__(self, port, **k):
        pass

    def readinto(self, buf):
        _I2S.reads += 1
        _clock[0] += 80                      # one chunk is ~80 ms of audio
        live = False
        if _I2S.live_after_kicks is not None and _ES.kicks >= _I2S.live_after_kicks:
            live = True
        if _I2S.live_after_reads is not None and _I2S.reads >= _I2S.live_after_reads:
            live = True
        for i in range(len(buf)):
            buf[i] = 0
        if live:
            buf[0] = 0x10                    # one non-zero left sample: spread > 0
            buf[1] = 0x00
        return len(buf)

    def write(self, b):
        pass

    def deinit(self):
        pass


class _ES:
    GAIN_37_5DB = 0x1E
    inits = 0
    starts = 0
    stops = 0
    kicks = 0
    _stopped = False

    def __init__(self, i2c, addr=0x40):
        pass

    def init(self, gain=0x1E):
        _ES.inits += 1

    def start(self):
        _ES.starts += 1
        if _ES._stopped:
            _ES.kicks += 1
            _ES._stopped = False

    def stop(self):
        _ES.stops += 1
        _ES._stopped = True


_machine = types.ModuleType("machine")
_machine.Pin = _Pin
_machine.I2S = _I2S
_machine.PWM = _PWM
sys.modules["machine"] = _machine
_es_mod = types.ModuleType("es7210")
_es_mod.ES7210 = _ES
sys.modules["es7210"] = _es_mod
import time as _time  # noqa: E402
_time.ticks_ms = lambda: _clock[0]
_time.ticks_diff = lambda a, b: a - b
_time.sleep_ms = lambda ms: _clock.__setitem__(0, _clock[0] + ms)
_mp = types.ModuleType("micropython")
_mp.viper = lambda f: f
_mp.native = lambda f: f
_mp.const = lambda x: x
sys.modules["micropython"] = _mp

from sound import Sound  # noqa: E402

_failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  " + str(detail)))
    if not cond:
        _failures.append(name)


def fresh(live_after_kicks=None, live_after_reads=None):
    _clock[0] = 0
    _PWM.made.clear()
    _I2S.live_after_kicks = live_after_kicks
    _I2S.live_after_reads = live_after_reads
    _I2S.reads = 0
    _ES.inits = _ES.starts = _ES.stops = _ES.kicks = 0
    _ES._stopped = False
    s = Sound()
    s.init_mic(object())
    _ES.inits = 0                             # count only what prime does
    return s


print("live ADC: the fast path")
s = fresh(live_after_reads=1)
ok = s.prime_mic()
check("returns True", ok)
check("no MCLK restart", len(_PWM.made) == 1, len(_PWM.made))
check("no ADC re-init", _ES.inits == 0, _ES.inits)
check("no kicks", _ES.kicks == 0, _ES.kicks)
check("about 0.6 s", _clock[0] < 1500, _clock[0])

print("dead ADC: MCLK restart + re-init, then kicks until live")
s = fresh(live_after_kicks=3)
ok = s.prime_mic()
check("returns True", ok)
check("MCLK PWM recreated on GPIO 48", len(_PWM.made) == 2 and not _PWM.made[0].alive
      and _PWM.made[1].pin == 48 and _PWM.made[1].freq_v == 4096000, [(p.pin, p.alive) for p in _PWM.made])
check("ADC re-initialised once", _ES.inits == 1, _ES.inits)
check("kicked three times", _ES.kicks == 3, _ES.kicks)
check("kicks about 1.5 s apart, so ~6 s total", 5000 < _clock[0] < 8000, _clock[0])

print("dead ADC that revives on its own after the re-init")
s = fresh(live_after_reads=20)                # live a few reads after the flush
ok = s.prime_mic()
check("returns True without kicking", ok and _ES.kicks == 0, _ES.kicks)

print("abort while recovering")
s = fresh()                                   # never comes back
calls = [0]
def abort():
    calls[0] += 1
    return _clock[0] > 3000
ok = s.prime_mic(abort=abort)
check("returns False once abort says so", ok is False)
check("gave up promptly after the abort", _clock[0] < 4500, _clock[0])

print("never live: bounded, re-initialising each round")
s = fresh()
ok = s.prime_mic()
check("returns False", ok is False)
check("gave up within the bound", _clock[0] <= Sound.MIC_RECOVER_MS + 2000, _clock[0])
check("re-initialised at the start of every round", _ES.inits == Sound.MIC_RECOVER_MS // Sound.MIC_ROUND_MS, _ES.inits)

if _failures:
    print("\n%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
    sys.exit(1)
print("\nall ok")
