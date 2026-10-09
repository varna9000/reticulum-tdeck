# The I2S pins are the board's, not the driver's. The v1's MAX98357A is on
# 7/5/6; on the T-Deck Pro those GPIOs are the LoRa BUSY and INT lines, and
# its PCM5102A sits on 7/9/8 (LilyGO's BOARD_I2S_BCLK/LRC/DOUT). So
# Sound.init() takes the pins, defaulting to the v1's, and tdeck_node passes
# board.SPK_PINS.
#
# Run:  python3 tests/test_sound_pins.py

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

made = []
ports = []


class _Pin:
    def __init__(self, n, *a, **k):
        self.n = n


class _I2S:
    TX = 1
    RX = 0
    MONO = 0
    STEREO = 1

    def __init__(self, port, sck=None, ws=None, sd=None, **k):
        made.append((sck.n, ws.n, sd.n))
        ports.append(port)

    def write(self, b):
        pass


_machine = types.ModuleType("machine")
_machine.Pin = _Pin
_machine.I2S = _I2S
_machine.PWM = object
sys.modules["machine"] = _machine
import time as _time
_time.sleep_ms = lambda ms: None
_mp = types.ModuleType("micropython")
_mp.viper = lambda f: f
_mp.native = lambda f: f
_mp.const = lambda x: x
sys.modules["micropython"] = _mp

from sound import Sound  # noqa: E402

_failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  " + detail))
    if not cond:
        _failures.append(name)


def test_init_defaults_to_the_v1_pins():
    del made[:]
    Sound().init()
    check("bare init() is the v1's MAX98357A wiring", made == [(7, 5, 6)], str(made))


def test_init_takes_the_boards_pins():
    del made[:]
    Sound().init(7, 9, 8)
    check("init(bck, ws, dout) drives the given pins", made == [(7, 9, 8)], str(made))


def test_pro_board_pins_are_lilygos_and_opt_in():
    """board_tdeck_pro reads HAS_AUDIO from the config and only then names
    the pins, LilyGO's 7/9/8 -- never Meshtastic's 47 (LoRa MISO)."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "board_tdeck_pro.py")).read()
    check("the Pro decides audio from its config file (or the saved first-boot answer)",
          'getattr(config, "HAS_AUDIO", "auto")' in src)
    check("the Pro names LilyGO's I2S pins",
          '"I2S_BCLK", 7' in src and '"I2S_LRCK", 9' in src and '"I2S_DOUT", 8' in src)
    check("GPIO 47 is not an audio pin", "47" not in src.split("SPK_PINS = ((")[1].split("\n")[0])
    check("the module slot's power enable is raised for the DAC",
          'if HAS_AUDIO:' in src and '"AUDIO_EN", 41' in src)


def _peak(buf):
    import struct
    return max(abs(struct.unpack_from("<h", buf, i * 2)[0]) for i in range(len(buf) // 2))


def _crossings(buf):
    import struct
    vals = [struct.unpack_from("<h", buf, i * 2)[0] for i in range(len(buf) // 2)]
    return sum(1 for a, b in zip(vals, vals[1:]) if (a < 0) != (b < 0))


def test_board_gain_and_pitch_shape_the_tones():
    """The Pro's speaker is near-silent at the v1's chirps; the board hands
    the driver a gain and a pitch, and the tones follow both."""
    v1 = Sound(); v1.init(7, 5, 6)
    pro = Sound(); pro.init(7, 9, 8, gain=8, pitch=2)
    check("the v1's tones are as they were", 2000 <= _peak(v1._ann_buf) <= 2600,
          str(_peak(v1._ann_buf)))
    check("the Pro's tones are louder, capped at full scale",
          19000 <= _peak(pro._ann_buf) <= 30000, str(_peak(pro._ann_buf)))
    c1, c2 = _crossings(v1._ann_buf), _crossings(pro._ann_buf)
    check("the Pro's tones are an octave up", 1.8 <= c2 / float(c1) <= 2.2, "%d vs %d" % (c1, c2))
    check("the default is unchanged", v1.gain == 1 and v1.pitch == 1)


def test_pcm_playback_gain_saturates():
    """Voice playback is scaled by the same gain; past full scale it must clip,
    not wrap round to the opposite sign."""
    import struct
    import sound as S
    src = bytearray(8)
    struct.pack_into("<hhhh", src, 0, 1000, -1000, 30000, -30000)
    dst = bytearray(8)
    S._scale_pcm(dst, src, 4, 8 * 256)          # x8
    out = struct.unpack("<hhhh", dst)
    check("small samples scale by the gain", out[0] == 8000 and out[1] == -8000, str(out))
    check("large samples saturate at full scale", out[2] == 32767 and out[3] == -32768, str(out))
    S._scale_pcm(dst, src, 4, 128)              # half, as the volume slider does
    check("attenuation still works", struct.unpack("<hhhh", dst)[0] == 500)


def test_playback_port_comes_from_the_board():
    del ports[:]
    Sound().init(7, 5, 6)
    Sound().init(7, 9, 8, port=1)
    check("the v1 plays on port 0, the Pro on port 1", ports == [0, 1], str(ports))


def test_pdm_mic_path_decimates_to_8k():
    """The Pro's PDM mic delivers 16 kHz mono; the recorder wants 8 kHz
    mono chunks of out_samples. A fake pdm_mic module feeds a 1 kHz tone
    with a DC offset; the chunk must come back at half the sample count,
    centred, with the tone intact."""
    import math
    import struct
    fake = types.ModuleType("pdm_mic")
    fake.calls = []
    phase = [0]

    def init(clk, data, rate=16000):
        fake.calls.append(("init", clk, data, rate))

    def readinto(buf):
        n = len(buf) // 2
        for i in range(n):
            v = 3000 + int(6000 * math.sin(2 * math.pi * 1000 * (phase[0] + i) / 16000))
            struct.pack_into("<h", buf, i * 2, v)
        phase[0] += n
        return n * 2
    fake.init = init
    fake.readinto = readinto
    sys.modules["pdm_mic"] = fake
    try:
        s = Sound()
        s.init_pdm_mic(18, 17)
        check("the mic is opened on the given pins at 16 kHz",
              fake.calls == [("init", 18, 17, 16000)], str(fake.calls))
        check("priming a PDM mic needs no warm-up", s.prime_mic() is True)
        s.start_recording(640)
        out = None
        for _ in range(6):                       # let the DC estimate settle
            out = s.read_mic_chunk(640)
        check("a chunk is out_samples of 16-bit PCM", out is not None and len(out) == 1280,
              str(None if out is None else len(out)))
        vals = [struct.unpack_from("<h", out, i * 2)[0] for i in range(640)]
        mean = sum(vals) / float(len(vals))
        check("the DC offset is removed", abs(mean) < 400, "mean %.0f" % mean)
        crossings = sum(1 for a, b in zip(vals, vals[1:]) if (a < 0) != (b < 0))
        # 1 kHz over 80 ms = 80 cycles = ~160 crossings
        check("the tone survives at 8 kHz", 140 <= crossings <= 180, str(crossings))
        check("the peak is reported for a VU meter", 4000 <= s._mic_peak <= 6200, str(s._mic_peak))
        s.stop_recording()
    finally:
        del sys.modules["pdm_mic"]


if __name__ == "__main__":
    test_init_defaults_to_the_v1_pins()
    test_playback_port_comes_from_the_board()
    test_pdm_mic_path_decimates_to_8k()
    test_init_takes_the_boards_pins()
    test_pro_board_pins_are_lilygos_and_opt_in()
    test_board_gain_and_pitch_shape_the_tones()
    test_pcm_playback_gain_saturates()
    print()
    if _failures:
        print("%d FAILED" % len(_failures))
        sys.exit(1)
    print("all passed")
