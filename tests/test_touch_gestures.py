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


if __name__ == "__main__":
    test_tap()
    test_long_press()
    test_key_guard()
    test_edge_reject()
    test_cancel_and_restart()
    print()
    if _failures:
        print("%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
        sys.exit(1)
    print("all passed")
