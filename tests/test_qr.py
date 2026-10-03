# qr.py builds one QR symbol: version 8, level L, byte mode, mask 0 -- the
# shape of the device's own "lxma://<addr>:<pubkey>" contact URI. The expected
# grid below was produced by the python-qrcode library for the same input and
# settings, and the same output was read back by a real decoder (zxing-cpp)
# when this was written; neither is needed to run the test. Run:
#   python3 tests/test_qr.py

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import qr

TEXT = ("lxma://7c9e21b4d05f3a6e8812c4f09ab3d7e5:"
        "94c9c5d7d68670ef932201f2950f4a272b7111d7c39ac2d961456c7ad9ba7d0b"
        "deea4e95ef0ba9eae0849435547165b21092fe00e72838dd7f76d00c9f452171")

# 49 rows of 49 modules, each row as 13 hex digits, leftmost module = top bit
EXPECTED = (
    "1fcd997ec717f",
    "1046447cc4741",
    "175e01c24435d",
    "174044c240a5d",
    "174e557c7f85d",
    "104408c47f441",
    "1fd555555557f",
    "0014e24531e00",
    "1df466fdf5ec4",
    "02bc660ab8229",
    "03d756cb9b33d",
    "1185009319298",
    "1b785593d10d9",
    "0ca911381a033",
    "1074445b1921b",
    "048f119b91210",
    "19614e9b59cd2",
    "1c064e9a8908f",
    "0e5d885b89093",
    "1286ee133929b",
    "054cbb1b79e11",
    "001caa3aa3a83",
    "1bfcff7db39f7",
    "0913aa479991a",
    "155a64575df58",
    "131c64c62a317",
    "01f664fc1b1fb",
    "0eac22c513d48",
    "055e558597500",
    "128e118d0aa09",
    "1b7accd70b977",
    "0f9215e511142",
    "03624ecdd1cc2",
    "04294e8d88b85",
    "0054bafd988f9",
    "0fadeced11cfa",
    "1dc7b925595cb",
    "15baaaafb190b",
    "08d3f797818f3",
    "0e0ae6a5118c2",
    "1c70c67d9d5f0",
    "0010c6c6b2b13",
    "1fda74d49395b",
    "105c32c513b10",
    "1751567d1fdf1",
    "174d12528bb13",
    "17544654b9b54",
    "105d542999c92",
    "1fda665911793",
)


def _rows(mod):
    return tuple("%013x" % int("".join(str(b) for b in mod[y * 49:(y + 1) * 49]), 2)
                 for y in range(49))


def test_contact_uri_matches_the_reference_grid():
    assert len(TEXT) == 168
    mod = qr.encode(TEXT)
    assert len(mod) == qr.SIZE * qr.SIZE == 2401
    got = _rows(mod)
    bad = [y for y in range(49) if got[y] != EXPECTED[y]]
    assert not bad, "rows differ: %r" % bad


def test_str_and_bytes_encode_the_same():
    assert qr.encode(TEXT) == qr.encode(TEXT.encode())


def test_finder_patterns_and_quiet_data():
    mod = qr.encode(TEXT)
    finder = ("1111111", "1000001", "1011101", "1011101", "1011101", "1000001", "1111111")
    for ox, oy in ((0, 0), (42, 0), (0, 42)):
        for y in range(7):
            row = "".join(str(mod[(oy + y) * 49 + ox + x]) for x in range(7))
            assert row == finder[y], (ox, oy, y, row)
    assert set(mod) == {0, 1}


def test_capacity_is_192_bytes():
    qr.encode(b"x" * 192)
    try:
        qr.encode(b"x" * 193)
    except ValueError:
        return
    raise AssertionError("193 bytes should not fit")


def test_different_text_gives_a_different_grid():
    assert qr.encode(TEXT) != qr.encode(TEXT[:-1] + "0")


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
        print("\n%d of %d qr tests FAILED." % (failed, len(fns)))
        sys.exit(1)
    print("\nAll %d qr tests passed." % len(fns))


if __name__ == "__main__":
    _run()
