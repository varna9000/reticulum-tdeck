# Touch gestures for the T-Deck Pro: the CST328 driver's DOWN / MOVE / UP
# events in, gestures out. Knows nothing about screens (touch_ui.py does
# that) and nothing about hardware; it never reads the clock except through
# the events' own times and tick(now).
#
# A gesture is (kind, x, y, n, t_down). x, y are where the finger went DOWN,
# because that is the thing the user aimed at; t_down lets the dispatcher drop
# a stroke that began before the screen it would act on was drawn.
#
# The numbers are the ones agreed for the Pro's panel (0.2 mm pixels) and
# measured on it: taps of 67..473 ms, MOVEs every ~26 ms.

import time

DOWN = 1      # the driver's event kinds (cst328.DOWN / MOVE / UP)
MOVE = 2
UP = 3

TAP = 1
LONG = 2      # still down after LONG_MS, emitted by tick() while held
DRAG = 3      # n = signed 16 px steps since the last DRAG (+ = finger moved down)
FLICK = 4     # n = +1 / -1, the direction the finger was moving on release
SWIPE = 5     # n = +1 finger moved right, -1 left
BACK = 6      # a rightward swipe that began in the left edge band

LONG_MS = 700
SLOP_PX = 10
STEP_PX = 16
FLICK_PX_S = 600
FLICK_WINDOW_MS = 100
SWIPE_MIN_PX = 40
SWIPE_MAX_DY = 20
EDGE_BAND_X = 24
KEY_GUARD_MS = 300
EDGE_REJECT_PX = 4
EDGE_REJECT_MS = 100
EDGE_REJECT_TRAVEL = 3

_PENDING = 0  # not yet moved past the slop
_VERT = 1     # a vertical drag
_HORIZ = 2    # horizontal travel: a swipe or nothing
_HELD = 3     # LONG already emitted


class Recognizer:
    def __init__(self, width=240, height=320):
        self._w = width
        self._h = height
        self._active = False
        self._dead = False       # this stroke is being ignored
        self._mode = _PENDING
        self._x0 = self._y0 = self._t0 = 0
        self._x = self._y = 0
        self._far = 0            # furthest travel from the DOWN point, px
        self._anchor = 0         # vertical travel at the last emitted drag step
        self._dir = 0            # direction of the last drag step: +1, -1, 0 = none yet
        self._hist = []          # recent (t, y) for the flick velocity
        self._key_ms = None

    def note_key(self, now_ms):
        """A key was pressed: thumbs are on the keyboard, and a touch in the
        next KEY_GUARD_MS is far more likely a palm than a tap."""
        self._key_ms = now_ms

    def cancel(self):
        self._active = False

    def tick(self, now_ms):
        if (self._active and not self._dead and self._mode == _PENDING
                and self._far <= SLOP_PX
                and time.ticks_diff(now_ms, self._t0) >= LONG_MS):
            self._mode = _HELD
            return [(LONG, self._x0, self._y0, 0, self._t0)]
        return []

    def feed(self, ev):
        kind, x, y, t = ev
        if kind == DOWN:
            self._active = True
            self._mode = _PENDING
            self._x0, self._y0, self._t0 = x, y, t
            self._x, self._y = x, y
            self._far = 0
            self._anchor = 0
            self._dir = 0
            self._hist = [(t, y)]
            k = self._key_ms
            self._dead = k is not None and 0 <= time.ticks_diff(t, k) < KEY_GUARD_MS
            return []
        if not self._active:
            return []
        if kind == MOVE:
            return self._move(x, y, t)
        if kind == UP:
            self._active = False
            if self._dead:
                return []
            return self._up(x, y, t)
        return []

    def _travel(self, x, y):
        dx = abs(x - self._x0)
        dy = abs(y - self._y0)
        d = dx if dx > dy else dy
        if d > self._far:
            self._far = d

    def _on_edge(self):
        e = EDGE_REJECT_PX
        return (self._x0 < e or self._x0 >= self._w - e
                or self._y0 < e or self._y0 >= self._h - e)

    def _move(self, x, y, t):
        self._x, self._y = x, y
        self._travel(x, y)
        if self._dead:
            return []
        dx = x - self._x0
        dy = y - self._y0
        if self._mode == _PENDING and self._far > SLOP_PX:
            # The axis is decided once, where the finger leaves the slop.
            self._mode = _VERT if abs(dy) >= abs(dx) else _HORIZ
        if self._mode != _VERT:
            return []
        self._hist.append((t, y))
        if len(self._hist) > 8:
            self._hist.pop(0)
        # Steps are counted from where the last one was emitted. Carrying on
        # the same way takes a full step; turning back takes half a step of
        # retreat first. Without that a finger resting on a 16 px boundary
        # toggles a step on every pixel of jitter, and each step is a
        # selection change and a ~700 ms refresh.
        d = dy - self._anchor
        half = STEP_PX // 2
        n = 0
        if d >= STEP_PX and self._dir >= 0:
            n = d // STEP_PX
        elif d <= -STEP_PX and self._dir <= 0:
            n = -((-d) // STEP_PX)
        elif self._dir > 0 and d <= -half:
            n = -((-d - half) // STEP_PX + 1)
        elif self._dir < 0 and d >= half:
            n = (d - half) // STEP_PX + 1
        if n == 0:
            return []
        self._anchor += n * STEP_PX
        self._dir = 1 if n > 0 else -1
        return [(DRAG, self._x0, self._y0, n, self._t0)]

    def _flick(self):
        """+1 / -1 if the last FLICK_WINDOW_MS of the drag averaged more
        than FLICK_PX_S, else 0. Measured between MOVE samples: the UP's own
        time lags the finger by up to a poll."""
        h = self._hist
        if len(h) < 2:
            return 0
        t1, y1 = h[-1]
        t0, y0 = h[-1]
        for ts, ys in reversed(h[:-1]):
            if time.ticks_diff(t1, ts) > FLICK_WINDOW_MS:
                break
            t0, y0 = ts, ys
        dt = time.ticks_diff(t1, t0)
        if dt <= 0:
            return 0
        v = (y1 - y0) * 1000 // dt
        if v > FLICK_PX_S:
            return 1
        if v < -FLICK_PX_S:
            return -1
        return 0

    def _up(self, x, y, t):
        self._travel(x, y)
        dur = time.ticks_diff(t, self._t0)
        if self._mode == _PENDING:
            if self._on_edge() and dur < EDGE_REJECT_MS and self._far < EDGE_REJECT_TRAVEL:
                return []            # a grip or a brush, not a tap
            if dur < LONG_MS:
                return [(TAP, self._x0, self._y0, 0, self._t0)]
            return []
        if self._mode == _VERT:
            # A drag that paused before lifting has a stale last sample.
            if time.ticks_diff(t, self._hist[-1][0]) > FLICK_WINDOW_MS:
                return []
            d = self._flick()
            return [(FLICK, self._x0, self._y0, d, self._t0)] if d else []
        if self._mode == _HORIZ:
            dx = x - self._x0
            if abs(dx) >= SWIPE_MIN_PX and abs(y - self._y0) < SWIPE_MAX_DY:
                if dx > 0 and self._x0 < EDGE_BAND_X:
                    return [(BACK, self._x0, self._y0, 0, self._t0)]
                return [(SWIPE, self._x0, self._y0, 1 if dx > 0 else -1, self._t0)]
        return []
