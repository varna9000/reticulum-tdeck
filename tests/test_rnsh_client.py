# Host-side tests for rnsh_client message routing (_on_message). Bootstraps
# urns via the submodule harness (for rnsh_proto's umsgpack/channel imports).
# Run:  python3 tests/test_rnsh_client.py

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
FW_TESTS = os.path.join(REPO, "vendor", "uP-reticulum", "firmware", "tests")

sys.path.insert(0, FW_TESTS)
import harness  # noqa: F401
sys.path.insert(0, REPO)

# uasyncio shim (rnsh_client imports it lazily inside functions, but be safe)
sys.modules.setdefault("uasyncio", types.ModuleType("uasyncio"))

import rnsh_client
import rnsh_proto as P


class FakeGui:
    def __init__(self):
        self.feeds = []
        self.exited = []
        self.closed = 0
        self.status = []

    def shell_feed(self, sid, data):
        self.feeds.append((sid, bytes(data)))

    def shell_exited(self, code):
        self.exited.append(code)

    def shell_closed(self):
        self.closed += 1

    def shell_status(self, text):
        self.status.append(text)

    def shell_connected(self):
        pass


def _reset(state=rnsh_client.RUNNING):
    g = FakeGui()
    rnsh_client._gui = g
    rnsh_client._link = None
    rnsh_client._channel = None
    rnsh_client._state = state
    rnsh_client._peer_version = None
    rnsh_client._error = None
    rnsh_client._exit_code = None
    return g


def test_stream_output_feeds_terminal():
    g = _reset()
    rnsh_client._on_message(P.StreamDataMessage(P.StreamDataMessage.STREAM_ID_STDOUT, b"hello"))
    rnsh_client._on_message(P.StreamDataMessage(P.StreamDataMessage.STREAM_ID_STDERR, b"err"))
    assert g.feeds == [(1, b"hello"), (2, b"err")], g.feeds
    print("ok test_stream_output_feeds_terminal")


def test_version_reply_recorded():
    _reset(state=rnsh_client.CONNECTING)
    rnsh_client._on_message(P.VersionInfoMessage("0.1.7", 1))
    assert rnsh_client._peer_version is not None
    assert rnsh_client._peer_version.protocol_version == 1
    print("ok test_version_reply_recorded")


def test_command_exited_reports_and_closes():
    g = _reset()
    rnsh_client._on_message(P.CommandExitedMessage(137))
    assert g.exited == [137]
    assert rnsh_client._state == rnsh_client.CLOSED
    print("ok test_command_exited_reports_and_closes")


def test_fatal_error_tears_down():
    g = _reset()
    rnsh_client._on_message(P.ErrorMessage("Identity is not allowed.", True, None))
    assert rnsh_client._error == ("Identity is not allowed.", True)
    assert rnsh_client._state == rnsh_client.CLOSED
    assert g.closed == 1
    assert any("not allowed" in (s or "") for s in g.status)
    print("ok test_fatal_error_tears_down")


def test_nonfatal_error_keeps_running():
    g = _reset()
    rnsh_client._on_message(P.ErrorMessage("just a warning", False, None))
    assert rnsh_client._state == rnsh_client.RUNNING
    assert g.closed == 0
    print("ok test_nonfatal_error_keeps_running")


def test_geometry_reaches_the_pty():
    """connect() geometry is what ExecuteCommand carries, and a resize during
    the connect is not lost (over LoRa that window is 30-90s)."""
    _reset(rnsh_client.IDLE)
    # connect() schedules the session coroutine; stub the scheduler so the
    # host test exercises only the geometry bookkeeping.
    import uasyncio as _aio
    _saved = getattr(_aio, "create_task", None)

    def _fake_create_task(coro):
        coro.close()
        return None

    _aio.create_task = _fake_create_task
    try:
        rnsh_client.connect(b"\x01" * 16, cols=53, rows=16)
    finally:
        if _saved is not None:
            _aio.create_task = _saved
    assert (rnsh_client._term_cols, rnsh_client._term_rows) == (53, 16), \
        (rnsh_client._term_cols, rnsh_client._term_rows)

    # font switched while still CONNECTING: no WindowSize can be sent yet,
    # but the pending ExecuteCommand must pick up the new size.
    class Ch:
        def __init__(self): self.sent = []
        def is_ready_to_send(self): return True
        def send(self, m): self.sent.append(m)

    ch = Ch()
    rnsh_client._channel = ch
    rnsh_client._state = rnsh_client.CONNECTING
    rnsh_client.resize(32, 80)
    assert ch.sent == [], "WindowSize sent before the session was up"
    assert (rnsh_client._term_cols, rnsh_client._term_rows) == (80, 32), \
        "mid-connect resize was dropped"

    msg = P.ExecuteCommandMessage(
        cmdline=None, pipe_stdin=False, pipe_stdout=False, pipe_stderr=False,
        tcflags=None, term="vt100", rows=rnsh_client._term_rows,
        cols=rnsh_client._term_cols, hpix=None, vpix=None)
    rt = P.ExecuteCommandMessage()
    rt.unpack(msg.pack())
    assert (rt.cols, rt.rows) == (80, 32), (rt.cols, rt.rows)
    assert rt.term == "vt100", rt.term

    # once RUNNING, a switch goes out as a WindowSize with rows before cols
    rnsh_client._state = rnsh_client.RUNNING
    rnsh_client.resize(16, 53)
    assert len(ch.sent) == 1, ch.sent
    w = P.WindowSizeMessage()
    w.unpack(ch.sent[0].pack())
    assert (w.rows, w.cols) == (16, 53), (w.rows, w.cols)
    rnsh_client._channel = None
    print("ok test_geometry_reaches_the_pty")


def test_window_size_retries_when_channel_busy():
    """A resize issued while the Channel window is full must not be lost."""
    _reset(rnsh_client.RUNNING)

    class Ch:
        def __init__(self): self.sent = []; self.ready = False
        def is_ready_to_send(self): return self.ready
        def send(self, m): self.sent.append(m)

    ch = Ch()
    rnsh_client._channel = ch
    rnsh_client._size_sent = (16, 53)

    rnsh_client.resize(32, 80)                  # channel busy -> dropped
    assert ch.sent == [], ch.sent
    assert rnsh_client._size_sent == (16, 53), rnsh_client._size_sent
    assert (rnsh_client._term_rows, rnsh_client._term_cols) == (32, 80)

    # what _pump_loop does each tick once the window frees up
    ch.ready = True
    assert rnsh_client._size_sent != (rnsh_client._term_rows, rnsh_client._term_cols)
    rnsh_client._send_window_size()
    assert len(ch.sent) == 1, ch.sent
    w = P.WindowSizeMessage()
    w.unpack(ch.sent[0].pack())
    assert (w.rows, w.cols) == (32, 80), (w.rows, w.cols)
    assert rnsh_client._size_sent == (32, 80)

    # settled: no further sends
    rnsh_client._send_window_size()
    assert len(ch.sent) == 2, "resend guard is in _pump_loop, not the sender"
    rnsh_client._channel = None
    rnsh_client._size_sent = None
    print("ok test_window_size_retries_when_channel_busy")


def test_noop_ignored():
    g = _reset()
    rnsh_client._on_message(P.NoopMessage())
    assert g.feeds == [] and g.exited == []
    print("ok test_noop_ignored")


if __name__ == "__main__":
    for name in list(globals()):
        if name.startswith("test_"):
            globals()[name]()
    print("all rnsh_client tests passed")
