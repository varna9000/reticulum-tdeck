# The keyboard backlight shows whether the deck is awake.
#
# On a board with a display backlight, sleeping the screen dims it and both
# saves the power and shows the state. The T-Deck Pro has neither: its e-ink
# panel holds the last frame with no power, so a sleeping deck and a wedged one
# look exactly alike, and the inactivity timeout saves nothing at all. There the
# keyboard light follows the screen instead -- it is the one visible sign the
# deck is awake, and the largest discretionary draw on the board.
#
# Two things have to stay true. The user's preference is the master: an
# explicit OFF is never overridden by a wake. And the sleep/wake path must not
# reach the persisting callback, or every idle timeout rewrites settings.json.
#
# Run:  python3 tests/test_kbd_backlight_sleep.py

import os
import sys
import types
import time as _time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_time.ticks_ms = lambda: int(_time.time() * 1000)
_time.ticks_diff = lambda a, b: a - b
_time.sleep_ms = lambda ms: None
sys.modules.setdefault("uasyncio", types.ModuleType("uasyncio"))


class _Pin:
    IN = 0
    OUT = 1
    PULL_UP = 2
    IRQ_FALLING = 4

    def __init__(self, *a, **k):
        self._v = 0

    def irq(self, *a, **k):
        pass

    def value(self, *a):
        if a:
            self._v = a[0]
        return self._v


_machine = types.ModuleType("machine")
_machine.Pin = _Pin
sys.modules["machine"] = _machine

import ui

_failures = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        _failures.append(name)


class FakeTFT:
    def text(self, *a, **k):
        pass

    def fill_rect(self, *a, **k):
        pass

    def fill(self, *a, **k):
        pass


class Deck:
    """A UI plus a record of what reached each backlight callback."""

    def __init__(self, display_backlight):
        self.drives = []   # transient sleep/wake drives
        self.saves = []    # preference changes, which write settings.json
        self.lit = False
        self.gui = ui.UI(FakeTFT(), object(), lambda: b"\x00",
                         node_name="t", trackball=False)
        self.gui.set_backlight(_Pin() if display_backlight else None)
        self.gui.on_kbd_backlight = self._save
        self.gui.on_kbd_backlight_drive = self._drive

    def _drive(self, on):
        self.drives.append(bool(on))
        self.lit = bool(on)
        return True

    def _save(self, on):
        self.saves.append(bool(on))
        self.lit = bool(on)
        return True


def test_light_follows_sleep_without_a_display_backlight():
    print("the light follows the screen where nothing else shows it")
    d = Deck(display_backlight=False)
    d.gui.set_kbd_backlight_pref(True)
    check("turning it on lights it", d.lit is True)

    d.gui.sleep_screen()
    check("sleep puts the light out", d.lit is False, str(d.drives))
    check("sleep does not touch the preference", d.gui._kbd_bl is True)

    d.gui.wake_screen()
    check("wake brings it back", d.lit is True, str(d.drives))


def test_an_explicit_off_survives_a_wake():
    """The preference is the master. A deck whose owner turned the light off
    must not have it lit by every keypress."""
    print("an explicit off stays off")
    d = Deck(display_backlight=False)
    check("starts off", d.gui._kbd_bl is False)

    d.gui.sleep_screen()
    d.gui.wake_screen()
    check("wake leaves an off light off", d.lit is False, str(d.drives))
    check("wake drove nothing at all", d.drives == [], str(d.drives))


def test_sleep_never_writes_settings():
    """on_kbd_backlight persists to settings.json. Routing sleep and wake
    through it would rewrite flash on every inactivity timeout and every
    keypress that wakes the deck."""
    print("sleeping does not rewrite settings.json")
    d = Deck(display_backlight=False)
    d.gui.set_kbd_backlight_pref(True)
    saves_after_pref = len(d.saves)

    for _ in range(5):
        d.gui.sleep_screen()
        d.gui.wake_screen()

    check("the preference was saved once", saves_after_pref == 1, str(d.saves))
    check("five sleep/wake cycles saved nothing more",
          len(d.saves) == saves_after_pref, str(d.saves))
    check("but they did drive the hardware", len(d.drives) == 10, str(d.drives))


def test_a_board_with_a_display_backlight_is_untouched():
    """The v1 dims its screen on sleep, which already shows the state and
    already saves the power. Its keyboard light keeps its old behaviour."""
    print("a board with a display backlight keeps its old behaviour")
    d = Deck(display_backlight=True)
    d.gui.set_kbd_backlight_pref(True)
    d.drives = []

    d.gui.sleep_screen()
    check("sleep leaves the keyboard light alone", d.lit is True, str(d.drives))
    d.gui.wake_screen()
    check("wake leaves it alone too", d.drives == [], str(d.drives))


def test_toggling_off_while_awake_puts_it_out():
    print("the settings toggle still works")
    d = Deck(display_backlight=False)
    d.gui.set_kbd_backlight_pref(True)
    d.gui.set_kbd_backlight_pref(False)
    check("off means off", d.lit is False)
    check("and the state agrees with the preference",
          d.gui._kbd_bl is False and d.gui._kbd_bl_lit is False)

    d.gui.wake_screen()
    check("a later wake does not resurrect it", d.lit is False)


def test_a_refused_drive_leaves_the_state_honest():
    """set_kbd_backlight returns False when the write failed. The UI must not
    then claim the light is lit, or it will never retry."""
    print("a failed drive is not recorded as success")
    d = Deck(display_backlight=False)
    d.gui.set_kbd_backlight_pref(True)
    d.gui.on_kbd_backlight_drive = lambda on: False

    d.gui.sleep_screen()
    check("a refused drive leaves _kbd_bl_lit true", d.gui._kbd_bl_lit is True)


if __name__ == "__main__":
    test_light_follows_sleep_without_a_display_backlight()
    test_an_explicit_off_survives_a_wake()
    test_sleep_never_writes_settings()
    test_a_board_with_a_display_backlight_is_untouched()
    test_toggling_off_while_awake_puts_it_out()
    test_a_refused_drive_leaves_the_state_honest()
    print()
    if _failures:
        print("%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
        sys.exit(1)
    print("all passed")
