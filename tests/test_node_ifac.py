# The node's pure IFAC helpers (tdeck_node.py), compiled out of the module
# the way test_attachments.py does it, since importing tdeck_node brings up
# the radio. Run:  python3 tests/test_node_ifac.py

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    with open(os.path.join(ROOT, "tdeck_node.py")) as f:
        tree = ast.parse(f.read())
    funcs = {"_ifac_overlay", "_ifac_from_settings"}
    consts = {"_IFAC_KEYS"}
    body = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in funcs:
            body.append(node)
        elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in consts for t in node.targets):
            body.append(node)
    assert len(body) == len(funcs) + len(consts), \
        "IFAC helpers moved or were renamed -- update this test"
    ns = {"DEBUG": 0}
    exec(compile(ast.Module(body=body, type_ignores=[]), "tdeck_node.py", "exec"), ns)
    return ns


N = _load()


def test_overlay_sets_the_urns_keys():
    cfg = {"type": "TCPClientInterface"}
    N["_ifac_overlay"](cfg, {"networkname": "mesh", "passphrase": "pw", "ifac_size": 8})
    assert cfg["networkname"] == "mesh" and cfg["passphrase"] == "pw" and cfg["ifac_size"] == 8


def test_overlay_drops_blank_fields_so_urns_sees_them_unset():
    cfg = {"networkname": "old", "passphrase": "old", "ifac_size": 16}
    N["_ifac_overlay"](cfg, {"networkname": "", "passphrase": "", "ifac_size": 16})
    assert "networkname" not in cfg and "passphrase" not in cfg
    assert "ifac_size" not in cfg             # no code, no size


def test_overlay_with_none_clears_everything():
    cfg = {"networkname": "old", "ifac_size": 8}
    N["_ifac_overlay"](cfg, None)
    assert cfg == {}


def test_overlay_name_alone_keeps_the_size():
    cfg = {}
    N["_ifac_overlay"](cfg, {"networkname": "mesh", "passphrase": "", "ifac_size": 8})
    assert cfg == {"networkname": "mesh", "ifac_size": 8}


def test_overlay_without_a_size_leaves_it_to_the_interface_default():
    cfg = {"ifac_size": 16}
    N["_ifac_overlay"](cfg, {"networkname": "mesh", "passphrase": ""})
    assert cfg == {"networkname": "mesh"}


def test_from_settings_reads_the_tcp_keys_with_defaults():
    assert N["_ifac_from_settings"]({}) == {"networkname": "", "passphrase": "", "ifac_size": 16}
    s = {"tcp_netname": "mesh", "tcp_passphrase": "pw", "tcp_ifac_size": 8}
    assert N["_ifac_from_settings"](s) == {"networkname": "mesh", "passphrase": "pw", "ifac_size": 8}


def _run():
    import traceback
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS  " + name)
        except Exception as e:
            failed += 1
            print("FAIL  " + name + "  ->  " + repr(e))
            traceback.print_exc()
    print("\n%d/%d passed" % (len(tests) - failed, len(tests)))
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
