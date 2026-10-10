# A UI task that dies takes the screen or the keyboard with it, silently:
# asyncio prints the traceback to a console nobody is watching and the deck
# looks frozen (seen on a v1 on 2026-10-10: backlight on, no repaint, keys
# "dead"). supervise.run() wraps a task: when it raises, the traceback is
# appended to a file on the device and the task is started again after a
# short pause, so the UI recovers and the cause survives for later reading.
#
# Run:  python3 tests/test_supervise.py

import asyncio
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# MicroPython's uasyncio on the host: CPython asyncio plus sleep_ms.
_ua = types.ModuleType("uasyncio")
_ua.sleep = asyncio.sleep
_ua.sleep_ms = lambda ms: asyncio.sleep(ms / 1000)
_ua.create_task = asyncio.create_task
_ua.CancelledError = asyncio.CancelledError
sys.modules["uasyncio"] = _ua

import supervise  # noqa: E402

_failures = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        _failures.append(name)


tmp = tempfile.mkdtemp()
supervise.CRASH_PATH = os.path.join(tmp, "crash.txt")
supervise.RESTART_MS = 10


async def scenario_restart():
    runs = []

    def factory():
        async def task():
            runs.append(len(runs))
            if len(runs) == 1:
                raise ValueError("boom on first run")
            await asyncio.sleep(0.05)
        return task()

    sup = asyncio.create_task(supervise.run("gui", factory))
    await asyncio.sleep(0.2)
    sup.cancel()
    try:
        await sup
    except asyncio.CancelledError:
        pass
    return runs


async def scenario_clean_exit():
    runs = []

    def factory():
        async def task():
            runs.append(1)
        return task()

    sup = asyncio.create_task(supervise.run("kbd", factory))
    await asyncio.sleep(0.1)
    sup.cancel()
    try:
        await sup
    except asyncio.CancelledError:
        pass
    return runs


print("a crashing task is logged and restarted")
runs = asyncio.run(scenario_restart())
check("task ran again after the crash", len(runs) >= 2, runs)
check("crash file written", os.path.exists(supervise.CRASH_PATH))
body = open(supervise.CRASH_PATH).read() if os.path.exists(supervise.CRASH_PATH) else ""
check("crash file names the task", "gui" in body, body[:200])
check("crash file holds the exception", "boom on first run" in body, body[:200])
check("crash file holds a traceback", "Traceback" in body or "File" in body, body[:200])

print("a task that returns cleanly is not restarted in a tight loop")
runs = asyncio.run(scenario_clean_exit())
check("re-run after RESTART_MS, not more than a handful of times", 1 <= len(runs) <= 20, len(runs))

print("crash entries accumulate, newest last, bounded")
os.remove(supervise.CRASH_PATH)
for i in range(3):
    supervise.report("t%d" % i, ValueError("err%d" % i))
body = open(supervise.CRASH_PATH).read()
check("three entries", body.count("\n--- ") + body.startswith("--- ") == 3, body.count("--- "))
check("ordered", body.index("err0") < body.index("err2"))
supervise.CRASH_MAX = 200
for i in range(50):
    supervise.report("t", ValueError("x" * 40))
check("file is trimmed to about CRASH_MAX", os.path.getsize(supervise.CRASH_PATH) <= supervise.CRASH_MAX * 2,
      os.path.getsize(supervise.CRASH_PATH))

if _failures:
    print("\n%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
    sys.exit(1)
print("\nall ok")
