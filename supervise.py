# supervise.py -- keep the UI's asyncio tasks alive, and say why they died.
#
# When a task such as the drawing loop raises, asyncio prints the traceback
# to the console and drops the task. On a deck with nobody on the console
# that looks like a freeze: the backlight comes on, nothing repaints, keys
# appear dead (seen on a v1, 2026-10-10). run() wraps a task: on an
# exception the traceback is appended to CRASH_PATH on the device, the loop
# waits RESTART_MS and starts the task again, so the UI recovers and the
# cause survives for `mpremote cat /rns/crash.txt` later.
#
# A task that returns is restarted too (the UI loops never return by
# design), with the same pause, so a bug that makes one exit does not spin.

import sys

CRASH_PATH = "/rns/crash.txt"
CRASH_MAX = 6000     # bytes kept; older entries are trimmed from the front
RESTART_MS = 1000


def _exc_text(e):
    try:
        import io
        buf = io.StringIO()
        sys.print_exception(e, buf)          # MicroPython
        return buf.getvalue()
    except AttributeError:
        import traceback                     # CPython (host tests)
        return "".join(traceback.format_exception(type(e), e, e.__traceback__))


def report(name, e):
    """Append one crash entry; never raise (the caller is already in trouble)."""
    try:
        import time
        entry = "--- %s task %s: %r\n%s" % (time.time(), name, e, _exc_text(e))
        try:
            with open(CRASH_PATH) as f:
                old = f.read()
        except OSError:
            old = ""
        body = old + entry
        if len(body) > CRASH_MAX:
            body = body[-CRASH_MAX:]
            cut = body.find("\n--- ")
            if cut >= 0:
                body = body[cut + 1:]
        with open(CRASH_PATH, "w") as f:
            f.write(body)
    except Exception:
        pass


async def run(name, factory):
    """Run factory() (a fresh coroutine each time) forever, reporting and
    restarting on any exception or return."""
    import uasyncio as asyncio
    while True:
        try:
            await factory()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            report(name, e)
            try:
                sys.print_exception(e)
            except AttributeError:
                pass
        await asyncio.sleep_ms(RESTART_MS)
