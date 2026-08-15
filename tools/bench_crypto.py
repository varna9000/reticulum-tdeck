# Ed25519/X25519 speed check — proves the crypto is running from IRAM.
#
# Run on the device with:
#     mpremote run tools/bench_crypto.py
# (`run` executes over the raw REPL, so it does not disturb main.py.)
#
# Why this exists: the firmware used to reach IRAM speed by loading a natmod
# (lib/ed25519_iram.mpy) off the filesystem. An M5Launcher install has no
# filesystem to load it from, so Monocypher is now placed in IRAM at link time
# (tools/c_modules/ed25519_fast/linker.lf, `noflash` scheme) and the .mpy is
# gone. This benchmark is the acceptance test for that change.
#
# Reference numbers measured on this board (T-Deck, ESP32-S3, 240MHz):
#     IRAM natmod   : verify ~17.6 ms      <- target
#     flash XIP     : verify ~1461 ms      <- ~83x slower, the regression
# Anything in the tens of ms is IRAM-class and passes. Hundreds of ms or more
# means the linker fragment did not take effect.

import time

try:
    from urns.crypto import ed25519 as e
except ImportError:
    import sys
    sys.path.insert(0, "/")
    from urns.crypto import ed25519 as e

mod = e._native
print("native module :", mod.__name__ if mod else "NONE (pure Python!)")
if mod is None:
    raise SystemExit("FAIL: no native crypto module — pure-Python fallback is in use")
if mod.__name__ == "ed25519_iram":
    print("NOTE: the .mpy natmod is still on the filesystem and took priority;")
    print("      delete /lib/ed25519_iram.mpy to measure the built-in.")

SEED = bytes(range(32))
MSG = b"reticulum benchmark message" * 4
N = 20


def bench(label, fn, n=N):
    fn()                       # warm up (first call may fault pages in)
    t0 = time.ticks_ms()
    for _ in range(n):
        fn()
    el = time.ticks_diff(time.ticks_ms(), t0)
    print("%-22s %7.2f ms/op  (%d ops in %d ms)" % (label, el / n, n, el))
    return el / n


pub = mod.publickey(SEED)
sig = mod.sign(MSG, SEED)
assert mod.verify(sig, MSG, pub), "signature self-check failed"

print("--- Ed25519 / X25519 ---")
bench("publickey", lambda: mod.publickey(SEED))
bench("sign", lambda: mod.sign(MSG, SEED))
v = bench("verify", lambda: mod.verify(sig, MSG, pub))
priv = bytes(range(1, 33))
bench("x25519", lambda: mod.x25519(priv, pub))

print("---")
if v < 60:
    print("PASS: %.1f ms verify is IRAM-class (flash XIP would be ~1461 ms)" % v)
elif v < 300:
    print("MARGINAL: %.1f ms — faster than XIP but short of the ~17.6 ms target" % v)
else:
    print("FAIL: %.1f ms verify — this is flash-XIP speed, IRAM placement did NOT take" % v)
