# T-Deck Pro board revisions: which one this is, and what differs.
#
# LilyGO ships three revisions that the firmware can tell apart over I2C
# ("Different versions can be distinguished by using the I2C function"):
#
#     revision   DRV2605 at 0x5A   XL9555 at 0x20
#     V1.0       no                no
#     V1.1       yes               no
#     MAX        yes               yes
#
# The DRV2605 haptic driver answers with nothing else powered, so the scan can
# run before the board's power gates are raised -- which matters, because one
# of the things that differs IS a power gate. The pins that differ (from
# Meshtastic's t-deck-pro and t-deck-pro-v1_1 variants and LilyGO's V1.1
# pinmap):
#
#     V1.0: no panel reset line (reset over a command); a 1.8 V rail behind
#           GPIO 38 must be raised before the panel or radio answer.
#     V1.1: panel reset on GPIO 16; no 1.8 V gate (38 is the touch reset).
#     MAX:  treated like V1.1 for these two pins. Untested; it also carries an
#           XL9555 IO expander this port does not drive.
#
# The 4G/audio question is harder. The Pro ships either with a PCM5102A DAC
# or with an A7682E modem, on the same GPIO 7/8/9, and nothing passive tells
# them apart: LilyGO's own factory firmware powers the module, pulses the
# modem's power key and sends AT until it answers, and treats silence as
# "audio". That costs seconds and a modem power cycle, so it runs ONCE: the
# answer (with the revision) is saved to PROFILE_PATH on the filesystem and
# read back on every later boot. Setting BOARD_REV / HAS_AUDIO in the config
# to anything but "auto" overrides the saved value; deleting the file makes
# the next boot detect again.
#
# Pure functions, so the host tests can exercise them without hardware.

DRV2605_ADDR = 0x5A
XL9555_ADDR = 0x20

REVISIONS = ("1.0", "1.1", "max")


def detect(addrs):
    """Revision from an I2C scan (a list of 7-bit addresses)."""
    if XL9555_ADDR in addrs:
        return "max"
    if DRV2605_ADDR in addrs:
        return "1.1"
    return "1.0"


def resolve(setting, addrs):
    """The revision to run as: the config's BOARD_REV, or detect() for "auto".

    Anything else is a config typo and is refused here rather than silently
    treated as one revision -- a wrong map boots to a blank panel.
    """
    s = str(setting).strip().lower().lstrip("v")
    if s == "auto":
        return detect(addrs)
    if s in REVISIONS:
        return s
    raise ValueError("BOARD_REV must be \"auto\", \"1.0\", \"1.1\" or \"max\", not %r" % (setting,))


def pins(rev):
    """The revision-dependent pins: (board_1v8_en, disp_res)."""
    if rev == "1.0":
        return 38, -1
    return None, 16


def touch_reset(rev):
    """The CST328 reset line. V1.0 wires it to GPIO 45; V1.1 and MAX moved
    it to GPIO 38, which on a V1.0 is the 1.8 V gate and must never be
    driven low (tdeck_pro_config.TOUCH_RST carries the V1.0 number)."""
    if rev == "1.0":
        return 45
    return 38


PROFILE_PATH = "/tdeck_pro_hw.json"


def load_profile(path=PROFILE_PATH):
    """The saved first-boot detection, or None. {"rev": ..., "audio": ...};
    "audio" may be None when it was never probed (the config overrode it)."""
    try:
        import json
        with open(path) as f:
            d = json.load(f)
    except Exception:
        return None
    if d.get("rev") not in REVISIONS or d.get("audio") not in (True, False, None):
        return None
    return d


def save_profile(rev, audio, path=PROFILE_PATH):
    import json
    with open(path, "w") as f:
        json.dump({"rev": rev, "audio": audio}, f)


def audio_setting(setting):
    """The config's HAS_AUDIO: True, False, or "auto"."""
    if setting is True or setting is False:
        return setting
    s = str(setting).strip().lower()
    if s == "auto":
        return "auto"
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    raise ValueError("HAS_AUDIO must be True, False or \"auto\", not %r" % (setting,))


def probe_modem(uart, pwrkey, sleep_ms, ticks_ms, ticks_diff, timeout_ms=12000):
    """True when an A7682E answers AT on `uart` within timeout_ms.

    The module must already be powered (GPIO 41 high). The power key is
    pulled low for 1.5 s and released, as SIMCom's power-on sequence wants;
    the modem then takes several seconds to boot, so AT is repeated every
    half second. A DAC unit has nothing on these pins and never answers.
    """
    pwrkey.value(0)
    sleep_ms(1500)
    pwrkey.value(1)
    t0 = ticks_ms()
    seen = b""
    while ticks_diff(ticks_ms(), t0) < timeout_ms:
        uart.write(b"AT\r\n")
        sleep_ms(500)
        n = uart.any()
        if n:
            seen += uart.read(n) or b""
            if b"OK" in seen:
                return True
            seen = seen[-16:]
    return False
