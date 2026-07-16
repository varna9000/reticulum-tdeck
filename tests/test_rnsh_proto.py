# Host-side tests for rnsh_proto.py — message pack/unpack vs known-good bytes,
# StreamData header bit math, and bz2 decompress on receive.
# Bootstraps urns via the submodule's firmware test harness.
# Run:  python3 tests/test_rnsh_proto.py

import os
import sys
import struct
import bz2

HERE = os.path.dirname(os.path.abspath(__file__))          # repo/tests
REPO = os.path.dirname(HERE)                                # repo root
FW_TESTS = os.path.join(REPO, "vendor", "uP-reticulum", "firmware", "tests")

sys.path.insert(0, FW_TESTS)
import harness  # noqa: F401  (installs shims + synthetic urns package)
sys.path.insert(0, REPO)

import rnsh_proto as P
import importlib
channel = importlib.import_module("urns.channel")
umsgpack = importlib.import_module("urns.umsgpack")


def test_msgtypes():
    assert P.NoopMessage.MSGTYPE == 0xAC00
    assert P.WindowSizeMessage.MSGTYPE == 0xAC02
    assert P.ExecuteCommandMessage.MSGTYPE == 0xAC03
    assert P.StreamDataMessage.MSGTYPE == 0xAC04
    assert P.VersionInfoMessage.MSGTYPE == 0xAC05
    assert P.ErrorMessage.MSGTYPE == 0xAC06
    assert P.CommandExitedMessage.MSGTYPE == 0xAC07
    print("ok test_msgtypes")


def test_versioninfo_known_bytes():
    # Reference wire: fixarray-2, fixstr "0.1.7", posfixint 1
    raw = P.VersionInfoMessage("0.1.7", 1).pack()
    assert raw == b"\x92\xa50.1.7\x01", raw
    m = P.VersionInfoMessage()
    m.unpack(raw)
    assert m.sw_version == "0.1.7" and m.protocol_version == 1
    print("ok test_versioninfo_known_bytes")


def test_execute_command_roundtrip():
    m = P.ExecuteCommandMessage(cmdline=None, pipe_stdin=False, pipe_stdout=False,
                                pipe_stderr=False, tcflags=None, term="vt100",
                                rows=14, cols=40, hpix=None, vpix=None)
    raw = m.pack()
    # 10-element msgpack array
    decoded = umsgpack.unpackb(raw)
    assert isinstance(decoded, list) and len(decoded) == 10, decoded
    assert decoded[0] is None and decoded[5] == "vt100" and decoded[6] == 14 and decoded[7] == 40
    m2 = P.ExecuteCommandMessage()
    m2.unpack(raw)
    assert m2.term == "vt100" and m2.rows == 14 and m2.cols == 40 and m2.cmdline is None
    print("ok test_execute_command_roundtrip")


def test_streamdata_header_bits():
    # stdin, no flags
    m = P.StreamDataMessage(P.StreamDataMessage.STREAM_ID_STDIN, b"ls\n")
    raw = m.pack()
    assert struct.unpack(">H", raw[:2])[0] == 0, raw
    assert raw[2:] == b"ls\n"
    # stdout + EOF
    m = P.StreamDataMessage(P.StreamDataMessage.STREAM_ID_STDOUT, b"", eof=True)
    hdr = struct.unpack(">H", m.pack()[:2])[0]
    assert hdr == (0x8000 | 1), hex(hdr)
    # stderr + compressed flag
    m = P.StreamDataMessage(P.StreamDataMessage.STREAM_ID_STDERR, b"x", compressed=True)
    hdr = struct.unpack(">H", m.pack()[:2])[0]
    assert hdr == (0x4000 | 2), hex(hdr)
    print("ok test_streamdata_header_bits")


def test_streamdata_unpack_flags():
    m = P.StreamDataMessage()
    m.unpack(struct.pack(">H", 0x8000 | 1) + b"done\n")
    assert m.stream_id == 1 and m.eof is True and m.compressed is False
    assert m.data == b"done\n"
    print("ok test_streamdata_unpack_flags")


def test_streamdata_bz2_decompress():
    payload = b"drwxr-xr-x  6 user staff  192 main.py\n" * 20   # compressible
    comp = bz2.compress(payload)
    assert len(comp) < len(payload)
    raw = struct.pack(">H", 0x4000 | P.StreamDataMessage.STREAM_ID_STDOUT) + comp
    m = P.StreamDataMessage()
    m.unpack(raw)
    assert m.stream_id == 1
    assert m.data == payload, (len(m.data), len(payload))
    assert m.compressed is False   # cleared after decompress
    print("ok test_streamdata_bz2_decompress")


def test_command_exited_and_error():
    raw = P.CommandExitedMessage(0).pack()
    assert umsgpack.unpackb(raw) == 0
    m = P.CommandExitedMessage()
    m.unpack(P.CommandExitedMessage(137).pack())
    assert m.return_code == 137

    e = P.ErrorMessage("Identity is not allowed.", True, None)
    m2 = P.ErrorMessage()
    m2.unpack(e.pack())
    assert m2.msg == "Identity is not allowed." and m2.fatal is True and m2.data is None
    print("ok test_command_exited_and_error")


def test_channel_registration_and_delivery():
    # Full path: register all types, round-trip a message through a Channel.
    class _Outlet:
        rtt = 0.1
        mdu = 419
        is_usable = True
    ch = channel.Channel(_Outlet())
    P.register_all(ch)
    got = []
    ch.add_message_handler(lambda m: got.append(m) or False)
    env = channel.Envelope(None, message=P.VersionInfoMessage("9.9", 1), sequence=0)
    ch._receive(env.pack())
    assert len(got) == 1 and isinstance(got[0], P.VersionInfoMessage)
    assert got[0].sw_version == "9.9" and got[0].protocol_version == 1
    print("ok test_channel_registration_and_delivery")


if __name__ == "__main__":
    for name in list(globals()):
        if name.startswith("test_"):
            globals()[name]()
    print("all rnsh_proto tests passed")
