# Host-side test for tdeck_node's send path against urns' delivery semantics
# (reference LXMF 1.3.1 scheduling, uP-reticulum rns-161-stamps and later):
# send_message() queues and returns the LXMessage, or None when the outbox is
# full; the row's mark moves only from the sent / delivered / failed callbacks.
# Lifts _status_callbacks, _submit and _async_send out of tdeck_node.py (it
# starts hardware at import) and runs them against a recording gui and a fake
# router driven like the real one.
#
# Run:  python3 tests/test_send_status.py

import ast
import asyncio
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# urns.lxmf.LXMessage: the real constants, without importing the stack.
OUTBOUND, SENDING, SENT, DELIVERED, REJECTED, FAILED = 0x01, 0x02, 0x04, 0x08, 0xFD, 0xFF
OPPORTUNISTIC, DIRECT = 0x01, 0x02
_lx = types.ModuleType("urns.lxmf")
_lx.LXMessage = types.SimpleNamespace(OUTBOUND=OUTBOUND, SENDING=SENDING, SENT=SENT,
                                      DELIVERED=DELIVERED, FAILED=FAILED,
                                      OPPORTUNISTIC=OPPORTUNISTIC, DIRECT=DIRECT)
_urns = types.ModuleType("urns")
_urns.lxmf = _lx
sys.modules.update({"urns": _urns, "urns.lxmf": _lx, "uasyncio": asyncio})


def _lifted():
    with open(os.path.join(ROOT, "tdeck_node.py")) as f:
        tree = ast.parse(f.read())
    want = ("_status_callbacks", "_submit", "_async_send")
    nodes = [n for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in want]
    assert sorted(n.name for n in nodes) == sorted(want)
    return compile(ast.Module(body=nodes, type_ignores=[]), "tdeck_node.py", "exec")


_CODE = _lifted()


class Gui:
    def __init__(self):
        self.status = {}
        self.lines = []
        self.dirty = False
        self._next = 0

    def add_chat_message(self, dest, mine, text, status=0, **kw):
        self._next += 1
        self.status[self._next] = status
        self.lines.append(text)
        return self._next

    def update_message_status(self, dest, mid, st):
        self.status[mid] = st


class Msg:
    def __init__(self, state):
        self.state = state


class Router:
    """mode: 'queue' (OUTBOUND, no path yet), 'sent_now' (fires sent inside
    send_message, as an opportunistic send with a path does), 'full' (None)."""

    def __init__(self, mode):
        self.mode = mode
        self.calls = []

    def send_message(self, dest, content, fields=None, desired_method=None,
                     sent_callback=None, delivery_callback=None, failed_callback=None):
        self.calls.append((dest, content, desired_method))
        if self.mode == "full":
            return None
        self.cbs = (sent_callback, delivery_callback, failed_callback)
        if self.mode == "sent_now":
            self.msg = Msg(SENT)
            sent_callback(self.msg)
        else:
            self.msg = Msg(OUTBOUND)
        return self.msg


def _node(mode):
    gui = Gui()
    router = Router(mode)
    tx = []
    g = {"gui": gui, "router": router, "DEBUG": 0,
         "sound": types.SimpleNamespace(play_tx=lambda: tx.append(1)),
         "gc": types.SimpleNamespace(collect=lambda: None)}
    exec(_CODE, g)
    return g, gui, router, tx


def _send(g, text="hi"):
    asyncio.run(g["_async_send"](b"D" * 16, text))


def test_opportunistic_with_path_is_sent_then_delivered_on_proof():
    g, gui, router, tx = _node("sent_now")
    _send(g)
    assert gui.status[1] == 5, gui.status          # '>' sent, not yet a tick
    assert tx == [1]
    assert router.calls[0][2] == OPPORTUNISTIC
    router.msg.state = DELIVERED
    router.cbs[1](router.msg)
    assert gui.status[1] == 2                      # tick only on the proof


def test_no_path_shows_queued_until_sent():
    g, gui, router, tx = _node("queue")
    _send(g)
    assert gui.status[1] == 4 and tx == []         # '~' queued: nothing on air yet
    router.msg.state = SENT
    router.cbs[0](router.msg)
    assert gui.status[1] == 5 and tx == [1]


def test_retries_exhausted_mark_failed():
    g, gui, router, tx = _node("sent_now")
    _send(g)
    router.msg.state = FAILED
    router.cbs[2](router.msg)
    assert gui.status[1] == 3


def test_rejected_transfer_marks_failed():
    g, gui, router, tx = _node("queue")
    _send(g)
    router.msg.state = REJECTED
    router.cbs[2](router.msg)
    assert gui.status[1] == 3


def test_outbox_full_fails_at_once_with_a_hint():
    g, gui, router, tx = _node("full")
    _send(g)
    assert gui.status[1] == 3
    assert any("outbox full" in ln for ln in gui.lines), gui.lines


def test_router_exception_marks_failed():
    g, gui, router, tx = _node("queue")

    def boom(*a, **k):
        raise RuntimeError("x")

    router.send_message = boom
    _send(g)
    assert gui.status[1] == 3 and "(send error)" in gui.lines


def test_direct_submit_passes_method_and_fields():
    g, gui, router, tx = _node("queue")
    mid = gui.add_chat_message(b"D" * 16, True, "[voice 2s]", status=1)
    g["_submit"](b"D" * 16, mid, "[voice]", fields={7: [9, b"x"]}, method=DIRECT, what="Audio")
    assert router.calls[0][1:] == ("[voice]", DIRECT)
    assert gui.status[mid] == 4


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("ok  ", fn.__name__)
        except Exception as e:
            failed += 1
            print("FAIL", fn.__name__, type(e).__name__, e)
    if failed:
        print("\n%d of %d send-status tests FAILED." % (failed, len(fns)))
        sys.exit(1)
    print("\nAll %d send-status tests passed." % len(fns))


if __name__ == "__main__":
    _run()
