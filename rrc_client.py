# RRC client controller for the T-Deck.
#
# Owns hub discovery (announces on the "rrc.hub" destination), the
# outgoing link to the hub in use, the RRC session state machine, and one
# joined room. The UI layer (rrc_ui.py, driven by ui.py) renders; the
# wiring in tdeck_node.py connects gui.on_rrc_* to the functions here.
#
# Connect flow (on-demand session):
#   recall identity -> ensure path -> OutgoingLink -> wait ACTIVE ->
#   link.identify() -> HELLO -> await WELCOME -> READY
#
# HELLO is sent exactly once per link. A second HELLO is a session reset
# server-side, so link retries re-establish the link and never re-send it.
#
# Only one session is active at a time, like nomad_browser and rnsh_client.

import time

import rrc_cbor as cbor
import rrc_proto as P

MAX_HUBS = 16

_gui = None
_my_identity = None


# --- discovery --------------------------------------------------------------

def init(gui, identity):
    """Hook announce observation. Call once at boot, after Reticulum init."""
    global _gui, _my_identity, _banned, _backoff_until
    _gui = gui
    _my_identity = identity
    _banned = set()
    _backoff_until = 0
    from urns.transport import Transport
    Transport.register_announce_handler(_on_announce)


def _txt(value):
    """A hub-supplied string, or None if the hub sent something else.

    The one type boundary between the wire and the UI. Everything a hub
    sends is attacker-controlled CBOR, and the decoder is deliberately
    permissive, so K_NICK can arrive as bytes, K_ROOM as an int and the
    WELCOME hub name as a list. Those used to be type-checked at the two
    or three places that happened to look and trusted everywhere else --
    and a str-only assumption downstream is not a cosmetic bug: nick +
    "> " + text runs inside ui.draw(), which gui_loop calls while holding
    the display SPI lock with no try/except. A TypeError there kills the
    task mid-lock: the screen freezes for good and the mutex leaks.
    "@" + nick has the same shape inside kbd_loop.

    So it is validated once, here, as the value is read out of the
    envelope -- before it can reach module state or the GUI -- rather
    than defensively at every use. No value from a hub leaves this module
    as anything but a str or None."""
    return value if isinstance(value, str) else None


def _hub_hash(dest_hash):
    """The "rrc.hub" destination hash for the identity behind dest_hash."""
    from urns.identity import Identity
    from urns.destination import Destination
    data = Identity.known_destinations.get(dest_hash)
    if data and data[2]:
        id_hash = Identity.truncated_hash(data[2])
        return Destination.hash(id_hash, P.HUB_APP, P.HUB_ASPECT)
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


def hub_name(app_data):
    """The hub's display name from its announce app_data (CBOR
    {"proto":"rrc","v":1,"hub":<name>}), or None if absent or unreadable."""
    if app_data:
        try:
            info = cbor.loads(app_data)
            if isinstance(info, dict) and info.get("proto") == "rrc":
                got = info.get("hub")
                if isinstance(got, str):
                    return got
        except Exception:
            pass
    return None


def _on_announce(dest_hash, app_data, packet):
    """Transport announce observer -- collect "rrc.hub" announces.

    The hub's app_data is CBOR {"proto":"rrc","v":1,"hub":<name>}; a hub
    that sends none, or sends something we cannot read, is still a hub
    and still gets listed, just without a display name."""
    if _hub_hash(dest_hash) != dest_hash:
        return
    if _gui is not None:
        _gui.add_rrc_hub(dest_hash, name=hub_name(app_data), hops=_node_hops(dest_hash))
        if _gui._wake_mode == 1:      # announces wake only under Wake: all
            _gui.wake_screen()


# The RRC tab is session-scoped and deliberately NOT seeded from
# Identity.known_destinations, for the same reason the SSH tab isn't: a
# hub recorded days ago says nothing about whether it is reachable now.


def clear_hubs():
    """Interface switched -- reachability changed, start over.

    Disconnects first, the way rnsh_client.clear_nodes() does. Clearing
    only the GUI list left the link alive on an interface that no longer
    exists: say() still believed it was JOINED and reported "send failed"
    once per message, and the hub held the session open paying keepalive
    airtime against a client that could never answer."""
    disconnect()
    if _gui is not None:
        _gui.clear_rrc_hubs()


def _status(text):
    if _gui is not None:
        _gui.rrc_status(text)


# --- session state ----------------------------------------------------------

IDLE = 0
CONNECTING = 1
READY = 2          # linked, identified, welcomed; no room joined
JOINED = 3
CLOSED = 4

SEEN_IDS = 60           # K_IDs remembered for replay dedupe
RATE_BACKOFF_S = 15     # pause sends after a hub "rate limited"

_link = None
_dest = None            # current hub identity hash being connected to
_state = IDLE
_room = None
_roster = {}            # identity_hash[:6] -> nick or None
# Is _roster the room, or only who we happened to see? The spec is explicit
# that "any member list provided by the hub is a snapshot, not a promise",
# and on a default rrcd we get no list at all -- so the difference has to
# reach the screen rather than be flattened into a confident number.
_roster_exact = False
_who_until = 0          # harvest /who entries from notices until this time
WHO_WINDOW = 20         # seconds a /who reply is still expected
_seen_ids = []          # recent K_IDs, newest last
_limits = {}            # WELCOME limits map
_hub_name = None
# Hubs that answered ERROR "banned". Scoped to the hub that said it, not
# global: one flat flag blocked connecting to EVERY hub until reboot, and
# reported "banned by this hub" over whichever hub the user tried next.
# The spec scopes the ban to the banning hub, and it holds for the session.
_banned = set()
_backoff_until = 0      # time.time() before which sends are refused


def is_active():
    return _state in (CONNECTING, READY, JOINED)


def is_banned(dest_hash):
    return dest_hash in _banned


def _mdu():
    """This link's max plaintext per packet (431 at MTU 500)."""
    return getattr(_link, "mdu", 431) if _link is not None else 431


def _send_raw(data):
    if _link is None:
        return False
    try:
        _link.send(data)
        return True
    except Exception as e:
        _status("send failed: " + str(e))
        return False


def _send_env(env):
    """Encode and send, never exceeding the link MDU.

    urns' OutgoingLink.send() has no MDU guard, so this is the only thing
    standing between an oversized envelope and ~1.1 s of wasted LoRa
    airtime. encode_capped() trims env in step with the bytes."""
    if _link is None:
        return False
    data = P.encode_capped(env, _mdu())
    if data is None:
        _status("message too big")
        return False
    return _send_raw(data)


def _nick():
    return P.normalize_nick(getattr(_gui, "node_name", None),
                            _limits.get(P.L_MAX_NICK, P.DEFAULT_MAX_NICK))


def _line(kind, nick, text):
    if _gui is not None:
        _gui.rrc_line(kind, nick, text)


def _seen(mid):
    """True if this K_ID was already rendered. History replay hands back
    the originals, so a rejoin must not paint them twice."""
    if not isinstance(mid, (bytes, bytearray)):
        return False
    mid = bytes(mid)
    if mid in _seen_ids:
        return True
    _seen_ids.append(mid)
    if len(_seen_ids) > SEEN_IDS:
        del _seen_ids[0]
    return False


_KEY_LEN = 6


def _key(src):
    """Roster keys are the first 6 bytes of an identity.

    /who names a member by 12 hex characters and JOINED by the whole
    16-byte hash, and both mean the same person. Truncating every key to
    the shorter of the two makes them one entry rather than two, which is
    why seeding needs no reconciliation pass afterwards.

    Safe for mentions: mention_for() emits 8 hex characters and the hub
    matches "@nick or @<6+ hex of an identity hash>", so 6 bytes is one
    byte more than it needs. A 48-bit collision inside a single chat room
    is not a scale this protocol reaches.
    """
    return bytes(src)[:_KEY_LEN]


def _remember(src, nick):
    if not isinstance(src, (bytes, bytearray)):
        return
    src = _key(src)
    if nick:
        _roster[src] = nick
    elif src not in _roster:
        _roster[src] = None


def _roster_changed():
    """Push both the cheap count (room header) and the full snapshot (the
    member panel) -- _gui.rrc_roster() alone predates the panel and its
    callers still depend on it firing every time the roster changes."""
    if _gui is None:
        return
    _gui.rrc_roster(len(_roster), _roster_exact)
    members = []
    for src in _roster:
        members.append((src, _roster[src]))
    members.sort(key=lambda m: (m[1] is None, (m[1] or "").lower()))
    _gui.rrc_members(members)


def _clean_limits(limits):
    """The WELCOME limits map, keeping only the entries that are integers.

    Every value here is a budget arithmetic is done on -- a byte cap, a
    nick length, a rate. A hub sending {L_MAX_BODY: "350"} reached
    min("350", 367) in body_cap() and raised TypeError on the say() path.
    body_cap() and normalize_nick() both fall back safely on their own now,
    but a limit that is not an integer is not a limit, so it is dropped
    per key rather than stored and worked around later."""
    if not isinstance(limits, dict):
        return {}
    out = {}
    for k in limits:
        v = limits[k]
        if isinstance(v, int) and not isinstance(v, bool):
            out[k] = v
    return out


def _on_packet(data, packet=None):
    """Link receive callback. Never raises.

    urns calls this from OutgoingLink.receive() and swallows any exception
    we let out, logging it where nothing on the device can see it
    (link.py:1182-1186). That turns a bug in here into a silent stall --
    the session simply never advances and the screen sits on its last
    status forever. Surfacing it as a line in the scrollback costs
    nothing and makes the failure legible on the device itself."""
    try:
        _dispatch(data)
    except Exception as e:
        _line("error", None, "!! rx: " + str(e))


def _dispatch(data):
    """Inbound link packet -> one decoded envelope, dispatched by type.

    Structured fields only: every NOTICE the hub sends is rendered as
    text rather than parsed, because its wording is an unversioned
    formatting choice that differs between hub implementations."""
    global _state, _limits, _hub_name, _backoff_until, _room, _roster_exact

    env = P.parse(data)
    if env is None:
        return
    t = env[P.K_T]
    src = env.get(P.K_SRC)
    # _txt() here, once, is the whole of Critical 1: from this line on,
    # nick is a str or None on every path out of this function.
    nick = _txt(env.get(P.K_NICK))
    body = env.get(P.K_BODY)

    if t == P.T_PING:
        _send_env(P.make_envelope(P.T_PONG, src=_my_identity.hash, body=body))
        return

    if t == P.T_PONG:
        return

    if t == P.T_RESOURCE_ENVELOPE:
        # We decline CAP_RESOURCE_ENVELOPE, so a hub should chunk into
        # NOTICEs instead. If one advertises anyway, ignore it: the link
        # refuses the Resource by default and nothing is owed here.
        return

    if t == P.T_WELCOME:
        if isinstance(body, dict):
            _hub_name = _txt(body.get(P.B_WELCOME_HUB))
            _limits = _clean_limits(body.get(P.B_WELCOME_LIMITS))
        else:
            _hub_name = None
            _limits = {}
        if not _hub_name:
            # draw_rooms() keys off the hub name to decide whether the
            # session is still coming up. A hub that omits B_WELCOME_HUB
            # would otherwise leave the header reading "connecting..."
            # forever over a session that is already up.
            _hub_name = _dest.hex()[:8] if _dest else "rrc hub"
        _state = READY
        # Clear the connect-phase progress text. connect() sets
        # "waiting for WELCOME..." and then never calls _status() again:
        # its wait loop exits because _state moved off CONNECTING and the
        # function simply returns. Nothing else clears it, so the header
        # kept rendering the first ten characters -- "waiting fo" -- over a
        # live session, which is indistinguishable from a hung connect.
        _status("")
        if _gui is not None:
            _gui.rrc_welcome(_hub_name)
        # The console's whole job is showing what this hub offers, and the
        # hub volunteers nothing beyond an optional MOTD -- this one sent
        # no greeting at all, leaving a blank screen that read as a hang.
        # One small MSG buys the room list; replying to PING from in here
        # is the same pattern.
        list_rooms()
        return

    if t == P.T_JOINED:
        room = _txt(env.get(P.K_ROOM))
        # Our own JOIN reply, or somebody else arriving?
        #
        # _state alone is not enough: slash commands reach the hub verbatim
        # (that is what makes its command set free), so a "/join #other"
        # typed in the composer joins us without join() ever running, and
        # the reply lands while _state is already JOINED. Read as an
        # arrival, it left _room pointing at the old room -- every later
        # send carried the wrong K_ROOM, the header lied and the roster was
        # polluted. Our own reply always echoes the room we asked for, so a
        # *named* room that is not the one we are in can only be our own.
        #
        # The None guard (K_ROOM absent, or not a string) is load-bearing:
        # a hub that omits K_ROOM from its arrival broadcasts would
        # otherwise have every arrival look like a join and wipe the roster.
        if _state != JOINED or (room is not None and room != _room):
            # The body is the room's entire member list, and there is no
            # nick -- it is not an arrival event.
            _roster.clear()
            _roster_exact = False
            if isinstance(body, list):
                for member in body:
                    _remember(member, None)
                # A hub that volunteered the list has told us the room;
                # one that sent an empty body has told us nothing.
                _roster_exact = bool(_roster)
            if room:
                _room = room          # adopt whatever the hub echoed
            _state = JOINED
            if _gui is not None:
                _gui.rrc_joined(room)
            if not _roster:
                _ask_who()
        else:
            # Somebody else arrived. K_SRC is the hub; the body carries the
            # identity that actually joined, and K_NICK names it. rrcd
            # sends exactly one (router.py:503), but a hub that sends the
            # whole room instead must not have the mover's nick stamped on
            # everybody in it -- the mover is the first entry; the rest are
            # members we simply learn about, nameless.
            if isinstance(body, list):
                for i in range(len(body)):
                    _remember(body[i], nick if i == 0 else None)
            if nick:
                _line("event", None, "* " + nick + " joined")
        _roster_changed()
        return

    if t == P.T_PARTED:
        # Roster accuracy depends on the hub's include_joined_member_list
        # config: when off, T_PARTED bodies are None and departures linger.
        #
        # Only the mover is removed -- the first entry, as rrcd sends it
        # (router.py:623, a one-element body). A hub that sends the whole
        # room instead must not empty our roster: members we cannot prove
        # left stay, and a rejoin reseeds the list from scratch anyway.
        if isinstance(body, list) and body:
            member = body[0]
            if isinstance(member, (bytes, bytearray)):
                _roster.pop(_key(member), None)
        elif nick:
            # No body, which is what a default hub sends: the hash we would
            # remove by is simply absent, and departures used to linger
            # forever -- the roster only ever grew. The nick is still here,
            # so remove the one member wearing it. When two wear the same
            # nick, removing either would be a guess: remove neither, and
            # let the count stop claiming to be the room.
            same = []
            for held in _roster:
                if _roster[held] == nick:
                    same.append(held)
            if len(same) == 1:
                _roster.pop(same[0], None)
            elif same:
                _roster_exact = False
        if nick:
            _line("event", None, "* " + nick + " left")
        _roster_changed()
        return

    if t in (P.T_MSG, P.T_ACTION):
        if _seen(env.get(P.K_ID)):
            return
        _remember(src, nick)
        _line("msg" if t == P.T_MSG else "action", nick,
              body if isinstance(body, str) else "")
        return

    if t == P.T_NOTICE:
        if isinstance(body, str):
            _harvest_members(body)
            _line("notice", None, _hash_room_list(body))
        return

    if t == P.T_ERROR:
        text = body if isinstance(body, str) else "error"
        _line("error", None, text)
        if text == P.ERR_BANNED:
            if _dest is not None:
                _banned.add(_dest)      # this hub only, for the session
            disconnect()
        elif text == P.ERR_RATE_LIMITED:
            _backoff_until = time.time() + RATE_BACKOFF_S
        return

    # Unknown type: a future core message or an extension. Ignore it.


WHO_HEADER = "members in "


def _ask_who():
    """Ask the hub who is in the room, because it did not volunteer them.

    The RRC spec makes the JOINED member list optional -- "It may include a
    list of current members, but it does not have to. Presence is a
    convenience, not a guarantee" -- and rrcd leaves it off by default. A
    client that only listens therefore learns nobody who was already there,
    and (same config, empty PARTED bodies) never learns that anyone left.

    /who is an rrcd extension rather than core RRC, so this is best-effort
    by construction: a hub without it answers an error notice or nothing at
    all, and we stay on what we can see. The room is named explicitly
    because the command takes an optional room argument (commands.py) --
    no need to rely on the hub's idea of where we are.
    """
    global _who_until
    if _link is None or not _room or _my_identity is None:
        return False
    _who_until = time.time() + WHO_WINDOW
    return _send_env(P.make_envelope(P.T_MSG, src=_my_identity.hash,
                                     body="/who " + _room, nick=_nick()))


def _unhex(text):
    """The identity in a /who entry, or None if this is not an entry.

    rrcd writes a nicked member as "nick (ident[:12])" and a nickless one
    as the whole hex identity (commands.py), so only those two lengths are
    entries. Anything else in a notice is prose.
    """
    n = len(text)
    if n != 12 and n != 32:
        return None
    try:
        return bytes.fromhex(text)
    except Exception:
        return None


def _harvest_members(text):
    """Seed the roster from an rrcd /who reply.

    Entries, not lines. rrcd's queue_notice_chunks() splits an oversized
    notice at arbitrary character positions and carries no continuation
    marker, so only the first chunk holds the "members in <room>: " header.
    Parsing each entry on its own means a chunked reply still seeds -- at
    the cost of the single entry straddling each seam -- without holding
    reassembly state that an unrelated notice could poison.

    Only harvested inside the short window after we asked, so hub prose
    that happens to look like an entry cannot rewrite the room.
    """
    global _roster_exact
    if not _who_until or time.time() > _who_until:
        return
    if text.startswith(WHO_HEADER):
        head = text.find(": ")
        if head == -1:
            return
        text = text[head + 2:]
    found = 0
    for entry in text.split(","):
        entry = entry.strip()
        if not entry:
            continue
        nick = None
        ident = entry
        if entry.endswith(")"):
            nick, _, ident = entry.partition("(")
            nick = nick.strip()
            ident = ident[:-1].strip()
        src = _unhex(ident)
        if src is None:
            continue
        _remember(src, nick or None)
        found += 1
    if found:
        # The hub answered, so this is the room as the hub sees it -- a
        # snapshot, which is all the spec ever promises.
        _roster_exact = True
        _roster_changed()


LIST_HEADER = "Registered public rooms:"


def _hash_room_list(text):
    """Prefix "#" onto the room names in an rrcd /list reply, and capture
    them for the room picker.

    This is the one place the client reads the *shape* of hub prose, and it
    is deliberately narrow. Everything else renders NOTICEs verbatim,
    because their wording is an unversioned formatting choice -- so this
    fires only on rrcd's exact header line and, when a hub words it
    differently, simply does nothing and the names show bare as before.

    rrcd builds the reply as "  {name}" or "  {name} - {topic}"
    (commands.py), one room per line under that header.

    The picker's rows are taken here rather than in a parser of their own:
    the block is already being walked under an already-argued guard, so a
    hub with different wording degrades to an empty picker instead of one
    full of its MOTD. Captured names are BARE -- join() owns the "#" and
    the wire must never carry one.
    """
    if not text:
        return text
    lines = text.split("\n")
    if not lines or lines[0].strip() != LIST_HEADER:
        return text
    out = [lines[0]]
    rooms = []
    for line in lines[1:]:
        body = line.strip()
        if body and not body.startswith("#"):
            out.append("  #" + body)
        else:
            out.append(line)
        if body:
            name, _, topic = body.partition(" - ")
            name = name.strip()
            if name.startswith("#"):
                name = name[1:].strip()
            if name:
                rooms.append((name, topic.strip()))
    if _gui is not None:
        _gui.rrc_rooms(rooms)
    return "\n".join(out)


# --- outbound ---------------------------------------------------------------

CONNECT_PATH_WAIT = 30   # seconds to wait for a path
LINK_ATTEMPTS = 4        # lossy multi-hop LoRa drops requests and proofs
WELCOME_WAIT = 60        # upper bound on the WELCOME wait

_task_gen = 0


def compose_cap():
    """Longest body this session can send, from the hub's limit and the link.

    Sized against THIS session's envelope -- its room name, its nick --
    because those are what the body shares the 431-byte MDU with, and they
    are the part a fixed overhead constant could not see."""
    src = getattr(_my_identity, "hash", None)
    return P.body_cap(_mdu(), _limits.get(P.L_MAX_BODY), src=src,
                      room=_room, nick=_nick())


def mention_for(identity_hash):
    """The @-token that names this member.

    The hub matches @nick or @<6+ hex of an identity hash>; a bare nick
    matches nothing, and an ambiguous @nick resolves to nobody at all.
    We hold the roster, so ambiguity is decidable here."""
    src = _key(identity_hash)
    nick = _roster.get(src)
    if nick:
        same = 0
        for other in _roster:
            if _roster[other] == nick:
                same += 1
        if same == 1:
            return "@" + nick
    return "@" + src.hex()[:8]


def list_rooms():
    """Ask the hub which rooms it has.

    /list is the one rrcd command that needs neither a room nor
    authorisation (commands.py), which is what makes it usable from the
    console -- where there is no composer to type a command into. Without
    this nothing ever requested the list, so the console stayed empty and
    the (j)oin prompt asked for a room name the UI gave no way to learn.

    The reply is a single NOTICE whose body is "\n".join(lines). rrcd
    applies no size guard to it, so on a hub with many rooms the send can
    exceed the link MDU and fail hub-side -- we would simply receive
    nothing. That is the same trap /who has; it is the hub's to fix.
    """
    if _state not in (READY, JOINED) or _link is None:
        return False
    return _send_env(P.make_envelope(P.T_MSG, src=_my_identity.hash,
                                     body="/list", nick=_nick()))


def say(text):
    """Send composer text to the joined room.

    "/me ..." becomes ACTION; every other slash string goes as MSG, which
    is what makes the hub's whole command set free.

    Every refusal reports into the scrollback, not just through _status():
    the room view renders the scrollback and does not render the status
    line, so a message refused during a rate-limit backoff used to vanish
    without a trace -- rrc_ui clears the composer regardless of what this
    returns."""
    if not text:
        return False
    if _state != JOINED:
        _line("error", None, "not sent: not in a room")
        return False
    if time.time() < _backoff_until:
        _status("rate limited - hold on")
        _line("error", None, "not sent: rate limited")
        return False
    t = P.T_MSG
    body = text
    if text.startswith("/me ") and len(text) > 4:
        t = P.T_ACTION
        body = text[4:]
    cap = compose_cap()
    if len(body.encode("utf-8")) > cap:
        # cap is a byte budget; trim whole characters until the encoded
        # body fits, so a multi-byte tail cannot overrun it.
        while body and len(body.encode("utf-8")) > cap:
            body = body[:-1]
        if not body:
            _line("error", None, "not sent: no room in the budget")
            return False
    env = P.make_envelope(t, src=_my_identity.hash, room=_room,
                          body=body, nick=_nick())
    # Measure the real packet before anything is echoed or sent. cap is an
    # estimate from a probe encode; this is the encoder itself, and it trims
    # env in step, so the echo below shows exactly what went on the wire.
    data = P.encode_capped(env, _mdu())
    body = env.get(P.K_BODY, "")
    if data is None or not body:
        _line("error", None, "not sent: no room in the budget")
        return False
    # Echo locally, and pre-seed the dedupe with our own K_ID.
    #
    # Whether rrcd fans a message back to its sender is settled by neither
    # the spec nor the plan. This is correct either way: if the hub echoes
    # the envelope, _seen() suppresses the duplicate; if it does not, the
    # user still sees what they sent instead of typing into a void. It is
    # also the app's existing idiom -- _handle_key_chat echoes locally
    # before calling on_send.
    _seen(env[P.K_ID])
    _line("msg" if t == P.T_MSG else "action", _nick(), body)
    ok = _send_raw(data)
    if not ok:
        # _send_env only reports through _status(), which the room view
        # does not render -- and the echo above has already told the user
        # the message exists.
        _line("error", None, "not sent: send failed")
    return ok


def join(room, key=None):
    """JOIN a room. A +k room takes its key as the JOIN body."""
    global _room
    if _state not in (READY, JOINED) or not room:
        return False
    name = room.strip().lower()     # rrcd normalises exactly this way
    # The leading "#" is ours, not the hub's. rrcd's _norm_room() only does
    # strip().lower() -- "#" is an ordinary character there, so "#varna"
    # and "varna" are different rooms and /list prints names bare. We show
    # "#varna" everywhere for the IRC convention, so we have to take it
    # back off here or the name the user reads would join, or silently
    # create, a different empty room. Known cost of that trade: a room a
    # hub genuinely named "#varna" is unreachable from this device.
    if name.startswith("#"):
        name = name[1:].strip()
    if not name:
        return False
    if _state == JOINED:
        # One room at a time. Leaving first stops the hub relaying the old
        # room's traffic, and returns _state to READY so the new room's
        # JOINED reply is recognised as our own join rather than an arrival.
        part()
    _room = name
    _roster.clear()
    return _send_env(P.make_envelope(P.T_JOIN, src=_my_identity.hash,
                                     room=name, body=key, nick=_nick()))


def part():
    """PART the joined room, staying connected to the hub."""
    global _room, _state
    if _state != JOINED or not _room:
        return False
    ok = _send_env(P.make_envelope(P.T_PART, src=_my_identity.hash, room=_room))
    _room = None
    _roster.clear()
    _state = READY
    _roster_changed()
    return ok


def disconnect():
    """Tear the session down: PART the room, then close the link."""
    global _link, _state, _room, _limits, _hub_name
    # Cancel any session task still in flight FIRST. Backing out during
    # "finding path..." or "linking..." used to leave the task running: it
    # went on to establish the link, identify, send HELLO, reassign _link,
    # and on WELCOME call _gui.rrc_welcome() -- which forces
    # state = STATE_RRC_ROOMS and yanks the user out of whatever unrelated
    # LXMF conversation they had moved on to. It also left a link nobody
    # would tear down, paying LoRa airtime against the on-demand-session
    # decision. Bumping the generation makes _stale() true, and each early
    # return in _session_task tears down what it owns.
    _task_gen_next()
    if _state == JOINED:
        part()
    link = _link
    _link = None          # cleared first: teardown() fires the closed
    if link is not None:  # callback synchronously, and a deliberate close
        try:              # must not be painted as a dropped link
            link.teardown()
        except Exception:
            pass
    _room = None
    _limits = {}
    _hub_name = None
    _roster.clear()
    _seen_ids[:] = []
    _state = CLOSED


def _task_gen_next():
    global _task_gen
    _task_gen += 1
    return _task_gen


def _stale(my_gen):
    return my_gen != _task_gen


def _drop_link(link):
    """Tear down a link this task owns but is abandoning.

    _link is cleared first when it names this link, the way disconnect()
    does: teardown() fires the closed callback synchronously, and giving up
    on a link deliberately must not be painted at the user as a dropped
    one. Called from every _stale() early return that has a link or a
    candidate in hand -- otherwise an abandoned session leaves a live link
    on the hub that nobody will ever close."""
    global _link
    if link is None:
        return
    if _link is link:
        _link = None
    try:
        link.teardown()
    except Exception:
        pass


def connect(dest_hash):
    """GUI: open an RRC session to a hub (RRC tab click / manual hash).

    One session at a time, and the newest request wins. Refusing instead
    ("busy - session in progress") was worse than it looks: rrc_ui's
    open_selected_hub() has already switched the view and wiped the
    scrollback by the time this runs, so the user sat on an empty hub-B
    console while still joined to hub A, with A's traffic painting into
    it. Tearing the old session down here keeps the UI and the client
    describing the same hub, and stops paying airtime for a session
    nobody is looking at."""
    import uasyncio as asyncio
    if is_active():
        disconnect()
    # The generation is claimed HERE, not when the coroutine first runs:
    # two clicks inside one event-loop turn would otherwise both pass an
    # is_active() check and the older task would win the race.
    asyncio.create_task(_session_task(dest_hash, _task_gen_next()))


async def _session_task(dest_hash, my_gen=None):
    """Path -> link (with retries) -> identify -> HELLO -> WELCOME.

    HELLO is sent once, after the link is ACTIVE and identified. Retrying
    it on a live link would reset the session server-side and drop us
    from every room, so only link establishment is retried."""
    global _link, _dest, _state, _limits

    import uasyncio as asyncio
    from urns.identity import Identity
    from urns.transport import Transport

    if my_gen is None:                  # called directly rather than via connect()
        my_gen = _task_gen_next()
    if _stale(my_gen):
        return                          # superseded before we even started
    if dest_hash in _banned:
        _status("banned by this hub")
        return

    _dest = dest_hash
    _state = CONNECTING
    _limits = {}

    try:
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
                _link = None
                _status("no path to hub")
                _state = CLOSED
                return

        identity = Identity.recall(dest_hash)
        if identity is None:
            _link = None
            _status("unknown identity")
            _state = CLOSED
            return

        from urns.link import OutgoingLink
        from urns.destination import Destination
        dst = Destination(identity, Destination.OUT, Destination.SINGLE,
                          P.HUB_APP, P.HUB_ASPECT)
        hops = max(1, Transport.hops_to(dest_hash))
        per_attempt = min(90, max(40, 14 * hops))
        link = None
        for attempt in range(1, LINK_ATTEMPTS + 1):
            if _stale(my_gen):
                return
            _status("linking..." if attempt == 1
                    else "linking retry %d/%d..." % (attempt, LINK_ATTEMPTS))
            if attempt > 1:
                Transport.request_path(dest_hash)
                await asyncio.sleep(1)
            await asyncio.sleep_ms(50)
            candidate = OutgoingLink(dst, closed_callback=_on_link_closed,
                                     sign_proofs=True)
            t0 = time.time()
            while candidate.status == OutgoingLink.PENDING and \
                    time.time() - t0 < per_attempt:
                await asyncio.sleep_ms(200)
                if _stale(my_gen):
                    _drop_link(candidate)
                    return
            if candidate.status == OutgoingLink.ACTIVE:
                link = candidate
                _link = link            # _link only ever names an ACTIVE link
                break
            try:
                # _link is not this candidate, so the synchronous closed
                # callback cannot flip _state mid-retry.
                candidate.teardown()
            except Exception:
                pass
        if link is None or link.status != OutgoingLink.ACTIVE:
            _link = None
            _status("link failed")
            _state = CLOSED
            return

        _status("identifying...")
        # urns defines set_packet_callback() on the INCOMING Link class only
        # (link.py:591). OutgoingLink is a separate class with no inheritance:
        # it honours the same attribute (set to None at :742, invoked at
        # :1182) but exposes no setter, so calling one raises AttributeError
        # before we ever identify -- the hub sees the link come up and then
        # silently drops us, because rrcd ignores an unidentified link.
        link.packet_callback = _on_packet
        link.identify(_my_identity)
        await asyncio.sleep_ms(200)
        if _stale(my_gen):
            # Backed out while identifying. Today disconnect() has already
            # cleared and torn down _link by the time we get here, so this
            # is belt-and-braces -- but it is what makes the invariant
            # true unconditionally: a task that goes stale never leaves the
            # link it owns open, and never walks on to HELLO and the
            # WELCOME wait on a session nobody is waiting for.
            _drop_link(link)
            return

        _status("waiting for WELCOME...")
        hello_body = {P.B_HELLO_NAME: "tdeck", P.B_HELLO_VER: "1",
                      P.B_HELLO_CAPS: {}}
        _send_env(P.make_envelope(P.T_HELLO, src=_my_identity.hash,
                                  body=hello_body, nick=_nick()))
        t0 = time.time()
        while _state == CONNECTING and time.time() - t0 < WELCOME_WAIT:
            await asyncio.sleep_ms(250)
            if _stale(my_gen):
                _drop_link(link)
                return
        if _state == CONNECTING:
            _status("no WELCOME - hub silent")
            disconnect()
    except Exception as e:
        if _stale(my_gen):
            # Every other exit in this function checks first; this one did
            # not. A task abandoned by a newer connect() that then raised
            # set _link = None and _state = CLOSED, silently killing the
            # NEW session and leaking its link -- never torn down, still
            # open on the hub and still paying keepalive airtime -- while
            # painting "connect failed" over the new session's status.
            return
        _link = None
        _status("connect failed: " + str(e))
        _state = CLOSED


def _on_link_closed(link):
    global _state
    if link is _link:
        _state = CLOSED
        _line("error", None, "!! link closed")
        if _gui is not None:
            _gui.rrc_closed()
