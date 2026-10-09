# Host-side test for the GDEQ031T10 (UC8253) panel driver's command sequence.
# Run:
#   python3 tests/test_eink_driver.py
#
# The panel refreshes a partial window by diffing its "current" buffer (0x13)
# against its "previous" buffer (0x10) and only drives pixels that differ. The
# driver therefore has to copy the window into 0x10 after every partial
# refresh, and -- the part this test exists for -- a refresh leaves the panel
# needing its panel-setting re-init before it accepts another RAM write. Skip
# the re-init and the 0x10 write is silently dropped: the next partial sees
# the moved selection bar's old row as unchanged and leaves the old bar on the
# paper. Seen on hardware 2026-10-09 as a trail behind the node-list cursor.

import os
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

# --- MicroPython shims -----------------------------------------------------

_mp = types.ModuleType("micropython")
_mp.const = lambda x: x
sys.modules.setdefault("micropython", _mp)


class _FrameBuffer:
    def __init__(self, buf, width, height, fmt):
        self.buf, self.width, self.height = buf, width, height


_fbmod = types.ModuleType("framebuf")
_fbmod.FrameBuffer = _FrameBuffer
_fbmod.MONO_HLSB = 0
sys.modules.setdefault("framebuf", _fbmod)

if not hasattr(time, "ticks_ms"):
    time.ticks_ms = lambda: int(time.time() * 1000)
    time.ticks_add = lambda a, b: a + b
    time.ticks_diff = lambda a, b: a - b
    time.sleep_ms = lambda ms: None

import eink_gdeq031t10  # noqa: E402


# --- fake hardware ---------------------------------------------------------

class FakePin:
    def __init__(self, v=1):
        self.v = v

    def value(self, v=None):
        if v is None:
            return self.v
        self.v = v


class FakeSPI:
    """Records the command stream: a list of (command, n_data_bytes)."""

    def __init__(self, dc):
        self.dc = dc
        self.log = []

    def write(self, buf):
        if self.dc.value() == 0:
            for b in bytes(buf):
                self.log.append([b, 0])
        elif self.log:
            self.log[-1][1] += len(buf)


def make_panel():
    dc = FakePin(1)
    spi = FakeSPI(dc)
    panel = eink_gdeq031t10.GDEQ031T10(spi, FakePin(1), dc, FakePin(1))
    return panel, spi


def commands(spi):
    return [c for c, _ in spi.log]


# --- tests -----------------------------------------------------------------

def test_partial_refresh_reinits_before_previous_buffer_write():
    panel, spi = make_panel()
    panel.flush()                       # the mandatory first full refresh
    spi.log.clear()
    panel.flush_rect(0, 100, 32, 16)

    cmds = commands(spi)
    assert 0x12 in cmds, "no display refresh in %r" % cmds
    refresh = cmds.index(0x12)
    after = cmds[refresh + 1:]
    assert 0x10 in after, "previous buffer (0x10) not rewritten after the refresh: %r" % cmds
    prev_write = after.index(0x10)
    assert 0x00 in after[:prev_write], (
        "panel setting (0x00) must be re-sent between the refresh (0x12) and "
        "the previous-buffer write (0x10); got %r" % after)
    # and the 0x10 write carried the window's bytes: 32px wide -> 4 bytes x 16 rows
    n = [n for c, n in spi.log[refresh + 1:] if c == 0x10][0]
    assert n == 4 * 16, "previous-buffer write carried %d bytes, expected 64" % n


def test_partial_refresh_writes_current_then_refreshes():
    panel, spi = make_panel()
    panel.flush()
    spi.log.clear()
    panel.flush_rect(8, 20, 16, 8)
    cmds = commands(spi)
    assert cmds.index(0x13) < cmds.index(0x12), "current buffer must be written before the refresh"
    assert cmds.count(0x12) == 1


def test_first_flush_rect_is_a_full_refresh():
    panel, spi = make_panel()
    panel.flush_rect(0, 0, 8, 8)
    cmds = commands(spi)
    assert 0x91 not in cmds, "initial update must be a full refresh, not a partial window"
    assert 0x10 in cmds and 0x13 in cmds and 0x12 in cmds


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print("ok  ", t.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL", t.__name__, "--", e)
    print("all passed" if not failed else "%d failure(s)" % failed)
    sys.exit(1 if failed else 0)
