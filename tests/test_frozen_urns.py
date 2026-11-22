# The frozen firmware carries every urns module the deck can import, and the
# built-in ed25519 C module exports everything the urns natmod does.
#
# Both gaps are silent until first use: urns imports its stamp modules lazily
# (first stamped announce / path request / link), and crypto/ed25519.py simply
# falls back to slower limits when the native module lacks hashcash or
# x25519_stamp2. Found on a T-Deck Pro, 2026-11-22, when the uP-reticulum
# rns-161-stamps update added four modules and seven native functions.
#
# Run:  python3 tests/test_frozen_urns.py

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FW = os.path.join(ROOT, "vendor", "uP-reticulum", "firmware")
NATMOD = os.path.join(ROOT, "vendor", "uP-reticulum", "tools", "natmod", "ed25519_fast", "main.c")
BUILTIN = os.path.join(ROOT, "tools", "c_modules", "ed25519_fast", "ed25519_fast_module.c")
MANIFEST = os.path.join(ROOT, "tools", "tdeck_manifest.py")

# Deliberately left out (see the manifest): no E32 or serial KISS link on a deck.
NOT_FROZEN = {"urns/interfaces/e32.py", "urns/interfaces/serial.py"}

failures = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else "  " + str(detail)))
    if not ok:
        failures.append(name)


def test_every_urns_module_is_frozen():
    man = open(MANIFEST).read()
    frozen = set(re.findall(r'freeze\(_vendor, "([^"]+)"\)', man))
    have = set()
    for d, _, files in os.walk(os.path.join(FW, "urns")):
        for f in files:
            if f.endswith(".py"):
                have.add(os.path.relpath(os.path.join(d, f), FW).replace(os.sep, "/"))
    missing = sorted(have - frozen - NOT_FROZEN)
    check("every urns module is frozen (except e32/serial)", not missing, missing)
    stale = sorted(m for m in frozen if m.startswith("urns/") and m not in have)
    check("the manifest names no urns module that no longer exists", not stale, stale)


def test_builtin_exports_what_the_natmod_exports():
    nat = set(re.findall(r"mp_store_global\(MP_QSTR_(\w+),", open(NATMOD).read()))
    blt = set(re.findall(r"MP_ROM_QSTR\(MP_QSTR_(\w+)\), MP_ROM_PTR", open(BUILTIN).read()))
    missing = sorted(nat - blt)
    check("built-in ed25519 module exports every natmod function", not missing, missing)
    for fn in ("hashcash", "x25519_stamp2", "x25519_stamp2_init", "hwselftest", "hwprobe", "hwstats"):
        check("built-in exports " + fn, fn in blt)


def test_filesystem_natmods_match_the_submodule():
    # The manual (stock MicroPython) setup and tools/gen_bootstrap.py copy lib/*.mpy to the
    # device. lib/ed25519_fast_xtensawin.mpy was a 2026 build without hashcash/x25519_stamp2
    # until v1.16.0, so a deck set up that way silently had no network stamps.
    for m in ("ed25519_fast_xtensawin.mpy", "bz2_fast_xtensawin.mpy"):
        ours = open(os.path.join(ROOT, "lib", m), "rb").read()
        theirs = open(os.path.join(FW, "lib", m), "rb").read()
        check("lib/%s is the submodule's build" % m, ours == theirs)


if __name__ == "__main__":
    test_every_urns_module_is_frozen()
    test_builtin_exports_what_the_natmod_exports()
    test_filesystem_natmods_match_the_submodule()
    if failures:
        print("\n%d FAILED" % len(failures))
        sys.exit(1)
    print("\nall ok")
