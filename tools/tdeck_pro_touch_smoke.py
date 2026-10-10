# Touch driver smoke test on the deck, through the real board module and
# without the app: a fake UI that says the screen is on, then 15 s of
# get_key() passes printing every touch event.
#
#   mpremote connect /dev/cu.usbmodem1101 run tools/tdeck_pro_touch_smoke.py
#
# Expect: "touch: ready" within ~100 ms, then DOWN/MOVE/UP lines as you tap
# and drag (kind 1/2/3), nothing while you do not, and "asleep" after the
# fake screen goes dark at the end.

import time
import board_tdeck_pro as board


class FakeUI:
    _screen_on = True
    inputs = 0

    def wake_screen(self, force=False, why=None):
        self.inputs += 1            # what stamps the inactivity clock in the real UI

    def nav_event(self, name):
        print("nav", name)

    def toggle_kbd_backlight(self):
        pass


ui = FakeUI()
board.attach_ui(ui)
print("touch object:", board.touch)
if board.touch is None:
    raise SystemExit("no touch driver constructed; check HAS_TOUCH and the I2C scan")

seen = []
_board_handler = board._touch_handler     # keep the board's own handling in the path


def _spy(ev):
    seen.append(ev)
    print("event", ev)
    _board_handler(ev)


board._touch_handler = _spy

t0 = time.ticks_ms()
ready_at = None
while time.ticks_diff(time.ticks_ms(), t0) < 15000:
    board.get_key()
    if ready_at is None and board.touch.ready:
        ready_at = time.ticks_diff(time.ticks_ms(), t0)
        print("touch: ready after %d ms -- tap and drag now" % ready_at)
        board.set_kbd_backlight(True)       # the cue for the person holding the deck
    time.sleep_ms(20)

board.set_kbd_backlight(False)
ui._screen_on = False
board.get_key()
print("screen off -> asleep:", board.touch.asleep, "ready:", board.touch.ready)
kinds = [e[0] for e in seen]
print("events: %d (DOWN %d, MOVE %d, UP %d), activity stamps %d (expect DOWN + MOVE)"
      % (len(seen), kinds.count(1), kinds.count(2), kinds.count(3), ui.inputs))
durs = []
t_down = None
for k, x, y, t in seen:
    if k == 1:
        t_down = t
    elif k == 3 and t_down is not None:
        durs.append(time.ticks_diff(t, t_down))
        t_down = None
print("stroke durations ms:", durs)
