# Host tests for the touch gesture recogniser. Run:
#   /opt/homebrew/bin/python3 tests/test_touch_gestures.py
#
# Event timings come from the deck (2026-10-10): taps of 67..473 ms, MOVEs
# about every 26 ms, a late-read touch arriving as DOWN + UP with true times.

import os
import sys
import time as _time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_time.ticks_ms = lambda: 0
_time.ticks_diff = lambda a, b: a - b

import touch_gestures as tg  # noqa: E402
from touch_gestures import DOWN, MOVE, UP, TAP, LONG, DRAG, FLICK, SWIPE, BACK  # noqa: E402

_failures = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        _failures.append(name)


def run(r, evs, ticks=()):
    """Feed events in order; return every gesture produced."""
    out = []
    for ev in evs:
        out += r.feed(ev)
    for t in ticks:
        out += r.tick(t)
    return out


def test_tap():
    print("tap")
    r = tg.Recognizer()
    g = run(r, [(DOWN, 100, 150, 1000), (UP, 100, 150, 1067)])
    check("a 67 ms touch is a TAP at the DOWN point, carrying its DOWN time",
          g == [(TAP, 100, 150, 0, 1000)], repr(g))
    g = run(tg.Recognizer(), [(DOWN, 100, 150, 1000), (MOVE, 104, 153, 1200), (UP, 104, 153, 1473)])
    check("473 ms with 5 px of wobble is still a TAP at the DOWN point",
          g == [(TAP, 100, 150, 0, 1000)], repr(g))
    g = run(tg.Recognizer(), [(DOWN, 100, 150, 1000), (UP, 100, 150, 1699)])
    check("699 ms is a TAP", [x[0] for x in g] == [TAP], repr(g))
    g = run(tg.Recognizer(), [(DOWN, 100, 150, 1000), (UP, 100, 150, 1700)])
    check("a DOWN+UP pair of 700 ms read late is nothing (a stale long press)", g == [], repr(g))


def test_long_press():
    print("long press")
    r = tg.Recognizer()
    g = r.feed((DOWN, 60, 60, 1000))
    g += r.tick(1699)
    check("nothing before 700 ms", g == [], repr(g))
    g = r.tick(1700)
    check("LONG at 700 ms while still held", g == [(LONG, 60, 60, 0, 1000)], repr(g))
    g = r.tick(1800) + r.tick(2500)
    check("only once per stroke", g == [], repr(g))
    g = r.feed((UP, 60, 60, 2600))
    check("its UP emits nothing", g == [], repr(g))
    r = tg.Recognizer()
    r.feed((DOWN, 60, 60, 1000))
    r.feed((MOVE, 80, 60, 1300))          # moved 20 px: not a long press any more
    g = r.tick(1800)
    check("no LONG once the finger has moved past the slop", g == [], repr(g))


def test_key_guard():
    print("key guard")
    r = tg.Recognizer()
    r.note_key(1000)
    g = run(r, [(DOWN, 50, 50, 1299), (UP, 50, 50, 1400)])
    check("a touch 299 ms after a key is dropped whole", g == [], repr(g))
    g = r.tick(2100)
    check("and never becomes a long press", g == [], repr(g))
    g = run(r, [(DOWN, 50, 50, 1300), (UP, 50, 50, 1400)])
    check("300 ms after the key it counts", [x[0] for x in g] == [TAP], repr(g))
    r = tg.Recognizer()
    g = run(r, [(DOWN, 50, 50, 900), (UP, 50, 50, 950)])
    r.note_key(1000)
    check("a touch before the key is unaffected", [x[0] for x in g] == [TAP], repr(g))


def test_edge_reject():
    print("edge reject")
    g = run(tg.Recognizer(), [(DOWN, 239, 317, 1000), (UP, 239, 317, 1064)])
    check("the corner touch seen on the deck (239,317), 64 ms, is ignored", g == [], repr(g))
    g = run(tg.Recognizer(), [(DOWN, 239, 317, 1000), (UP, 239, 317, 1100)])
    check("100 ms at the edge is a real tap", [x[0] for x in g] == [TAP], repr(g))
    g = run(tg.Recognizer(), [(DOWN, 235, 315, 1000), (UP, 235, 315, 1064)])
    check("5 px in from both edges is a real tap", [x[0] for x in g] == [TAP], repr(g))
    g = run(tg.Recognizer(), [(DOWN, 2, 150, 1000), (UP, 2, 150, 1050)])
    check("a 50 ms brush on the left edge is ignored", g == [], repr(g))
    g = run(tg.Recognizer(), [(DOWN, 120, 318, 1000), (UP, 120, 318, 1300)])
    check("a normal 300 ms tap on the footer's bottom edge counts", [x[0] for x in g] == [TAP], repr(g))


def test_cancel_and_restart():
    print("cancel")
    r = tg.Recognizer()
    r.feed((DOWN, 50, 50, 1000))
    r.cancel()
    g = r.feed((UP, 50, 50, 1100)) + r.tick(1800)
    check("a cancelled stroke emits nothing on UP or tick", g == [], repr(g))
    g = run(r, [(MOVE, 60, 60, 1900), (UP, 60, 60, 1950)])
    check("stray MOVE / UP with no stroke are ignored", g == [], repr(g))
    g = run(r, [(DOWN, 10, 100, 2000), (DOWN, 90, 100, 2500), (UP, 90, 100, 2600)])
    check("a second DOWN restarts the stroke", g == [(TAP, 90, 100, 0, 2500)], repr(g))


def stroke(x0, y0, x1, y1, t0=1000, dt=26, n=10):
    """DOWN, n evenly spaced MOVEs to (x1, y1), UP one interval later."""
    evs = [(DOWN, x0, y0, t0)]
    for i in range(1, n + 1):
        evs.append((MOVE, x0 + (x1 - x0) * i // n, y0 + (y1 - y0) * i // n, t0 + dt * i))
    evs.append((UP, x1, y1, t0 + dt * (n + 1)))
    return evs


def kinds(gs):
    return [g[0] for g in gs]


def test_drag_steps():
    print("drag")
    r = tg.Recognizer()
    g = run(r, [(DOWN, 100, 100, 1000), (MOVE, 100, 109, 1100)])
    check("9 px is inside the slop: nothing", g == [], repr(g))
    g = r.feed((MOVE, 100, 116, 1200))
    check("16 px down is one step, at the DOWN point", g == [(DRAG, 100, 100, 1, 1000)], repr(g))
    g = r.feed((MOVE, 100, 131, 1300))
    check("31 px: no new step yet (remainder carried)", g == [], repr(g))
    g = r.feed((MOVE, 100, 150, 1400))
    check("50 px total = 3 steps, so 2 more", g == [(DRAG, 100, 100, 2, 1000)], repr(g))
    g = r.feed((MOVE, 100, 110, 1500))
    check("back up to 10 px gives -2: a reversal's first step needs half a step of retreat (hysteresis)",
          g == [(DRAG, 100, 100, -2, 1000)], repr(g))
    g = r.feed((UP, 100, 110, 3000))
    check("a slow release is not a flick and not a tap", g == [], repr(g))
    g = run(tg.Recognizer(), [(DOWN, 100, 200, 1000), (MOVE, 100, 150, 1100)])
    check("upward drag gives negative steps", g == [(DRAG, 100, 200, -3, 1000)], repr(g))


def test_drag_big_jump_yields_all_steps():
    g = run(tg.Recognizer(), [(DOWN, 100, 50, 1000), (MOVE, 102, 150, 1700)])
    check("one 100 px jump (the loop was blocked) is 6 steps at once",
          g == [(DRAG, 100, 50, 6, 1000)], repr(g))


def test_flick():
    print("flick")
    g = run(tg.Recognizer(), stroke(100, 250, 100, 50, dt=26, n=8))     # 200 px in 208 ms
    check("a fast upward drag ends in FLICK -1", kinds(g)[-1] == FLICK and g[-1][3] == -1, repr(g[-1:]))
    check("and its drag steps came first", kinds(g)[:-1] == [DRAG] * (len(g) - 1) and len(g) > 1)
    g = run(tg.Recognizer(), stroke(100, 50, 100, 250, dt=26, n=8))
    check("downward: FLICK +1", g[-1][0] == FLICK and g[-1][3] == 1, repr(g[-1:]))
    g = run(tg.Recognizer(), stroke(100, 50, 100, 250, dt=100, n=8))    # 200 px in 800 ms = 250 px/s
    check("250 px/s is a drag with no flick", FLICK not in kinds(g), repr(kinds(g)))
    evs = stroke(100, 50, 100, 250, dt=26, n=8)[:-1] + [(MOVE, 100, 250, 1500), (UP, 100, 250, 1530)]
    g = run(tg.Recognizer(), evs)
    check("a fast drag that stops before lifting is not a flick", FLICK not in kinds(g), repr(kinds(g)))
    evs = stroke(100, 50, 100, 250, dt=26, n=8)[:-1] + [(UP, 100, 250, 1700)]
    g = run(tg.Recognizer(), evs)
    check("a fast drag, then the finger rests 500 ms with no further MOVE, is not a flick",
          FLICK not in kinds(g), repr(kinds(g)))


def test_swipe_and_back():
    print("swipe / back")
    g = run(tg.Recognizer(), stroke(60, 150, 160, 155))
    check("100 px right is SWIPE +1 at the DOWN point", g == [(SWIPE, 60, 150, 1, 1000)], repr(g))
    g = run(tg.Recognizer(), stroke(200, 150, 100, 150))
    check("leftward is SWIPE -1", g == [(SWIPE, 200, 150, -1, 1000)], repr(g))
    g = run(tg.Recognizer(), stroke(60, 150, 99, 150))
    check("39 px is nothing", g == [], repr(g))
    g = run(tg.Recognizer(), stroke(60, 150, 100, 150))
    check("40 px is a swipe", kinds(g) == [SWIPE], repr(g))
    # A thumb sweeps an arc, not a ruler line: the drift allowed grows with
    # the length of the swipe (a fixed 20 px was 4 mm, and swipes were lost).
    g = run(tg.Recognizer(), stroke(60, 150, 160, 190))
    check("100 px right drifting 40 px down is still a swipe", g == [(SWIPE, 60, 150, 1, 1000)], repr(g))
    g = run(tg.Recognizer(), stroke(200, 150, 80, 80))
    check("120 px left drifting 70 px up is still a swipe", g == [(SWIPE, 200, 150, -1, 1000)], repr(g))
    g = run(tg.Recognizer(), stroke(60, 150, 160, 225))
    check("drift of 3/4 of the travel is the limit", kinds(g) == [SWIPE], repr(g))
    g = run(tg.Recognizer(), stroke(60, 150, 160, 230))
    check("a diagonal (80 px down over 100) is nothing", g == [], repr(g))
    g = run(tg.Recognizer(), stroke(23, 150, 123, 150))
    check("starting at x=23 and going right is BACK", g == [(BACK, 23, 150, 0, 1000)], repr(g))
    g = run(tg.Recognizer(), stroke(24, 150, 124, 150))
    check("starting at x=24 is an ordinary swipe", kinds(g) == [SWIPE], repr(g))
    g = run(tg.Recognizer(), stroke(120, 150, 5, 150))
    check("ending in the edge band going left is a swipe, not BACK", g == [(SWIPE, 120, 150, -1, 1000)], repr(g))
    g = run(tg.Recognizer(), stroke(60, 150, 160, 150), ticks=(2500,))
    check("a horizontal stroke never becomes a long press", LONG not in kinds(g))


def test_axis_is_decided_once():
    r = tg.Recognizer()
    g = run(r, [(DOWN, 100, 100, 1000), (MOVE, 100, 130, 1100), (MOVE, 180, 135, 1200), (UP, 180, 135, 2000)])
    check("a stroke that began vertical stays a drag even if it wanders sideways",
          SWIPE not in kinds(g) and kinds(g)[0] == DRAG, repr(g))
    g = run(tg.Recognizer(), [(DOWN, 100, 100, 1000), (MOVE, 130, 100, 1100), (MOVE, 135, 200, 1200), (UP, 135, 200, 2000)])
    check("a stroke that began horizontal never emits drag steps", DRAG not in kinds(g), repr(g))



def test_drag_does_not_flap_at_a_step_boundary():
    # A finger resting on a 16 px boundary jitters by a pixel or two. Each
    # drag step is a selection change and a ~700 ms e-ink refresh, so a
    # boundary must not be a place where steps toggle.
    r = tg.Recognizer()
    g = run(r, [(DOWN, 100, 100, 1000), (MOVE, 100, 133, 1100)])
    check("33 px: two steps", g == [(DRAG, 100, 100, 2, 1000)], repr(g))
    out = []
    for i, y in enumerate((131, 133, 130, 134, 131, 133)):
        out += r.feed((MOVE, 100, y, 1200 + 30 * i))
    check("jitter of a few px around the boundary emits nothing", out == [], repr(out))
    g = r.feed((MOVE, 100, 123, 1500))
    check("retreating 9 px past the boundary steps back once", g == [(DRAG, 100, 100, -1, 1000)], repr(g))
    g = r.feed((MOVE, 100, 131, 1600))
    check("and coming forward 8 px steps forward again", g == [(DRAG, 100, 100, 1, 1000)], repr(g))


if __name__ == "__main__":
    test_tap()
    test_long_press()
    test_key_guard()
    test_edge_reject()
    test_cancel_and_restart()
    test_drag_steps()
    test_drag_big_jump_yields_all_steps()
    test_flick()
    test_swipe_and_back()
    test_axis_is_decided_once()
    test_drag_does_not_flap_at_a_step_boundary()
    print()
    if _failures:
        print("%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
        sys.exit(1)
    print("all passed")
