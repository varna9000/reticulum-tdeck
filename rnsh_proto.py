# rnsh wire-protocol messages for the T-Deck rnsh client.
#
# These are the 7 message classes exchanged over an RNS Channel with an rnsh
# listener (github.com/acehoss/rnsh). Each rides a Channel Envelope; the client
# only ever *sends* VersionInfo / ExecuteCommand / StreamData(stdin) /
# WindowSize, and *receives* VersionInfo / StreamData(stdout,stderr) /
# CommandExited / Error / Noop.
#
# Wire facts (rnsh protocol v1): MSGTYPE = 0xAC00 | n; msgpack payloads use
# urns.umsgpack (tuples serialize as arrays). StreamDataMessage uses a 2-byte
# big-endian header (bit15 EOF, bit14 compressed, bits13..0 stream_id) then raw
# data (a complete bz2 stream when compressed). We always send uncompressed;
# the listener may compress its output, so we decompress on receive.

import struct
from urns.channel import MessageBase
from urns import umsgpack

MSG_MAGIC = 0xac
PROTOCOL_VERSION = 1
SW_VERSION = "0.1.0-tdeck"   # informational only; listener ignores it


def _msgtype(n):
    return ((MSG_MAGIC << 8) & 0xff00) | (n & 0x00ff)


class NoopMessage(MessageBase):
    MSGTYPE = _msgtype(0)

    def pack(self):
        return b""

    def unpack(self, raw):
        pass


class WindowSizeMessage(MessageBase):
    MSGTYPE = _msgtype(2)

    def __init__(self, rows=None, cols=None, hpix=None, vpix=None):
        self.rows = rows
        self.cols = cols
        self.hpix = hpix
        self.vpix = vpix

    def pack(self):
        return umsgpack.packb([self.rows, self.cols, self.hpix, self.vpix])

    def unpack(self, raw):
        self.rows, self.cols, self.hpix, self.vpix = umsgpack.unpackb(raw)


class ExecuteCommandMessage(MessageBase):
    # rnsh spells the class "ExecuteCommandMesssage" (3 s's); only the MSGTYPE
    # is on the wire, so we use the correct spelling locally.
    MSGTYPE = _msgtype(3)

    def __init__(self, cmdline=None, pipe_stdin=False, pipe_stdout=False,
                 pipe_stderr=False, tcflags=None, term=None,
                 rows=None, cols=None, hpix=None, vpix=None):
        self.cmdline = cmdline
        self.pipe_stdin = pipe_stdin
        self.pipe_stdout = pipe_stdout
        self.pipe_stderr = pipe_stderr
        self.tcflags = tcflags
        self.term = term
        self.rows = rows
        self.cols = cols
        self.hpix = hpix
        self.vpix = vpix

    def pack(self):
        return umsgpack.packb([self.cmdline, self.pipe_stdin, self.pipe_stdout,
                               self.pipe_stderr, self.tcflags, self.term,
                               self.rows, self.cols, self.hpix, self.vpix])

    def unpack(self, raw):
        (self.cmdline, self.pipe_stdin, self.pipe_stdout, self.pipe_stderr,
         self.tcflags, self.term, self.rows, self.cols, self.hpix,
         self.vpix) = umsgpack.unpackb(raw)


class StreamDataMessage(MessageBase):
    MSGTYPE = _msgtype(4)
    STREAM_ID_STDIN  = 0
    STREAM_ID_STDOUT = 1
    STREAM_ID_STDERR = 2
    MAX_STREAM_ID    = 0x3fff

    def __init__(self, stream_id=None, data=None, eof=False, compressed=False):
        self.stream_id = stream_id
        self.data = data if data is not None else b""
        self.eof = eof
        self.compressed = compressed

    def pack(self):
        header = ((self.MAX_STREAM_ID & self.stream_id)
                  | (0x8000 if self.eof else 0x0000)
                  | (0x4000 if self.compressed else 0x0000))
        return struct.pack(">H", header) + (self.data if self.data else b"")

    def unpack(self, raw):
        header = struct.unpack(">H", raw[:2])[0]
        self.eof = (header & 0x8000) > 0
        self.compressed = (header & 0x4000) > 0
        self.stream_id = header & self.MAX_STREAM_ID
        self.data = raw[2:]
        if self.compressed and self.data:
            from urns import bz2dec
            self.data = bz2dec.decompress(bytes(self.data))
            self.compressed = False


class VersionInfoMessage(MessageBase):
    MSGTYPE = _msgtype(5)

    def __init__(self, sw_version=None, protocol_version=None):
        self.sw_version = sw_version if sw_version is not None else SW_VERSION
        self.protocol_version = (protocol_version if protocol_version is not None
                                 else PROTOCOL_VERSION)

    def pack(self):
        return umsgpack.packb([self.sw_version, self.protocol_version])

    def unpack(self, raw):
        self.sw_version, self.protocol_version = umsgpack.unpackb(raw)


class ErrorMessage(MessageBase):
    MSGTYPE = _msgtype(6)

    def __init__(self, msg=None, fatal=False, data=None):
        self.msg = msg
        self.fatal = fatal
        self.data = data

    def pack(self):
        return umsgpack.packb([self.msg, self.fatal, self.data])

    def unpack(self, raw):
        self.msg, self.fatal, self.data = umsgpack.unpackb(raw)


class CommandExitedMessage(MessageBase):
    MSGTYPE = _msgtype(7)

    def __init__(self, return_code=None):
        self.return_code = return_code

    def pack(self):
        return umsgpack.packb(self.return_code)

    def unpack(self, raw):
        self.return_code = umsgpack.unpackb(raw)


ALL_MESSAGE_TYPES = [
    NoopMessage, WindowSizeMessage, ExecuteCommandMessage, StreamDataMessage,
    VersionInfoMessage, ErrorMessage, CommandExitedMessage,
]


def register_all(channel):
    """Register every rnsh message type with a Channel."""
    for cls in ALL_MESSAGE_TYPES:
        channel.register_message_type(cls)
