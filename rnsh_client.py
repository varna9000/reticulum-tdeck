# rnsh client controller for the T-Deck.
#
# Owns rnsh-listener discovery (announces on the "rnsh" destination), the
# outgoing link + Channel to the listener being used, the rnsh handshake state
# machine, and the interactive session (stdin out, stdout/stderr in). The UI
# layer (ui.py) renders the terminal and captures keys; tdeck_node.py wires the
# gui.on_shell_* callbacks to the functions below.
#
# Connect flow (rnsh protocol v1, initiator):
#   recall identity -> ensure path -> OutgoingLink(sign_proofs=True) ->
#   wait ACTIVE -> (short pre-identify wait) -> link.identify() ->
#   get_channel + register message types -> send VersionInfo, await reply ->
#   send ExecuteCommand -> RUNNING (pump stdin, render stream output).
#
# Only one session is active at a time (single controller, like nomad_browser).

import time

MAX_NODES = 16
# Defaults only — the GUI passes the live geometry to connect(), derived from
# the active shell font (ui._SHELL_FONTS: 6x12 -> 53x16, 6x10 -> 53x19,
# 5x8 -> 64x24, 4x6 -> 80x32), and pushes a WindowSizeMessage via resize()
# when the font is switched. These match ui's default font.
TERM_COLS = 53          # T-Deck terminal width (chars)
TERM_ROWS = 16          # visible rows reported to the remote pty
CONNECT_PATH_WAIT = 30  # seconds to wait for a path
VERSION_WAIT_CAP = 90   # upper bound on the version-reply wait
LINK_ATTEMPTS = 4       # link-establishment retries (lossy multi-hop LoRa drops
                        # the request/proof; one dropped packet must not be fatal)

# Session states
IDLE       = 0
CONNECTING = 1
RUNNING    = 2
CLOSED     = 3

_gui = None
_my_identity = None     # node identity we identify to the listener with
_link = None
_channel = None
_dest = None            # dest_hash of the current/last session
_state = IDLE
_peer_version = None    # set when the listener's VersionInfoMessage arrives
_error = None           # (msg, fatal) from an ErrorMessage, or None
_exit_code = None
_stdin_buf = bytearray()
_task_gen = 0           # bumped per connect; stale tasks self-cancel
# Live terminal geometry. Held in module state rather than captured by
# _session_task so a font switch mid-connect still reaches the pty — see
# resize().
_term_cols = TERM_COLS
_term_rows = TERM_ROWS
_size_sent = None       # geometry the pty has actually been told about


# --- discovery --------------------------------------------------------------

def init(gui, identity):
    """Hook announce observation. Call once at boot, after Reticulum init.
    `identity` is the node's own identity (used to identify to listeners)."""
    global _gui, _my_identity
    _gui = gui
    _my_identity = identity
    from urns.transport import Transport
    Transport.register_announce_handler(_on_announce)


def _rnsh_hash(dest_hash):
    """The 'rnsh' destination hash for the identity behind dest_hash, or None."""
    from urns.identity import Identity
    from urns.destination import Destination
    data = Identity.known_destinations.get(dest_hash)
    if data and data[2]:
        id_hash = Identity.truncated_hash(data[2])
        return Destination.hash(id_hash, "rnsh")
    return None


def _node_hops(dest_hash):
    try:
        from urns.transport import Transport
        from urns import const as _uc
        entry = Transport.path_table.get(dest_hash)
        if entry:
            return entry[_uc.IDX_PT_HOPS]
    except Exception:
        pass
    return None


def _on_announce(dest_hash, app_data, packet):
    """Transport announce observer — collect 'rnsh' listener announces."""
    if _rnsh_hash(dest_hash) != dest_hash:
        return
    if _gui is not None:
        _gui.add_shell_node(dest_hash, hops=_node_hops(dest_hash))
        _gui.wake_screen()



# The SSH tab is deliberately NOT seeded from Identity.known_destinations.
# That store is persistent (it holds the public keys needed to encrypt and to
# validate announces, so it must survive a reboot), but a listener recorded in
# it days ago says nothing about whether it is reachable now. Seeding from it
# filled the tab with stale hashes that had to be tried before they could be
# discovered dead. The list is therefore session-scoped: it starts empty on
# boot and holds exactly the listeners announced since. Live announces are
# added straight to the GUI by the announce handler above, so nothing is lost
# by not seeding — including announces heard before the tab is first opened.


def clear_nodes():
    """Interface switched — reachability changed, start over."""
    disconnect()
    if _gui is not None:
        _gui.clear_shell_nodes()


# --- helpers ----------------------------------------------------------------

def _status(text):
    if _gui is not None:
        _gui.shell_status(text)


def is_active():
    return _state in (CONNECTING, RUNNING)


# --- public GUI API ---------------------------------------------------------

def connect(dest_hash, cols=TERM_COLS, rows=TERM_ROWS):
    """GUI: open an rnsh session to a listener (SSH tab click / manual hash)."""
    global _term_cols, _term_rows
    import uasyncio as asyncio
    _term_cols, _term_rows = cols, rows
    asyncio.create_task(_session_task(dest_hash))


def send_input(data):
    """GUI: queue stdin bytes for the remote shell (a keystroke or a line)."""
    if _state != RUNNING:
        return
    if isinstance(data, str):
        data = data.encode("utf-8")
    _stdin_buf.extend(data)


def _send_window_size():
    """Best-effort push of the current geometry to the pty; True once away.

    The Channel refuses sends while its window is full — routine when output
    is streaming — so a resize issued at a busy moment would be dropped and
    the pty left permanently wrong. _pump_loop retries this until _size_sent
    matches, which matters now that the font is cycled from the shell menu."""
    global _size_sent
    if _state != RUNNING or _channel is None:
        return False
    try:
        if not _channel.is_ready_to_send():
            return False
        from rnsh_proto import WindowSizeMessage
        _channel.send(WindowSizeMessage(_term_rows, _term_cols, None, None))
        _size_sent = (_term_rows, _term_cols)
        return True
    except Exception:
        return False


def resize(rows, cols):
    """GUI: report a new terminal size to the remote pty (shell font switch).

    The geometry is recorded unconditionally, before any RUNNING check: a font
    switch during the connect — 30-90s over multi-hop LoRa, an easy window to
    hit — would otherwise be dropped here while the ExecuteCommand still to be
    sent carried the pre-switch size, leaving the pty wrong for the whole
    session with no correction ever sent."""
    global _term_cols, _term_rows
    _term_cols, _term_rows = cols, rows
    _send_window_size()


def disconnect():
    """GUI: end the session and tear down the link."""
    global _task_gen
    _task_gen += 1   # invalidate any running session task
    _teardown()


# --- session lifecycle ------------------------------------------------------

def _teardown():
    global _link, _channel, _state, _stdin_buf
    link = _link
    _link = None
    _channel = None
    _state = CLOSED
    _stdin_buf = bytearray()
    if link is not None:
        try:
            link.teardown()
        except Exception:
            pass


def _on_link_closed(link):
    global _state
    if link is _link:
        _state = CLOSED
        _status("disconnected")
        if _gui is not None:
            _gui.shell_closed()


def _on_message(msg):
    """Channel message handler (runs on the event loop)."""
    global _peer_version, _error, _exit_code, _state
    from rnsh_proto import (StreamDataMessage, VersionInfoMessage,
                            CommandExitedMessage, ErrorMessage, NoopMessage)
    try:
        if isinstance(msg, StreamDataMessage):
            if msg.data and _gui is not None:
                _gui.shell_feed(msg.stream_id, bytes(msg.data))
        elif isinstance(msg, VersionInfoMessage):
            _peer_version = msg
        elif isinstance(msg, CommandExitedMessage):
            _exit_code = msg.return_code
            _state = CLOSED
            if _gui is not None:
                _gui.shell_exited(msg.return_code)
            _teardown()
        elif isinstance(msg, ErrorMessage):
            _error = (msg.msg, bool(msg.fatal))
            _status("error: " + str(msg.msg))
            if msg.fatal:
                _teardown()
                if _gui is not None:
                    _gui.shell_closed()
        elif isinstance(msg, NoopMessage):
            pass
    except Exception as e:
        _status("rx error: " + str(e))
    return True   # handled


async def _await_channel_ready(timeout):
    import uasyncio as asyncio
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _channel is not None and _channel.is_ready_to_send():
            return True
        await asyncio.sleep_ms(100)
    return False


async def _session_task(dest_hash):
    global _link, _channel, _dest, _state, _peer_version, _error, _exit_code
    global _size_sent
    import uasyncio as asyncio
    from urns.identity import Identity
    from urns.transport import Transport

    if is_active():
        _status("busy — session in progress")
        return

    my_gen = _task_gen_next()
    _dest = dest_hash
    _state = CONNECTING
    _peer_version = None
    _error = None
    _exit_code = None
    _size_sent = None

    try:
        # 1. Path + identity (path response carries the announce).
        if not Transport.has_path(dest_hash) or Identity.recall(dest_hash) is None:
            _status("finding path...")
            Transport.request_path(dest_hash)
            for _ in range(CONNECT_PATH_WAIT):
                await asyncio.sleep(1)
                if _stale(my_gen):
                    return
                if Transport.has_path(dest_hash) and Identity.recall(dest_hash):
                    break
            else:
                _status("no path to listener")
                _state = CLOSED
                return
        identity = Identity.recall(dest_hash)
        if identity is None:
            _status("unknown identity")
            _state = CLOSED
            return

        # 2. Link (sign_proofs=True: rnsh needs real packet proofs for Channel).
        #    Retry: over multi-hop lossy LoRa a single dropped link-request or
        #    proof means no link at all. Each attempt re-sends the request (and
        #    refreshes a possibly-degraded path), so one lost packet isn't fatal.
        from urns.link import OutgoingLink
        from urns.destination import Destination
        dst = Destination(identity, Destination.OUT, Destination.SINGLE, "rnsh")
        hops = max(1, Transport.hops_to(dest_hash))
        per_attempt = min(90, max(40, 14 * hops))   # ~one multi-hop round trip
        link = None
        for attempt in range(1, LINK_ATTEMPTS + 1):
            if _stale(my_gen):
                return
            _status("linking..." if attempt == 1
                    else "linking retry %d/%d..." % (attempt, LINK_ATTEMPTS))
            if attempt > 1:
                Transport.request_path(dest_hash)   # refresh the path between tries
                await asyncio.sleep(1)
            await asyncio.sleep_ms(50)              # let the status row paint before keygen
            link = OutgoingLink(dst, closed_callback=_on_link_closed, sign_proofs=True)
            _link = link
            t0 = time.time()
            while link.status == OutgoingLink.PENDING and time.time() - t0 < per_attempt:
                await asyncio.sleep_ms(200)
                if _stale(my_gen):
                    return
            if link.status == OutgoingLink.ACTIVE:
                break
            try:
                link.teardown()          # drop the dead attempt before retrying
            except Exception:
                pass
            _link = None
            link = None
        if link is None or link.status != OutgoingLink.ACTIVE:
            _status("link failed (no reply after %d tries)" % LINK_ATTEMPTS)
            _teardown()
            return

        # 3. Short pre-identify wait (capped — over LoRa the reference's rtt*10
        #    would overshoot the listener's own state watchdog). One link
        #    latency is enough for the listener's link to go ACTIVE.
        rtt = getattr(link, "rtt", 0) or 0
        await asyncio.sleep(min(max(rtt * 1.5, 0.1), rtt + 2))
        if _stale(my_gen):
            return

        # 4. Identify (rnsh listener authorizes our identity).
        if _my_identity is not None:
            link.identify(_my_identity)

        # 5. Channel + message types + handler.
        import rnsh_proto
        ch = link.get_channel()
        rnsh_proto.register_all(ch)
        ch.add_message_handler(_on_message)
        _channel = ch

        # 6. Version exchange — we send first, then await the reply.
        _status("handshake...")
        if not await _await_channel_ready(10):
            _status("channel not ready")
            _teardown()
            return
        ch.send(rnsh_proto.VersionInfoMessage())
        vwait = min(max(rtt * 20, 10), VERSION_WAIT_CAP)
        t0 = time.time()
        while _peer_version is None and time.time() - t0 < vwait:
            await asyncio.sleep_ms(200)
            if _stale(my_gen):
                return
            if _error is not None and _error[1]:
                return   # fatal error already handled
        if _peer_version is None:
            _status("no version reply")
            _teardown()
            return
        if _peer_version.protocol_version != rnsh_proto.PROTOCOL_VERSION:
            _status("incompatible protocol v" + str(_peer_version.protocol_version))
            _teardown()
            return

        # 7. Execute the remote default shell on a pty (all output on stream 1).
        if not await _await_channel_ready(10):
            _status("channel not ready")
            _teardown()
            return
        # Read the geometry now, not at connect time: the font may have been
        # switched while the link was coming up.
        ch.send(rnsh_proto.ExecuteCommandMessage(
            cmdline=None, pipe_stdin=False, pipe_stdout=False, pipe_stderr=False,
            tcflags=None, term="vt100", rows=_term_rows, cols=_term_cols,
            hpix=None, vpix=None))
        _size_sent = (_term_rows, _term_cols)   # ExecuteCommand carried it

        # 8. RUNNING: stdin pump; output arrives via _on_message.
        _state = RUNNING
        _status(None)
        if _gui is not None:
            _gui.shell_connected()
        await _pump_loop(my_gen)

    except Exception as e:
        _status("error: " + str(e))
        _teardown()


async def _pump_loop(my_gen):
    """Drain the stdin buffer into StreamDataMessages when the channel is ready.
    Char-at-a-time or a whole line: whatever is buffered, up to one message."""
    global _stdin_buf
    import uasyncio as asyncio
    from rnsh_proto import StreamDataMessage
    while _state == RUNNING and not _stale(my_gen):
        # Retry a geometry change the channel wasn't ready to take.
        if _size_sent != (_term_rows, _term_cols):
            _send_window_size()
        if _stdin_buf and _channel is not None and _channel.is_ready_to_send():
            # One message per ready slot; cap to the channel MDU minus the
            # 2-byte stream header (leave margin — rnsh uses mdu-8).
            limit = max(1, _channel.mdu - 8)
            chunk = bytes(_stdin_buf[:limit])
            # Reassign rather than `del buf[:n]` / `buf[:0]=` — MicroPython
            # bytearray supports neither slice deletion nor slice assignment.
            _stdin_buf = _stdin_buf[len(chunk):]
            try:
                _channel.send(StreamDataMessage(StreamDataMessage.STREAM_ID_STDIN, chunk))
            except Exception:
                # Not ready after all — put the bytes back, retry next tick.
                _stdin_buf = bytearray(chunk) + _stdin_buf
                await asyncio.sleep_ms(100)
                continue
        await asyncio.sleep_ms(30)


# --- task-generation guards (single active session) -------------------------

def _task_gen_next():
    global _task_gen
    _task_gen += 1
    return _task_gen


def _stale(my_gen):
    return my_gen != _task_gen
