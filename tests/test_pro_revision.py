# The T-Deck Pro's revisions differ in the panel reset line and the 1.8 V
# gate (issue #1: a V1.1 boots to a blank panel on the V1.0 map). LilyGO
# documents the tell: a DRV2605 at 0x5A is V1.1, an XL9555 at 0x20 is MAX.
# tdeck_pro_rev resolves the config's BOARD_REV ("auto" or an override) to a
# revision and its pins; board_tdeck_pro.py runs it before power-up.
#
# Run:  python3 tests/test_pro_revision.py

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import tdeck_pro_rev as R  # noqa: E402

_failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  " + detail))
    if not cond:
        _failures.append(name)


V1_1_BUS = [0x1A, 0x28, 0x34, 0x55, 0x5A, 0x6B]      # scanned on a V1.1, bare


def test_detect_from_the_bus():
    check("a bare V1.0 bus (no DRV2605) is 1.0", R.detect([0x34, 0x55, 0x6B]) == "1.0")
    check("the DRV2605 at 0x5A marks a 1.1", R.detect(V1_1_BUS) == "1.1")
    check("the XL9555 at 0x20 marks a MAX", R.detect(V1_1_BUS + [0x20]) == "max")
    check("an empty scan is 1.0", R.detect([]) == "1.0")


def test_resolve_honours_the_override():
    check("auto detects", R.resolve("auto", V1_1_BUS) == "1.1")
    check("an override wins over the bus", R.resolve("1.0", V1_1_BUS) == "1.0")
    check("V1.1 spelling is accepted", R.resolve("V1.1", []) == "1.1")
    try:
        R.resolve("2.0", [])
        check("a typo is refused", False)
    except ValueError:
        check("a typo is refused", True)


def test_pins_per_revision():
    check("1.0 raises the 1.8 V gate on 38 and has no reset line", R.pins("1.0") == (38, -1))
    check("1.1 drives the panel reset on 16 and has no gate", R.pins("1.1") == (None, 16))
    check("MAX is treated like 1.1", R.pins("max") == (None, 16))


def test_config_defaults_to_auto():
    src = open(os.path.join(ROOT, "tdeck_pro_config.py")).read()
    check("BOARD_REV defaults to auto", 'BOARD_REV = "auto"' in src)
    check("HAS_AUDIO defaults to auto", 'HAS_AUDIO = "auto"' in src)
    check("the config no longer derives the pins itself",
          "DISP_RES" not in src and "BOARD_1V8_EN" not in src.replace("# ", ""))


def test_board_file_resolves_before_power_up():
    src = open(os.path.join(ROOT, "board_tdeck_pro.py")).read()
    i_scan = src.index("tdeck_pro_rev.resolve(")
    i_power = src.index("power_up()\n", src.index("def power_up"))
    check("the revision is resolved before power_up() runs", i_scan < i_power)
    check("the gate is skipped when None", "if BOARD_1V8_EN is not None" in src)
    check("the reset line reaches the panel driver", "rst=_disp_rst" in src)
    check("no reset pin is made for -1", "if DISP_RES >= 0 else None" in src)


def test_profile_round_trip():
    import tempfile
    path = os.path.join(tempfile.mkdtemp(), "hw.json")
    check("no file means no profile", R.load_profile(path) is None)
    R.save_profile("1.1", True, path)
    check("a saved profile reads back", R.load_profile(path) == {"rev": "1.1", "audio": True})
    R.save_profile("1.0", None, path)
    check("an unprobed audio answer is kept as None", R.load_profile(path) == {"rev": "1.0", "audio": None})
    with open(path, "w") as f:
        f.write("{\"rev\": \"9.9\", \"audio\": true}")
    check("a corrupt profile is ignored", R.load_profile(path) is None)


def test_audio_setting_values():
    check("True/False pass through", R.audio_setting(True) is True and R.audio_setting(False) is False)
    check("auto is auto", R.audio_setting("auto") == "auto" and R.audio_setting("AUTO") == "auto")
    try:
        R.audio_setting("maybe")
        check("a typo is refused", False)
    except ValueError:
        check("a typo is refused", True)


class _Clock:
    def __init__(self):
        self.t = 0

    def ticks_ms(self):
        return self.t

    def sleep_ms(self, ms):
        self.t += ms

    def ticks_diff(self, a, b):
        return a - b


class _Pin:
    def __init__(self):
        self.values = []

    def value(self, v):
        self.values.append(v)


class _Uart:
    def __init__(self, answer_after_ms, clock):
        self.answer_after_ms = answer_after_ms
        self.clock = clock
        self.sent = b""
        self.buf = b""

    def write(self, b):
        self.sent += b
        if self.answer_after_ms is not None and self.clock.t >= self.answer_after_ms:
            self.buf += b"\r\nOK\r\n"

    def any(self):
        return len(self.buf)

    def read(self, n):
        out, self.buf = self.buf[:n], self.buf[n:]
        return out


def test_modem_probe():
    clk = _Clock()
    pk = _Pin()
    u = _Uart(6000, clk)                  # a modem that boots in 6 s
    check("a modem that answers AT is found",
          R.probe_modem(u, pk, clk.sleep_ms, clk.ticks_ms, clk.ticks_diff) is True)
    check("the power key was pulsed low then released", pk.values == [0, 1], str(pk.values))
    check("AT was sent", b"AT\r\n" in u.sent)
    clk = _Clock()
    u = _Uart(None, clk)                  # a DAC unit: nothing on the pins
    check("silence within the timeout means no modem",
          R.probe_modem(u, _Pin(), clk.sleep_ms, clk.ticks_ms, clk.ticks_diff, timeout_ms=4000) is False)
    check("and the probe gave up at the timeout", clk.t <= 1500 + 4000 + 500, str(clk.t))


def test_board_file_probes_once_and_saves():
    src = open(os.path.join(ROOT, "board_tdeck_pro.py")).read()
    check("a saved profile answers the revision", '_profile["rev"]' in src)
    check("a saved profile answers the audio question", '_profile["audio"]' in src)
    check("the probe runs only when nothing is saved and the config says auto",
          "if HAS_AUDIO is None:\n    HAS_AUDIO = _first_boot_audio_probe()" in src)
    check("the probe waits for the panel", src.index("def _first_boot_audio_probe") > src.index("tft = eink_shim.EinkShim(panel)"))
    check("the answer is saved", "tdeck_pro_rev.save_profile(" in src)
    check("a found modem is powered back down", "slot_en.value(0)" in src)
    check("the notice names the detected revision and is centred",
          '"T-Deck Pro V" + BOARD_REV' in src and "(tft.width - len(line) * _font.WIDTH) // 2" in src)
    check("the answer is shown before the app starts", "Audio module found" in src)


if __name__ == "__main__":
    test_profile_round_trip()
    test_audio_setting_values()
    test_modem_probe()
    test_board_file_probes_once_and_saves()
    test_detect_from_the_bus()
    test_resolve_honours_the_override()
    test_pins_per_revision()
    test_config_defaults_to_auto()
    test_board_file_resolves_before_power_up()
    print()
    if _failures:
        print("%d FAILED" % len(_failures))
        sys.exit(1)
    print("all passed")
