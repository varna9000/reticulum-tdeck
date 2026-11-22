# STAMP_ON_BATTERY: background announce-stamp generation runs on battery unless the
# board config says False. The stamp is what lets an enforcing (RNS 1.6.2) hub pass
# the deck's announces on, so a deck that is never plugged in must make it too.
# Lifts the module-level block out of tdeck_node.py (it starts hardware at import).
#
# Run:  python3 tests/test_stamp_battery.py

import ast
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
failures = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else "  " + str(detail)))
    if not ok:
        failures.append(name)


def _block():
    tree = ast.parse(open(os.path.join(ROOT, "tdeck_node.py")).read())
    nodes = [n for n in tree.body if isinstance(n, ast.If) and "STAMP_ON_BATTERY" in ast.unparse(n.test)]
    assert len(nodes) == 1, len(nodes)
    return compile(ast.Module(body=nodes, type_ignores=[]), "tdeck_node.py", "exec")


def _run(config):
    stamps = types.ModuleType("urns.announce_stamps")
    stamps.hold = None
    urns = types.ModuleType("urns")
    urns.announce_stamps = stamps
    sys.modules["urns"] = urns
    sys.modules["urns.announce_stamps"] = stamps
    exec(_block(), {"config": config, "_on_ext_power": lambda: False})
    return stamps.hold


def test_default_generates_on_battery():
    check("no STAMP_ON_BATTERY in the config (an existing deck's file): no hold",
          _run(types.SimpleNamespace()) is None)
    check("STAMP_ON_BATTERY = True: no hold", _run(types.SimpleNamespace(STAMP_ON_BATTERY=True)) is None)


def test_false_restricts_to_usb_power():
    hold = _run(types.SimpleNamespace(STAMP_ON_BATTERY=False))
    check("STAMP_ON_BATTERY = False: generation held off USB power", callable(hold) and hold() is True)


def test_both_configs_carry_the_setting_on():
    for f in ("tdeck_config.py", "tdeck_pro_config.py"):
        src = open(os.path.join(ROOT, f)).read()
        check(f + " sets STAMP_ON_BATTERY = True", "\nSTAMP_ON_BATTERY = True" in src)


if __name__ == "__main__":
    test_default_generates_on_battery()
    test_false_restricts_to_usb_power()
    test_both_configs_carry_the_setting_on()
    print("\n%s" % ("all ok" if not failures else "%d FAILED" % len(failures)))
    sys.exit(1 if failures else 0)
