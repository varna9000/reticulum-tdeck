# Host tests for rrc_client discovery and inbound dispatch. Bootstraps
# urns via the submodule harness. Run:
#   /opt/homebrew/bin/python3 tests/test_rrc_client.py

import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
FW_TESTS = os.path.join(REPO, "vendor", "uP-reticulum", "firmware", "tests")

sys.path.insert(0, FW_TESTS)
import harness  # noqa: F401
sys.path.insert(0, REPO)

sys.modules.setdefault("uasyncio", types.ModuleType("uasyncio"))

import rrc_cbor as C
import rrc_proto as P
import rrc_client


class FakeGui:
    def __init__(self):
        self.hubs = []
        self.cleared = 0
        self.status = []
        self._wake_mode = 0
        self.members = []           # rrc_members() snapshots, newest last
        self.rooms = []             # rrc_rooms() snapshots, newest last

    def add_rrc_hub(self, dest_hash, name=None, hops=None):
        self.hubs.append((dest_hash, name, hops))

    def clear_rrc_hubs(self):
        self.cleared += 1

    def rrc_status(self, text):
        self.status.append(text)

    def wake_screen(self):
        pass

    def rrc_closed(self):
        pass

    def rrc_members(self, members):
        # _roster_changed() calls this alongside rrc_roster() on every
        # roster change (Task 9) -- every FakeGui needs it or the fixture
        # crashes, not just the ones with a _session()-installed override.
        self.members.append(members)

    def rrc_rooms(self, rooms):
        # Same contract as rrc_members(): the /list reply pushes the room
        # picker's rows here, so every FakeGui needs it or a NOTICE
        # carrying a room list crashes the fixture.
        self.rooms.append(rooms)


def _reset():
    g = FakeGui()
    rrc_client.init(g, types.SimpleNamespace(hash=b"\x69" * 16))
    rrc_client._hub_hash = lambda h: h      # every hash is an rrc.hub hash
    rrc_client._node_hops = lambda h: 2
    return g


def test_announce_with_hub_name_is_listed():
    g = _reset()
    app_data = C.dumps({"proto": "rrc", "v": 1, "hub": "Varna Hub"})
    rrc_client._on_announce(b"\x42" * 16, app_data, None)
    assert g.hubs == [(b"\x42" * 16, "Varna Hub", 2)], g.hubs
    print("ok test_announce_with_hub_name_is_listed")


def test_announce_without_app_data_still_lists_the_hub():
    g = _reset()
    rrc_client._on_announce(b"\x43" * 16, None, None)
    assert g.hubs == [(b"\x43" * 16, None, 2)], g.hubs
    print("ok test_announce_without_app_data_still_lists_the_hub")


def test_announce_with_junk_app_data_does_not_raise():
    g = _reset()
    rrc_client._on_announce(b"\x44" * 16, b"\xff\xff\xff", None)
    assert g.hubs == [(b"\x44" * 16, None, 2)], g.hubs
    print("ok test_announce_with_junk_app_data_does_not_raise")


def test_non_rrc_announce_is_ignored():
    g = _reset()
    rrc_client._hub_hash = lambda h: b"\x00" * 16    # never matches
    rrc_client._on_announce(b"\x45" * 16, None, None)
    assert g.hubs == []
    print("ok test_non_rrc_announce_is_ignored")


class FakeLink:
    def __init__(self):
        self.sent = []
        self.mdu = 431
        self.torn_down = False

    def send(self, data):
        self.sent.append(bytes(data))

    def teardown(self):
        # urns' OutgoingLink.teardown() invokes closed_callback synchronously
        # (link.py _close). The fake must too, or the tests cannot see a
        # teardown that paints a spurious error at the user.
        self.torn_down = True
        rrc_client._on_link_closed(self)


def _session(room="#varna"):
    g = _reset()
    g.lines = []
    g.rosters = []
    g.joined = []
    g.welcomed = []
    g.rrc_line = lambda kind, nick, text: g.lines.append((kind, nick, text))
    g.rrc_roster = lambda count, exact=True: g.rosters.append(count)
    g.rrc_joined = lambda r: g.joined.append(r)
    g.rrc_welcome = lambda name: g.welcomed.append(name)
    link = FakeLink()
    rrc_client._link = link
    rrc_client._state = rrc_client.JOINED
    rrc_client._room = room
    rrc_client._roster = {}
    rrc_client._seen_ids = []
    return g, link


def _env(t, **kw):
    kw.setdefault("src", b"\xaa" * 16)
    return C.dumps(P.make_envelope(t, **kw))


def test_ping_is_answered_with_pong_echoing_the_body():
    g, link = _session()
    rrc_client._on_packet(_env(P.T_PING, body=12345))
    assert len(link.sent) == 1
    reply = P.parse(link.sent[0])
    assert reply[P.K_T] == P.T_PONG
    assert reply[P.K_BODY] == 12345
    print("ok test_ping_is_answered_with_pong_echoing_the_body")


def test_msg_renders_with_its_nick():
    g, link = _session()
    rrc_client._on_packet(_env(P.T_MSG, room="#varna", body="hi there",
                               nick="kc1awv"))
    assert g.lines == [("msg", "kc1awv", "hi there")], g.lines
    print("ok test_msg_renders_with_its_nick")


def test_action_is_rendered_not_dropped():
    g, link = _session()
    rrc_client._on_packet(_env(P.T_ACTION, room="#varna", body="waves",
                               nick="sam"))
    assert g.lines == [("action", "sam", "waves")], g.lines
    print("ok test_action_is_rendered_not_dropped")


def test_duplicate_msg_id_is_rendered_once():
    g, link = _session()
    mid = b"\x07" * 8
    for _ in range(2):
        rrc_client._on_packet(_env(P.T_MSG, room="#varna", body="replayed",
                                   nick="sam", mid=mid))
    assert len(g.lines) == 1, g.lines
    print("ok test_duplicate_msg_id_is_rendered_once")


def test_joined_body_seeds_the_roster():
    g, link = _session()
    rrc_client._state = rrc_client.READY          # we sent JOIN, not in yet
    rrc_client._roster = {_rk(b"\x99" * 16): "stale"}  # left from a previous room
    members = [b"\x11" * 16, b"\x22" * 16, b"\x33" * 16]
    rrc_client._on_packet(C.dumps(P.make_envelope(
        P.T_JOINED, src=b"\xaa" * 16, room="#varna", body=members)))
    assert rrc_client._state == rrc_client.JOINED
    assert g.joined == ["#varna"], g.joined
    # Keys are the first 6 bytes: /who names a member by 12 hex and JOINED
    # by the whole hash, and both must land on one entry. See
    # test_a_who_prefix_and_a_later_full_hash_are_one_member.
    assert set(rrc_client._roster) == set(m[:6] for m in members), rrc_client._roster
    assert b"\x99" * 6 not in rrc_client._roster, "stale member survived the reseed"
    assert g.rosters[-1] == 3
    print("ok test_joined_body_seeds_the_roster")


def test_roster_changed_pushes_both_the_count_and_the_member_snapshot():
    # Task 9 rewrote _roster_changed() to call rrc_members() alongside the
    # existing rrc_roster() -- both must fire on the same change, or either
    # the room header (count) or the member panel (snapshot) goes stale.
    # This also pins the sort: named members first (case-insensitively),
    # unnamed (never-spoken, nick is None) last -- the shape the panel
    # renders as "nick or ?" in list order.
    g, link = _session()
    rrc_client._roster = {
        b"\x33" * 16: None,
        b"\x11" * 16: "Sam",
        b"\x22" * 16: "kc1awv",
    }
    rrc_client._roster_changed()
    assert g.rosters[-1] == 3, g.rosters
    snap = g.members[-1]
    assert snap == [
        (b"\x22" * 16, "kc1awv"),
        (b"\x11" * 16, "Sam"),
        (b"\x33" * 16, None),
    ], snap
    print("ok test_roster_changed_pushes_both_the_count_and_the_member_snapshot")


def test_joined_event_adds_a_member_and_parted_removes_one():
    g, link = _session()
    who = b"\x55" * 16
    rrc_client._on_packet(C.dumps(P.make_envelope(
        P.T_JOINED, src=b"\xaa" * 16, room="#varna", body=[who], nick="sam")))
    assert _rk(who) in rrc_client._roster
    assert len(rrc_client._roster) == 1
    rrc_client._on_packet(C.dumps(P.make_envelope(
        P.T_PARTED, src=b"\xaa" * 16, room="#varna", body=[who], nick="sam")))
    assert _rk(who) not in rrc_client._roster
    assert len(rrc_client._roster) == 0
    print("ok test_joined_event_adds_a_member_and_parted_removes_one")


def test_joined_event_never_adds_the_hub_to_the_roster():
    g, link = _session()                          # already JOINED
    hub = b"\xaa" * 16
    who = b"\x55" * 16
    rrc_client._on_packet(C.dumps(P.make_envelope(
        P.T_JOINED, src=hub, room="#varna", body=[who], nick="sam")))
    assert _rk(who) in rrc_client._roster
    assert _rk(hub) not in rrc_client._roster, "K_SRC on JOINED is the hub, not a member"
    assert len(rrc_client._roster) == 1, rrc_client._roster
    assert rrc_client._roster[_rk(who)] == "sam"
    print("ok test_joined_event_never_adds_the_hub_to_the_roster")


def test_nick_is_learned_from_incoming_messages():
    g, link = _session()
    src = b"\x99" * 16
    rrc_client._on_packet(C.dumps(P.make_envelope(
        P.T_MSG, src=src, room="#varna", body="hi", nick="hilltop-rx")))
    assert rrc_client._roster.get(_rk(src)) == "hilltop-rx"
    print("ok test_nick_is_learned_from_incoming_messages")


def test_notice_renders_as_plain_text():
    g, link = _session()
    rrc_client._on_packet(_env(P.T_NOTICE, body="members in #varna: sam"))
    assert g.lines == [("notice", None, "members in #varna: sam")], g.lines
    print("ok test_notice_renders_as_plain_text")


def test_banned_error_closes_and_blocks_retry():
    g, link = _session()
    rrc_client._dest = b"\x42" * 16
    rrc_client._on_packet(_env(P.T_ERROR, body="banned"))
    assert rrc_client.is_banned(b"\x42" * 16) is True
    assert ("error", None, "banned") in g.lines
    print("ok test_banned_error_closes_and_blocks_retry")


def test_a_ban_is_scoped_to_the_hub_that_issued_it():
    # Important 1: one flat _no_retry flag blocked connecting to EVERY hub
    # until reboot, with a status line naming the wrong one. The spec scopes
    # the ban to the banning hub.
    g, link = _session()
    banning = b"\x42" * 16
    other = b"\x43" * 16
    rrc_client._dest = banning
    rrc_client._on_packet(_env(P.T_ERROR, body="banned"))
    assert rrc_client.is_banned(banning) is True
    assert rrc_client.is_banned(other) is False, \
        "one hub's ban blocked every other hub"
    print("ok test_a_ban_is_scoped_to_the_hub_that_issued_it")


def test_a_ban_survives_for_the_rest_of_the_session():
    # It must not be cleared by a later disconnect/connect cycle, or the
    # client hammers a hub that has already said no.
    g, link = _session()
    banning = b"\x42" * 16
    rrc_client._dest = banning
    rrc_client._on_packet(_env(P.T_ERROR, body="banned"))
    rrc_client.disconnect()
    rrc_client.clear_hubs()
    assert rrc_client.is_banned(banning) is True
    print("ok test_a_ban_survives_for_the_rest_of_the_session")


def test_rate_limited_error_sets_backoff():
    g, link = _session()
    rrc_client._dest = b"\x42" * 16
    rrc_client._on_packet(_env(P.T_ERROR, body="rate limited"))
    assert rrc_client._backoff_until > 0
    assert rrc_client.is_banned(b"\x42" * 16) is False
    print("ok test_rate_limited_error_sets_backoff")


def test_welcome_records_hub_limits():
    g, link = _session()
    body = {P.B_WELCOME_HUB: "Varna Hub", P.B_WELCOME_VER: "rrcd/0.4",
            P.B_WELCOME_LIMITS: {P.L_MAX_BODY: 350, P.L_MAX_NICK: 32}}
    rrc_client._state = rrc_client.CONNECTING
    rrc_client._on_packet(_env(P.T_WELCOME, body=body))
    assert rrc_client._limits.get(P.L_MAX_BODY) == 350
    assert rrc_client._state == rrc_client.READY
    assert g.welcomed == ["Varna Hub"]
    print("ok test_welcome_records_hub_limits")


def test_welcome_without_caps_map_is_accepted():
    g, link = _session()
    rrc_client._state = rrc_client.CONNECTING
    rrc_client._on_packet(_env(P.T_WELCOME, body={P.B_WELCOME_HUB: "Go Hub"}))
    assert rrc_client._state == rrc_client.READY
    print("ok test_welcome_without_caps_map_is_accepted")


def test_welcome_clears_the_connect_progress_status():
    """The status row must not keep reporting the last connect step.

    connect() ends on _status("waiting for WELCOME...") and never calls
    _status() again -- its wait loop exits because _state left CONNECTING,
    then the function returns. With nothing clearing it the header went on
    rendering the first ten characters of that string, "waiting fo", over a
    fully established session: the device looked hung while the link was up,
    identified and welcomed.
    """
    g, link = _session()
    rrc_client._state = rrc_client.CONNECTING
    rrc_client._status("waiting for WELCOME...")
    rrc_client._on_packet(_env(P.T_WELCOME, body={P.B_WELCOME_HUB: "Varna Hub"}))
    assert rrc_client._state == rrc_client.READY
    assert g.status[-1] == "", g.status
    print("ok test_welcome_clears_the_connect_progress_status")


def test_welcome_without_a_hub_name_still_names_the_header():
    """draw_rooms() reads an empty hub name as "still connecting".

    rrcd always sends B_WELCOME_HUB, but it is the hub's choice. Passing
    None through would park the header on "connecting..." for a session
    that is up -- the same lie the stale status told.
    """
    g, link = _session()
    rrc_client._dest = b"\xc9\x2b\xcc\x48" + b"\x00" * 12
    rrc_client._state = rrc_client.CONNECTING
    rrc_client._on_packet(_env(P.T_WELCOME, body={P.B_WELCOME_VER: "rrcd/0.4"}))
    assert rrc_client._state == rrc_client.READY
    assert g.welcomed[-1] == "c92bcc48", g.welcomed
    print("ok test_welcome_without_a_hub_name_still_names_the_header")


def test_welcome_asks_the_hub_for_its_room_list():
    """The console had no way to ever show a room list.

    Nothing sent /list, and STATE_RRC_ROOMS has no composer to type one
    into -- so the console sat empty and the (j)oin prompt asked for a room
    name the UI gave no way to discover. The hub volunteers nothing beyond
    an optional MOTD, and the hub under test sent none at all.
    """
    g, link = _session()
    link.sent = []
    rrc_client._state = rrc_client.CONNECTING
    rrc_client._on_packet(_env(P.T_WELCOME, body={P.B_WELCOME_HUB: "Varna Hub"}))
    assert rrc_client._state == rrc_client.READY
    assert len(link.sent) == 1, link.sent
    env = C.loads(link.sent[0])
    assert env[P.K_T] == P.T_MSG, env
    assert env[P.K_BODY] == "/list", env
    print("ok test_welcome_asks_the_hub_for_its_room_list")


def test_list_rooms_needs_no_room_and_is_refused_before_welcome():
    """/list is the one rrcd command needing neither room nor auth, which
    is what makes it usable from the console. It still needs a session:
    before WELCOME there is no link to send it on."""
    g, link = _session()
    rrc_client._state = rrc_client.CONNECTING
    link.sent = []
    assert rrc_client.list_rooms() is False
    assert link.sent == [], link.sent
    rrc_client._state = rrc_client.READY
    assert rrc_client.list_rooms() is True
    env = C.loads(link.sent[0])
    assert env[P.K_BODY] == "/list"
    assert env.get(P.K_ROOM) is None, env      # no room context needed
    print("ok test_list_rooms_needs_no_room_and_is_refused_before_welcome")


def test_resource_envelope_is_ignored_quietly():
    g, link = _session()
    before = len(g.lines)
    rrc_client._on_packet(_env(P.T_RESOURCE_ENVELOPE, body={0: b"12345678"}))
    assert len(g.lines) == before
    print("ok test_resource_envelope_is_ignored_quietly")


def test_unknown_type_and_junk_do_not_raise():
    g, link = _session()
    rrc_client._on_packet(_env(99, body="whatever"))
    rrc_client._on_packet(b"\xff\xff")
    print("ok test_unknown_type_and_junk_do_not_raise")


def test_say_sends_a_msg_envelope_with_room_and_nick():
    g, link = _session()
    g.node_name = "tdeck"
    rrc_client.say("gm from varna")
    env = P.parse(link.sent[-1])
    assert env[P.K_T] == P.T_MSG
    assert env[P.K_ROOM] == "#varna"
    assert env[P.K_BODY] == "gm from varna"
    assert env[P.K_NICK] == "tdeck"
    print("ok test_say_sends_a_msg_envelope_with_room_and_nick")


def test_say_sends_action_for_slash_me():
    g, link = _session()
    g.node_name = "tdeck"
    rrc_client.say("/me waves")
    env = P.parse(link.sent[-1])
    assert env[P.K_T] == P.T_ACTION
    assert env[P.K_BODY] == "waves"
    print("ok test_say_sends_action_for_slash_me")


def test_say_passes_other_slash_commands_through_as_msg():
    g, link = _session()
    g.node_name = "tdeck"
    rrc_client.say("/who")
    env = P.parse(link.sent[-1])
    assert env[P.K_T] == P.T_MSG and env[P.K_BODY] == "/who"
    print("ok test_say_passes_other_slash_commands_through_as_msg")


def test_say_truncates_to_the_computed_cap():
    g, link = _session()
    g.node_name = "tdeck"
    rrc_client._limits = {P.L_MAX_BODY: 20}
    rrc_client.say("x" * 100)
    env = P.parse(link.sent[-1])
    assert len(env[P.K_BODY]) == 20
    print("ok test_say_truncates_to_the_computed_cap")


def test_say_truncates_on_utf8_bytes_not_characters():
    g, link = _session()
    g.node_name = "tdeck"
    rrc_client._limits = {P.L_MAX_BODY: 10}
    rrc_client.say("ä" * 20)          # 2 bytes each, 40 bytes total
    env = P.parse(link.sent[-1])
    assert len(env[P.K_BODY].encode("utf-8")) <= 10
    print("ok test_say_truncates_on_utf8_bytes_not_characters")


def test_say_is_refused_while_rate_limited():
    g, link = _session()
    g.node_name = "tdeck"
    rrc_client._backoff_until = time.time() + 10
    sent_before = len(link.sent)
    rrc_client.say("hello")
    assert len(link.sent) == sent_before
    rrc_client._backoff_until = 0
    print("ok test_say_is_refused_while_rate_limited")


def test_join_sends_room_and_optional_key():
    g, link = _session(room=None)
    rrc_client._state = rrc_client.READY
    rrc_client.join("#varna")
    env = P.parse(link.sent[-1])
    # The "#" is a display convention of ours and never goes on the wire:
    # rrcd's _norm_room() is strip().lower() with no "#" semantics, so
    # "#varna" and "varna" are different rooms there and /list prints them
    # bare. See test_join_strips_the_display_hash_before_the_wire.
    assert env[P.K_T] == P.T_JOIN and env[P.K_ROOM] == "varna", env
    assert P.K_BODY not in env
    rrc_client.join("#secret", key="hunter2")
    env = P.parse(link.sent[-1])
    assert env[P.K_BODY] == "hunter2"
    print("ok test_join_sends_room_and_optional_key")


def test_join_strips_the_display_hash_before_the_wire():
    """We show "#varna"; the hub must hear "varna".

    rrcd's _norm_room() only strips and lowercases, so "#" is an ordinary
    character to it: sending the name as displayed would join -- or
    silently create -- a second, empty room next to the real one.
    """
    g, link = _session(room=None)
    for typed in ("#varna", "varna", "  #VARNA  ", "#  varna"):
        rrc_client._state = rrc_client.READY
        rrc_client.join(typed)
        env = P.parse(link.sent[-1])
        assert env[P.K_ROOM] == "varna", (typed, env)
    print("ok test_join_strips_the_display_hash_before_the_wire")


def test_join_refuses_a_name_that_is_only_a_hash():
    g, link = _session(room=None)
    rrc_client._state = rrc_client.READY
    before = len(link.sent)
    assert rrc_client.join("#") is False
    assert len(link.sent) == before, "nothing may go on the wire"
    print("ok test_join_refuses_a_name_that_is_only_a_hash")


def test_the_room_list_is_shown_with_hashes():
    """rrcd prints /list names bare; we show them IRC style."""
    g, link = _session()
    g.lines = []
    body = "Registered public rooms:\n  varna - chat here\n  general"
    rrc_client._on_packet(_env(P.T_NOTICE, body=body))
    text = g.lines[-1][2]
    assert text == "Registered public rooms:\n  #varna - chat here\n  #general", text
    print("ok test_the_room_list_is_shown_with_hashes")


def test_the_room_list_is_captured_for_the_picker():
    """The picker's rows come out of the block we already parse.

    _hash_room_list() is the one place this client reads the shape of hub
    prose, and it walks this exact block to prefix the "#". Capturing the
    names there costs no second parse and adds no second guard -- and it
    keeps the names BARE, because join() owns the "#" strip and the wire
    must never see one.
    """
    g, link = _session()
    body = "Registered public rooms:\n  varna - chat here\n  general"
    rrc_client._on_packet(_env(P.T_NOTICE, body=body))
    assert g.rooms[-1] == [("varna", "chat here"), ("general", "")], g.rooms
    print("ok test_the_room_list_is_captured_for_the_picker")


def _rk(identity):
    """The roster key for an identity: its first 6 bytes.

    /who names a member by 12 hex characters and JOINED by the whole
    16-byte hash; rrc_client truncates both so they are one entry.
    """
    return bytes(identity)[:6]


def _fresh_join(room="varna", body=None):
    """Join a room the way a client actually does: state READY, then the
    hub's JOINED reply. _session() leaves us already JOINED, which is the
    *arrival* path, not our own join."""
    g, link = _session(room=None)
    rrc_client._state = rrc_client.READY
    rrc_client._room = None
    rrc_client._roster = {}
    link.sent = []
    rrc_client._on_packet(_env(P.T_JOINED, room=room, body=body))
    return g, link


def _sent_bodies(link):
    out = []
    for raw in link.sent:
        try:
            out.append(P.parse(raw).get(P.K_BODY))
        except Exception:
            pass
    return out


def test_who_is_asked_when_the_hub_sends_no_member_list():
    """The spec makes the JOINED member list optional -- "Presence is a
    convenience, not a guarantee" -- and rrcd leaves it off by default, so
    a client that only listens never learns who was already in the room."""
    g, link = _fresh_join()
    assert "/who varna" in _sent_bodies(link), _sent_bodies(link)
    print("ok test_who_is_asked_when_the_hub_sends_no_member_list")


def test_who_is_asked_even_when_the_hub_volunteers_the_list():
    """The JOINED member list is identity hashes and nothing else -- rrcd
    (router.py) and the Go hub (messages.go Joined) both send bare 16-byte
    hashes. Not asking /who on such a hub left every member a "?" in the
    panel until they happened to speak."""
    g, link = _fresh_join(body=[b"\x11" * 16, b"\x22" * 16])
    assert "/who varna" in _sent_bodies(link), _sent_bodies(link)
    assert len(rrc_client._roster) == 2, rrc_client._roster
    assert g.rosters and g.rosters[-1] == 2          # the count is known already
    # ...and the reply names the people the list only numbered
    rrc_client._on_packet(_env(
        P.T_NOTICE, body="members in varna: alice (%s), bob (%s)"
        % ("11" * 6, "22" * 6)))
    assert rrc_client._roster[b"\x11" * 6] == "alice", rrc_client._roster
    assert rrc_client._roster[b"\x22" * 6] == "bob", rrc_client._roster
    assert len(rrc_client._roster) == 2, "a named member was counted twice"
    print("ok test_who_is_asked_even_when_the_hub_volunteers_the_list")


def test_the_who_reply_seeds_the_roster():
    """rrcd builds "members in {room}: " + ", ".join(entries), each entry
    "nick (12-hex)" or a bare full-length hex identity (commands.py)."""
    g, link = _fresh_join()
    rrc_client._on_packet(_env(
        P.T_NOTICE,
        body="members in varna: alice (aabbccddeeff), bob (112233445566), "
             + "9f" * 16))
    assert len(rrc_client._roster) == 3, rrc_client._roster
    assert rrc_client._roster[bytes.fromhex("aabbccddeeff")] == "alice"
    assert rrc_client._roster[bytes.fromhex("112233445566")] == "bob"
    assert rrc_client._roster[bytes.fromhex("9f" * 6)] is None, "no nick to know"
    print("ok test_the_who_reply_seeds_the_roster")


def test_a_who_prefix_and_a_later_full_hash_are_one_member():
    """/who gives 12 hex, JOINED gives the whole 16-byte hash, and both
    name the same person. Roster keys are truncated so they collapse into
    one entry instead of counting that member twice."""
    g, link = _fresh_join()
    rrc_client._on_packet(_env(P.T_NOTICE,
                               body="members in varna: alice (aabbccddeeff)"))
    assert len(rrc_client._roster) == 1
    full = bytes.fromhex("aabbccddeeff") + b"\x77" * 10
    rrc_client._on_packet(_env(P.T_JOINED, room="varna", body=[full], nick="alice"))
    assert len(rrc_client._roster) == 1, rrc_client._roster
    print("ok test_a_who_prefix_and_a_later_full_hash_are_one_member")


def test_a_chunked_reply_without_the_header_still_seeds():
    """queue_notice_chunks() splits an oversized notice at arbitrary
    character positions with no continuation marker, so only the first
    chunk carries the header. Entries are parsed independently."""
    g, link = _fresh_join()
    rrc_client._on_packet(_env(P.T_NOTICE,
                               body="members in varna: alice (aabbccddeeff)"))
    rrc_client._on_packet(_env(P.T_NOTICE,
                               body="carol (334455667788), dave (99aabbccddee)"))
    assert len(rrc_client._roster) == 3, rrc_client._roster
    assert rrc_client._roster[bytes.fromhex("334455667788")] == "carol"
    print("ok test_a_chunked_reply_without_the_header_still_seeds")


def test_ordinary_hub_prose_seeds_nobody():
    g, link = _fresh_join()
    before = dict(rrc_client._roster)
    rrc_client._on_packet(_env(P.T_NOTICE,
                               body="room varna: registered; mode=+n; topic=(none)"))
    rrc_client._on_packet(_env(P.T_NOTICE, body="welcome, behave yourself"))
    assert rrc_client._roster == before, rrc_client._roster
    print("ok test_ordinary_hub_prose_seeds_nobody")


def test_member_entries_outside_the_ask_window_are_ignored():
    """The harvest scans any notice while a /who is outstanding -- that is
    what lets a chunked reply seed without the header. The window is the
    only thing stopping a later hub notice that happens to carry an
    identity from rewriting the room."""
    g, link = _fresh_join()
    rrc_client._who_until = 0          # nothing outstanding any more
    before = dict(rrc_client._roster)
    rrc_client._on_packet(_env(P.T_NOTICE,
                               body="members in varna: mallory (deadbeef0000)"))
    assert rrc_client._roster == before, rrc_client._roster
    print("ok test_member_entries_outside_the_ask_window_are_ignored")


def test_a_departure_with_no_body_is_removed_by_nick():
    """On a default hub PARTED bodies are empty, so departures used to
    linger forever -- the roster only ever grew."""
    g, link = _fresh_join()
    rrc_client._on_packet(_env(
        P.T_NOTICE,
        body="members in varna: alice (aabbccddeeff), bob (112233445566)"))
    rrc_client._on_packet(_env(P.T_PARTED, room="varna", nick="alice"))
    assert bytes.fromhex("aabbccddeeff") not in rrc_client._roster, rrc_client._roster
    assert bytes.fromhex("112233445566") in rrc_client._roster
    print("ok test_a_departure_with_no_body_is_removed_by_nick")


def test_an_ambiguous_departure_removes_nobody():
    """Two members wearing one nick: removing either would be a guess."""
    g, link = _fresh_join()
    rrc_client._on_packet(_env(
        P.T_NOTICE,
        body="members in varna: sam (aabbccddeeff), sam (112233445566)"))
    rrc_client._on_packet(_env(P.T_PARTED, room="varna", nick="sam"))
    assert len(rrc_client._roster) == 2, rrc_client._roster
    print("ok test_an_ambiguous_departure_removes_nobody")


def test_the_roster_reports_whether_it_is_the_whole_room():
    """A count that cannot be backed must not look like one that can."""
    seen = []
    g, link = _session(room=None)
    g.rrc_roster = lambda count, exact=True: seen.append((count, exact))
    rrc_client._state = rrc_client.READY
    rrc_client._room = None
    rrc_client._roster = {}
    rrc_client._on_packet(_env(P.T_JOINED, room="varna",
                               body=[b"\x11" * 16, b"\x22" * 16]))
    assert seen[-1] == (2, True), seen
    seen[:] = []
    rrc_client._state = rrc_client.READY
    rrc_client._room = None
    rrc_client._roster = {}
    rrc_client._on_packet(_env(P.T_JOINED, room="varna"))
    rrc_client._on_packet(_env(P.T_JOINED, room="varna", body=[b"\x33" * 16],
                               nick="zoe"))
    assert seen[-1] == (1, False), seen
    print("ok test_the_roster_reports_whether_it_is_the_whole_room")


def test_foreign_hub_prose_captures_no_rooms():
    """Same narrow guard as the "#" prefix: a hub that words its header
    differently degrades to an empty picker, never to a picker full of
    someone's MOTD."""
    g, link = _session()
    rrc_client._on_packet(_env(P.T_NOTICE, body="Rooms on this hub:\n  varna"))
    assert g.rooms == [], g.rooms
    print("ok test_foreign_hub_prose_captures_no_rooms")


def test_other_hub_prose_is_never_rewritten():
    """The /list transform reads the shape of hub prose, which this client
    otherwise refuses to do. It must fire on rrcd's exact header and
    nothing else, so a hub that words it differently degrades to bare
    names rather than having its MOTD mangled."""
    g, link = _session()
    for body in ("Welcome to the hub!\n  varna\n  general",
                 "rooms:\n  varna",
                 "  varna - chat here"):
        g.lines = []
        rrc_client._on_packet(_env(P.T_NOTICE, body=body))
        assert g.lines[-1][2] == body, g.lines[-1][2]
    print("ok test_other_hub_prose_is_never_rewritten")


def test_a_room_already_named_with_a_hash_is_not_doubled():
    g, link = _session()
    g.lines = []
    body = "Registered public rooms:\n  #varna"
    rrc_client._on_packet(_env(P.T_NOTICE, body=body))
    assert g.lines[-1][2] == body, g.lines[-1][2]
    print("ok test_a_room_already_named_with_a_hash_is_not_doubled")


def test_join_lowercases_the_room_name():
    g, link = _session(room=None)
    rrc_client._state = rrc_client.READY
    rrc_client.join("  #Varna  ")
    env = P.parse(link.sent[-1])
    # Lowercased and trimmed to match rrcd's _norm_room(); the display "#"
    # is stripped on the way out (see
    # test_join_strips_the_display_hash_before_the_wire).
    assert env[P.K_ROOM] == "varna", env
    print("ok test_join_lowercases_the_room_name")


def test_part_sends_part_and_returns_to_ready():
    g, link = _session()
    rrc_client.part()
    env = P.parse(link.sent[-1])
    assert env[P.K_T] == P.T_PART and env[P.K_ROOM] == "#varna"
    assert rrc_client._state == rrc_client.READY
    assert rrc_client._room is None
    print("ok test_part_sends_part_and_returns_to_ready")


def test_mention_uses_at_nick_when_unique():
    g, link = _session()
    rrc_client._roster = {_rk(b"\x11" * 16): "sam", _rk(b"\x22" * 16): "kc1awv"}
    assert rrc_client.mention_for(b"\x11" * 16) == "@sam"
    print("ok test_mention_uses_at_nick_when_unique")


def test_mention_falls_back_to_hash_when_ambiguous_or_unknown():
    g, link = _session()
    dup = b"\x11" * 16
    rrc_client._roster = {_rk(dup): "sam", _rk(b"\x22" * 16): "sam"}
    assert rrc_client.mention_for(dup) == "@" + dup.hex()[:8]
    silent = b"\x33" * 16
    rrc_client._roster[_rk(silent)] = None
    assert rrc_client.mention_for(silent) == "@" + silent.hex()[:8]
    print("ok test_mention_falls_back_to_hash_when_ambiguous_or_unknown")


def test_intentional_disconnect_is_not_reported_as_a_dropped_link():
    g, link = _session()
    rrc_client.disconnect()
    assert link.torn_down is True
    assert not [ln for ln in g.lines if ln[0] == "error"], g.lines
    assert rrc_client._link is None
    print("ok test_intentional_disconnect_is_not_reported_as_a_dropped_link")


def test_join_while_joined_parts_the_old_room_first():
    g, link = _session(room="#varna")          # already JOINED
    rrc_client.join("#dx")
    kinds = [P.parse(p)[P.K_T] for p in link.sent[-2:]]
    assert kinds == [P.T_PART, P.T_JOIN], kinds
    # PART carries _room verbatim (the fixture set it directly); JOIN goes
    # through join(), which strips our display "#".
    assert P.parse(link.sent[-2])[P.K_ROOM] == "#varna"
    assert P.parse(link.sent[-1])[P.K_ROOM] == "dx"
    assert rrc_client._state == rrc_client.READY   # awaiting the JOIN reply
    print("ok test_join_while_joined_parts_the_old_room_first")


# --- Important 7: a hub-side /join must not desync the client --------------


def test_hub_side_join_reply_is_read_as_our_own_join_not_an_arrival():
    # Slash commands reach the hub verbatim (that is what makes its command
    # set free), so "/join #other" typed in the composer joins us without
    # join() ever running: the JOINED reply lands while _state is already
    # JOINED. Read as somebody else arriving, _room kept pointing at the old
    # room -- every later send carried the wrong K_ROOM, the header lied and
    # the roster was polluted with the new room's members.
    g, link = _session(room="#varna")                # already JOINED
    rrc_client._roster = {_rk(b"\x77" * 16): "oldtimer"}
    members = [b"\x11" * 16, b"\x22" * 16]
    rrc_client._on_packet(C.dumps(P.make_envelope(
        P.T_JOINED, src=b"\xaa" * 16, room="#other", body=members)))
    assert rrc_client._room == "#other", rrc_client._room
    assert g.joined == ["#other"], g.joined
    assert set(rrc_client._roster) == set(_rk(m) for m in members), rrc_client._roster
    assert _rk(b"\x77" * 16) not in rrc_client._roster, "old room's roster survived"
    # And the next message must carry the room we are actually in.
    g.node_name = "tdeck"
    rrc_client.say("gm")
    assert P.parse(link.sent[-1])[P.K_ROOM] == "#other"
    print("ok test_hub_side_join_reply_is_read_as_our_own_join_not_an_arrival")


def test_an_arrival_in_the_room_we_are_in_is_still_an_arrival():
    # The other side of the discriminator: a JOINED echoing OUR room while
    # we are JOINED is somebody else arriving, and must add to the roster
    # rather than clearing it and re-firing rrc_joined().
    g, link = _session(room="#varna")
    rrc_client._roster = {_rk(b"\x77" * 16): "oldtimer"}
    who = b"\x55" * 16
    rrc_client._on_packet(C.dumps(P.make_envelope(
        P.T_JOINED, src=b"\xaa" * 16, room="#varna", body=[who], nick="sam")))
    assert g.joined == [], g.joined
    assert rrc_client._room == "#varna"
    assert _rk(b"\x77" * 16) in rrc_client._roster, "an arrival wiped the roster"
    assert rrc_client._roster[_rk(who)] == "sam"
    assert ("event", None, "* sam joined") in g.lines, g.lines
    print("ok test_an_arrival_in_the_room_we_are_in_is_still_an_arrival")


def test_an_arrival_without_a_room_field_is_not_mistaken_for_our_own_join():
    # A hub that omits K_ROOM from its arrival broadcasts would otherwise
    # have every arrival look like a join and wipe the roster -- the room
    # comparison only decides when the hub actually named a room.
    g, link = _session(room="#varna")
    rrc_client._roster = {_rk(b"\x77" * 16): "oldtimer"}
    who = b"\x55" * 16
    rrc_client._on_packet(C.dumps(P.make_envelope(
        P.T_JOINED, src=b"\xaa" * 16, body=[who], nick="sam")))   # no K_ROOM
    assert g.joined == [], g.joined
    assert rrc_client._room == "#varna", rrc_client._room
    assert _rk(b"\x77" * 16) in rrc_client._roster, "a roomless arrival wiped the roster"
    print("ok test_an_arrival_without_a_room_field_is_not_mistaken_for_our_own_join")


# --- Important 6: a refused send must not vanish silently ------------------


def test_a_rate_limited_send_says_so_in_the_scrollback():
    # rrc_ui clears the composer and ignores say()'s return value, and
    # draw_room() never renders _rrc_status -- so a _status() call alone is
    # invisible. The scrollback IS rendered.
    g, link = _session()
    g.node_name = "tdeck"
    rrc_client._backoff_until = time.time() + 10
    sent_before = len(link.sent)
    assert rrc_client.say("hello") is False
    rrc_client._backoff_until = 0
    assert len(link.sent) == sent_before
    assert ("error", None, "not sent: rate limited") in g.lines, g.lines
    print("ok test_a_rate_limited_send_says_so_in_the_scrollback")


def test_a_send_with_no_room_joined_says_so_in_the_scrollback():
    g, link = _session()
    g.node_name = "tdeck"
    rrc_client._state = rrc_client.READY          # connected, not in a room
    assert rrc_client.say("hello") is False
    assert link.sent == []
    assert ("error", None, "not sent: not in a room") in g.lines, g.lines
    print("ok test_a_send_with_no_room_joined_says_so_in_the_scrollback")


def test_a_failed_send_says_so_after_the_local_echo():
    # The local echo (below) makes a silent send failure actively
    # misleading: the user sees their own message and assumes it went.
    g, link = _session()
    g.node_name = "tdeck"

    def _boom(data):
        raise OSError("link gone")

    link.send = _boom
    assert rrc_client.say("gm") is False
    assert ("msg", "tdeck", "gm") in g.lines, g.lines
    assert ("error", None, "not sent: send failed") in g.lines, g.lines
    print("ok test_a_failed_send_says_so_after_the_local_echo")


# --- Important 9: settle self-echo -----------------------------------------


def test_say_echoes_locally_so_a_room_is_never_silent():
    # Neither the spec nor the plan settles whether rrcd fans a message back
    # to its sender. If it does not, typing into a room appeared to do
    # nothing at all.
    g, link = _session()
    g.node_name = "tdeck"
    rrc_client.say("gm from varna")
    assert g.lines == [("msg", "tdeck", "gm from varna")], g.lines
    print("ok test_say_echoes_locally_so_a_room_is_never_silent")


def test_slash_me_echoes_as_an_action_not_a_message():
    g, link = _session()
    g.node_name = "tdeck"
    rrc_client.say("/me waves")
    assert g.lines == [("action", "tdeck", "waves")], g.lines
    print("ok test_slash_me_echoes_as_an_action_not_a_message")


def test_a_hub_echo_of_our_own_message_renders_only_once():
    # The other half: a hub that DOES fan the message back hands us our own
    # envelope, K_ID and all. Pre-seeding _seen() with that id is what stops
    # it being painted twice.
    g, link = _session()
    g.node_name = "tdeck"
    rrc_client.say("gm from varna")
    assert len(g.lines) == 1, g.lines
    # Replay the exact envelope we put on the wire, as a hub fan-out would.
    rrc_client._on_packet(link.sent[-1])
    assert len(g.lines) == 1, g.lines
    print("ok test_a_hub_echo_of_our_own_message_renders_only_once")


def test_a_different_message_with_a_new_id_still_renders():
    # Guards the dedupe from over-reaching: pre-seeding our own id must not
    # suppress anybody else's message.
    g, link = _session()
    g.node_name = "tdeck"
    rrc_client.say("gm")
    rrc_client._on_packet(_env(P.T_MSG, room="#varna", body="hi", nick="sam"))
    assert g.lines == [("msg", "tdeck", "gm"), ("msg", "sam", "hi")], g.lines
    print("ok test_a_different_message_with_a_new_id_still_renders")


# --- Important 3: disconnect() must cancel an in-flight session task -------
#
# These drive the REAL _session_task coroutine. uasyncio's sleeps are
# replaced with a primitive that suspends on every await, so the test can
# step the task and call disconnect() at a chosen point; urns' identity,
# transport, link and destination modules are swapped for stubs for the
# duration. Nothing here fakes rrc_client itself.


class _Yield:
    def __await__(self):
        yield


async def _fake_sleep(*a):
    await _Yield()


class _FakeOutgoingLink:
    PENDING = 0
    ACTIVE = 1
    CLOSED = 2
    instances = []
    initial = 0            # PENDING unless a test says otherwise

    def __init__(self, dst, closed_callback=None, sign_proofs=False):
        self.status = _FakeOutgoingLink.initial
        self.mdu = 431
        self.sent = []
        self.torn_down = False
        self.identified = None
        self.packet_callback = None
        self.closed_callback = closed_callback
        _FakeOutgoingLink.instances.append(self)

    def send(self, data):
        self.sent.append(bytes(data))

    # NO set_packet_callback here, deliberately. urns defines that setter on
    # the INCOMING Link class only; OutgoingLink honours the attribute but
    # exposes no setter. This fake used to provide one, which is why the
    # suite happily green-lit a call that raised AttributeError on the first
    # real connect. test_the_fake_link_cannot_outgrow_the_real_one keeps the
    # two in step -- do not add a convenience method here without checking
    # the real class has it.

    def identify(self, identity):
        self.identified = identity

    def teardown(self):
        self.torn_down = True
        self.status = _FakeOutgoingLink.CLOSED
        if self.closed_callback is not None:
            self.closed_callback(self)


class _FakeDestination:
    OUT = 1
    SINGLE = 0

    def __init__(self, *a):
        pass


def _install_session_stubs(path_found_after=0):
    """Swap uasyncio and the four urns modules _session_task imports.

    Returns (restore_callable, transport_stub). path_found_after is the
    number of has_path() calls that answer False before the path appears,
    which is how the "finding path..." phase is held open.
    """
    saved = {}
    for name in ("uasyncio", "urns.identity", "urns.transport",
                 "urns.link", "urns.destination"):
        saved[name] = sys.modules.get(name)

    aio = types.ModuleType("uasyncio")
    aio.sleep = _fake_sleep
    aio.sleep_ms = _fake_sleep
    aio.create_task = lambda coro: coro
    sys.modules["uasyncio"] = aio

    state = {"calls": 0}

    class _Transport:
        requested = []

        @staticmethod
        def has_path(h):
            state["calls"] += 1
            return state["calls"] > path_found_after

        @staticmethod
        def request_path(h):
            _Transport.requested.append(h)

        @staticmethod
        def hops_to(h):
            return 1

    class _Identity:
        @staticmethod
        def recall(h):
            return object() if state["calls"] > path_found_after else None

    m = types.ModuleType("urns.identity")
    m.Identity = _Identity
    sys.modules["urns.identity"] = m
    m = types.ModuleType("urns.transport")
    m.Transport = _Transport
    sys.modules["urns.transport"] = m
    m = types.ModuleType("urns.link")
    m.OutgoingLink = _FakeOutgoingLink
    sys.modules["urns.link"] = m
    m = types.ModuleType("urns.destination")
    m.Destination = _FakeDestination
    sys.modules["urns.destination"] = m

    _FakeOutgoingLink.instances = []
    _FakeOutgoingLink.initial = _FakeOutgoingLink.PENDING
    _Transport.requested = []

    def restore():
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod

    return restore, _Transport


def _idle_session():
    g, link = _session()
    g.welcomed = []
    rrc_client._link = None
    rrc_client._state = rrc_client.IDLE
    rrc_client._banned = set()
    rrc_client._backoff_until = 0
    return g


def _step(coro, n=1):
    """Advance the coroutine n await points. Returns True once it finished."""
    for _ in range(n):
        try:
            coro.send(None)
        except StopIteration:
            return True
    return False


def test_disconnect_while_finding_a_path_stops_the_session_task_dead():
    # Back out during "finding path...": without cancellation the task went
    # on to establish the link, identify, send HELLO and -- on WELCOME --
    # call _gui.rrc_welcome(), which forces state = STATE_RRC_ROOMS and
    # yanks the user out of an unrelated LXMF conversation. It also left a
    # link nobody would tear down, paying LoRa airtime against the
    # on-demand-session decision.
    g = _idle_session()
    restore, transport = _install_session_stubs(path_found_after=1)
    try:
        # The path turns up on the very next poll, so a task that keeps
        # running gets all the way to HELLO rather than timing out.
        _FakeOutgoingLink.initial = _FakeOutgoingLink.ACTIVE
        coro = rrc_client._session_task(b"\x42" * 16)
        _step(coro)                       # into the path wait
        assert rrc_client._state == rrc_client.CONNECTING
        rrc_client.disconnect()           # the user backs out
        assert _step(coro, 60), "the session task kept running after disconnect"
        assert _FakeOutgoingLink.instances == [], "a link was opened anyway"
        assert g.welcomed == [], g.welcomed
        assert rrc_client._link is None
        assert rrc_client._state == rrc_client.CLOSED
    finally:
        restore()
    print("ok test_disconnect_while_finding_a_path_stops_the_session_task_dead")


def test_disconnect_while_linking_tears_down_the_pending_candidate():
    # One step further in: the link request is out, so the task owns a
    # candidate nobody else can see. Abandoning it without a teardown leaves
    # a half-open link on the hub.
    g = _idle_session()
    restore, transport = _install_session_stubs()
    try:
        coro = rrc_client._session_task(b"\x42" * 16)
        _step(coro, 2)                    # past sleep_ms(50), into the wait
        assert len(_FakeOutgoingLink.instances) == 1, _FakeOutgoingLink.instances
        candidate = _FakeOutgoingLink.instances[0]
        assert candidate.torn_down is False
        rrc_client.disconnect()
        assert _step(coro, 60), "the session task kept running after disconnect"
        assert candidate.torn_down is True, "the candidate link was orphaned"
        assert candidate.sent == [], "HELLO was spent on an abandoned session"
        assert rrc_client._link is None
        assert g.welcomed == [], g.welcomed
    finally:
        restore()
    print("ok test_disconnect_while_linking_tears_down_the_pending_candidate")


def test_disconnect_after_the_link_goes_active_leaves_no_live_link():
    # Honest about what this one is: a GUARD, not a regression witness. By
    # the time the task reaches the identify checkpoint, disconnect() has
    # already cleared and torn down _link, so this stays green with the
    # _stale() check there removed. It pins the composite invariant the
    # other two do not reach -- an ACTIVE, identified link plus a backout
    # still ends with no live link, no HELLO and no WELCOME at the GUI --
    # and it is the test that would catch _link not being cleared.
    g = _idle_session()
    restore, transport = _install_session_stubs()
    try:
        coro = rrc_client._session_task(b"\x42" * 16)
        _step(coro, 2)
        link = _FakeOutgoingLink.instances[0]
        link.status = _FakeOutgoingLink.ACTIVE
        _step(coro)                       # out of the wait, identify, sleep
        assert link.identified is not None, "the task never identified"
        assert rrc_client._link is link
        rrc_client.disconnect()
        assert _step(coro, 60), "the session task kept running after disconnect"
        assert link.torn_down is True
        assert link.sent == [], "HELLO was sent after the user backed out"
        assert rrc_client._link is None
        assert g.welcomed == [], g.welcomed
    finally:
        restore()
    print("ok test_disconnect_after_the_link_goes_active_spends_no_hello")


def test_every_interface_switch_clears_the_hub_list():
    # Important 8: clear_hubs() existed with the docstring "Interface
    # switched -- reachability changed, start over", and ui.clear_rrc_hubs()
    # existed to serve it, but neither interface-switch path in
    # tdeck_node.py called it. After a LoRa<->TCP switch the RRC tab kept
    # unreachable hubs, and clicking one cost a path request plus a 30 s
    # wait. tdeck_node.py cannot be imported on the host (machine, board,
    # the radio), so this pins the wiring at the source level -- and pins it
    # to the SIBLING calls, so a third switch path added later has to bring
    # the hub list with it too.
    src = open(os.path.join(REPO, "tdeck_node.py")).read()
    sites = src.count("rnsh_client.clear_nodes()")
    assert sites == 2, sites
    assert src.count("nomad_browser.clear_nodes()") == sites
    assert src.count("rrc_client.clear_hubs()") == sites, \
        "an interface switch does not clear the RRC hub list"
    for chunk in src.split("rnsh_client.clear_nodes()")[1:]:
        assert chunk.lstrip().startswith("rrc_client.clear_hubs()"), chunk[:120]
    print("ok test_every_interface_switch_clears_the_hub_list")


def test_clear_hubs_reaches_the_gui():
    g = _reset()
    rrc_client.clear_hubs()
    assert g.cleared == 1, g.cleared
    print("ok test_clear_hubs_reaches_the_gui")


def test_drop_link_does_not_paint_an_abandoned_link_as_a_dropped_one():
    # _drop_link clears _link before teardown() so the synchronous closed
    # callback cannot report a deliberate abandon as "!! link closed" --
    # the same ordering disconnect() relies on.
    g, link = _session()
    rrc_client._drop_link(link)
    assert link.torn_down is True
    assert rrc_client._link is None
    assert not [ln for ln in g.lines if ln[0] == "error"], g.lines
    print("ok test_drop_link_does_not_paint_an_abandoned_link_as_a_dropped_one")


# --- Critical 3: nothing oversized goes on air -----------------------------


def test_say_never_puts_a_packet_bigger_than_the_mdu_on_air():
    # Measured, not computed from a constant: room + nick + a max-length
    # body encoded to 433 bytes against a 431-byte link MDU, and urns'
    # OutgoingLink.send() has no MDU guard to catch it.
    g, link = _session(room="#varna-mesh-chat")
    g.node_name = "milen-tdeck-node"
    rrc_client._limits = {P.L_MAX_BODY: 350}
    rrc_client.say("x" * 350)
    assert len(link.sent[-1]) <= link.mdu, (len(link.sent[-1]), link.mdu)
    # ...and it still sent as much as it could, rather than collapsing.
    assert len(P.parse(link.sent[-1])[P.K_BODY]) > 300
    print("ok test_say_never_puts_a_packet_bigger_than_the_mdu_on_air")


def test_the_echo_shows_exactly_what_went_on_the_wire():
    # The local echo is the only confirmation the user gets; if the packet
    # was trimmed to fit, the echo has to show the trimmed text.
    g, link = _session(room="#varna-mesh-chat")
    g.node_name = "milen-tdeck-node"
    rrc_client._limits = {P.L_MAX_BODY: 350}
    rrc_client.say("x" * 350)
    assert P.parse(link.sent[-1])[P.K_BODY] == g.lines[-1][2], (
        len(P.parse(link.sent[-1])[P.K_BODY]), len(g.lines[-1][2]))
    print("ok test_the_echo_shows_exactly_what_went_on_the_wire")


def test_a_huge_ping_body_is_ponged_without_overrunning_the_link():
    # Everything from a hub is attacker-controlled, including the PING body
    # the PONG echoes back.
    g, link = _session()
    rrc_client._on_packet(_env(P.T_PING, body=b"\xab" * 400))
    assert len(link.sent) == 1, link.sent
    assert len(link.sent[0]) <= link.mdu, len(link.sent[0])
    assert P.parse(link.sent[0])[P.K_T] == P.T_PONG
    print("ok test_a_huge_ping_body_is_ponged_without_overrunning_the_link")


# --- Critical 2: a non-integer hub limit is not stored ---------------------


def test_welcome_limits_that_are_not_integers_are_ignored_per_key():
    g, link = _session()
    rrc_client._state = rrc_client.CONNECTING
    body = {P.B_WELCOME_HUB: "Evil Hub",
            P.B_WELCOME_LIMITS: {P.L_MAX_BODY: "350", P.L_MAX_NICK: 32,
                                 P.L_MAX_ROOM: None, P.L_RATE: [1]}}
    rrc_client._on_packet(_env(P.T_WELCOME, body=body))
    assert rrc_client._limits == {P.L_MAX_NICK: 32}, rrc_client._limits
    # ...and the composer cap stays a usable integer.
    cap = rrc_client.compose_cap()
    assert isinstance(cap, int) and cap > 0, cap
    print("ok test_welcome_limits_that_are_not_integers_are_ignored_per_key")


def test_a_welcome_limits_map_that_is_not_a_map_is_ignored():
    g, link = _session()
    rrc_client._state = rrc_client.CONNECTING
    rrc_client._on_packet(_env(P.T_WELCOME, body={P.B_WELCOME_LIMITS: [1, 2]}))
    assert rrc_client._limits == {}, rrc_client._limits
    print("ok test_a_welcome_limits_map_that_is_not_a_map_is_ignored")


# --- Critical 1: no hub value leaves the client untyped --------------------
#
# The UI-side half of this lives in test_rrc_ui.py, where a real UI and the
# real draw path are available. These pin the client boundary itself.


def test_a_non_string_nick_never_reaches_the_gui_or_the_roster():
    g, link = _session()
    # (A float is not in this list because rrc_cbor cannot encode one --
    # its decoder skips floats to None, so one never reaches a nick at all.)
    for bad in (b"\xff\xfe", 12345, ["nick"], {"n": 1}, True):
        g.lines = []
        rrc_client._roster = {}
        rrc_client._on_packet(C.dumps(P.make_envelope(
            P.T_MSG, src=b"\x55" * 16, room="#varna", body="hi", nick=bad)))
        for kind, nick, text in g.lines:
            assert nick is None or isinstance(nick, str), (bad, nick)
            assert isinstance(text, str), (bad, text)
        for src in rrc_client._roster:
            n = rrc_client._roster[_rk(src)]
            assert n is None or isinstance(n, str), (bad, n)
    print("ok test_a_non_string_nick_never_reaches_the_gui_or_the_roster")


def test_a_non_string_nick_on_a_join_event_does_not_raise():
    # "* " + nick + " joined" was a straight concatenation of a hub value.
    g, link = _session()
    rrc_client._on_packet(C.dumps(P.make_envelope(
        P.T_JOINED, src=b"\xaa" * 16, room="#varna", body=[b"\x55" * 16],
        nick=b"\xff\xfe")))
    rrc_client._on_packet(C.dumps(P.make_envelope(
        P.T_PARTED, src=b"\xaa" * 16, room="#varna", body=[b"\x55" * 16],
        nick=b"\xff\xfe")))
    for kind, nick, text in g.lines:
        assert isinstance(text, str), text
    print("ok test_a_non_string_nick_on_a_join_event_does_not_raise")


def test_a_mixed_type_roster_still_sorts_for_the_member_panel():
    # _roster_changed() sorts on (nick is None, nick.lower()); one bytes
    # nick beside one str nick raised TypeError on the comparison, in the
    # task that owns the display.
    g, link = _session()
    hub = b"\xaa" * 16
    rrc_client._on_packet(C.dumps(P.make_envelope(
        P.T_JOINED, src=hub, room="#varna", body=[b"\x11" * 16], nick="sam")))
    rrc_client._on_packet(C.dumps(P.make_envelope(
        P.T_JOINED, src=hub, room="#varna", body=[b"\x22" * 16],
        nick=b"\xff\xfe")))
    snap = g.members[-1]
    assert len(snap) == 2, snap
    for src, nick in snap:
        assert isinstance(src, (bytes, bytearray)), src
        assert nick is None or isinstance(nick, str), nick
    print("ok test_a_mixed_type_roster_still_sorts_for_the_member_panel")


def test_a_non_string_room_never_reaches_the_gui_or_the_wire():
    for bad in (12345, b"#varna", ["#varna"], {"r": 1}):
        g, link = _session(room="#varna")
        rrc_client._state = rrc_client.READY          # we sent JOIN
        rrc_client._on_packet(C.dumps(P.make_envelope(
            P.T_JOINED, src=b"\xaa" * 16, room=bad, body=[b"\x11" * 16])))
        assert g.joined == [None], (bad, g.joined)
        assert rrc_client._room is None or isinstance(rrc_client._room, str)
    print("ok test_a_non_string_room_never_reaches_the_gui_or_the_wire")


def test_a_non_string_welcome_hub_name_never_reaches_the_gui():
    for bad in (["evil"], 7, b"hub", {"h": 1}):
        g, link = _session()
        rrc_client._dest = b"\x42" * 16
        rrc_client._state = rrc_client.CONNECTING
        rrc_client._on_packet(_env(P.T_WELCOME, body={P.B_WELCOME_HUB: bad}))
        # _txt() rejects the hostile value; the header then falls back to
        # the hub's own hash rather than None, which draw_rooms() would
        # have read as "still connecting" on a live session.
        assert len(g.welcomed) == 1, (bad, g.welcomed)
        assert isinstance(g.welcomed[0], str), (bad, g.welcomed)
        assert g.welcomed[0] == "42424242", (bad, g.welcomed)
    print("ok test_a_non_string_welcome_hub_name_never_reaches_the_gui")


def test_mention_for_always_returns_a_string():
    g, link = _session()
    rrc_client._on_packet(C.dumps(P.make_envelope(
        P.T_MSG, src=b"\x55" * 16, room="#varna", body="hi", nick=b"\xff")))
    token = rrc_client.mention_for(b"\x55" * 16)
    assert isinstance(token, str), token
    print("ok test_mention_for_always_returns_a_string")


# --- Important 4/5: one session at a time, and switching away ends it ------


def test_connecting_to_a_second_hub_replaces_the_live_session():
    # rrc_ui.open_selected_hub() commits the UI to the new hub before this
    # runs, so refusing would leave the user on an empty hub-B console while
    # hub A's traffic painted into it.
    g, link = _session()                      # JOINED to hub A
    restore, transport = _install_session_stubs()
    started = []
    sys.modules["uasyncio"].create_task = lambda coro: started.append(coro)
    try:
        rrc_client.connect(b"\x43" * 16)
        assert link.torn_down is True, "the first session survived the switch"
        assert rrc_client._link is None
        assert rrc_client.is_active() is False
        # ...and the new session is actually accepted, not refused as busy.
        assert len(started) == 1, started
        _step(started[0], 1)
        assert rrc_client._state == rrc_client.CONNECTING, rrc_client._state
        assert rrc_client._dest == b"\x43" * 16, rrc_client._dest
        assert not any("busy" in s for s in g.status), g.status
        started[0].close()
    finally:
        restore()
    print("ok test_connecting_to_a_second_hub_replaces_the_live_session")


def test_clear_hubs_disconnects_the_live_session():
    # Important 5: after a LoRa<->TCP switch the link survives on a
    # deregistered interface -- say() still believes it is JOINED and the
    # hub keeps paying keepalive airtime. rnsh_client.clear_nodes()
    # disconnects first; this did not.
    g, link = _session()
    rrc_client.clear_hubs()
    assert link.torn_down is True, "the link survived the interface switch"
    assert rrc_client._link is None
    assert rrc_client.is_active() is False
    assert g.cleared == 1, g.cleared
    print("ok test_clear_hubs_disconnects_the_live_session")


# --- Important 7: a stale task must not clobber the newer session ----------


def test_a_stale_task_that_raises_does_not_kill_the_newer_session():
    # Every other exit in _session_task checks _stale() first; the except
    # handler did not. An old task raising after a newer one started set
    # _link = None and _state = CLOSED, silently killing the NEW session and
    # leaking its link -- never torn down, still open on the hub.
    # The stepper is single-threaded, so the takeover is staged from inside
    # the call that raises: Transport.hops_to() runs in the straight-line
    # prelude, after the path wait's _stale() check and before the next one,
    # which is exactly the window the defect lives in. What is under test is
    # the ORDER -- a newer session owns _link and _state by the time the old
    # task's exception reaches the handler -- not how the two interleaved.
    g = _idle_session()
    restore, transport = _install_session_stubs(path_found_after=1)
    try:
        newlink = FakeLink()

        def _takeover_then_boom(h):
            rrc_client._task_gen_next()          # what connect()/disconnect() do
            rrc_client._link = newlink
            rrc_client._state = rrc_client.JOINED
            raise OSError("radio busy")

        transport.hops_to = staticmethod(_takeover_then_boom)
        coro = rrc_client._session_task(b"\x42" * 16)
        _step(coro, 1)                  # parked in the path wait
        status_before = len(g.status)
        assert _step(coro, 20), "the stale task kept running"
        assert rrc_client._link is newlink, "a stale task cleared the new link"
        assert rrc_client._state == rrc_client.JOINED, rrc_client._state
        assert newlink.torn_down is False, "the new link was leaked/torn down"
        assert g.status[status_before:] == [], g.status[status_before:]
    finally:
        restore()
    print("ok test_a_stale_task_that_raises_does_not_kill_the_newer_session")


def test_a_live_task_that_raises_still_reports_and_closes():
    # The other half: the _stale() guard must not make a real failure silent.
    g = _idle_session()
    restore, transport = _install_session_stubs(path_found_after=1)
    try:
        def _boom(h):
            raise OSError("radio busy")

        transport.hops_to = staticmethod(_boom)
        coro = rrc_client._session_task(b"\x42" * 16)
        _step(coro, 1)
        assert _step(coro, 20), "the task never finished"
        assert rrc_client._state == rrc_client.CLOSED, rrc_client._state
        assert rrc_client._link is None
        assert any("connect failed" in s for s in g.status), g.status
    finally:
        restore()
    print("ok test_a_live_task_that_raises_still_reports_and_closes")


def test_a_banned_hub_is_refused_but_another_hub_is_not():
    g = _idle_session()
    restore, transport = _install_session_stubs()
    try:
        rrc_client._banned = set([b"\x42" * 16])
        coro = rrc_client._session_task(b"\x42" * 16)
        assert _step(coro, 5), "the banned hub was dialled anyway"
        assert any("banned" in s for s in g.status), g.status
        assert _FakeOutgoingLink.instances == [], _FakeOutgoingLink.instances

        coro = rrc_client._session_task(b"\x43" * 16)
        _step(coro, 1)
        assert rrc_client._state == rrc_client.CONNECTING, \
            "a ban on one hub blocked another"
    finally:
        restore()
    print("ok test_a_banned_hub_is_refused_but_another_hub_is_not")


# --- Minor 1: an event names the mover, not everybody in the body ----------


def test_a_join_event_names_only_the_mover():
    # rrcd's event bodies are [peer_hash] -- exactly one -- so this is
    # hardening for a hub that sends the whole room instead. The nick
    # belongs to the member that moved, not to everyone listed.
    g, link = _session(room="#varna")
    mover = b"\x55" * 16
    bystander = b"\x66" * 16
    rrc_client._on_packet(C.dumps(P.make_envelope(
        P.T_JOINED, src=b"\xaa" * 16, room="#varna",
        body=[mover, bystander], nick="sam")))
    assert rrc_client._roster.get(_rk(mover)) == "sam", rrc_client._roster
    assert rrc_client._roster.get(bystander) is None, \
        "the mover's nick was stamped on another member"
    print("ok test_a_join_event_names_only_the_mover")


def test_a_part_event_removes_only_the_mover():
    g, link = _session(room="#varna")
    mover = b"\x55" * 16
    bystander = b"\x66" * 16
    rrc_client._roster = {mover: "sam", bystander: "kc1awv"}
    rrc_client._on_packet(C.dumps(P.make_envelope(
        P.T_PARTED, src=b"\xaa" * 16, room="#varna",
        body=[mover, bystander], nick="sam")))
    assert _rk(mover) not in rrc_client._roster, rrc_client._roster
    assert bystander in rrc_client._roster, \
        "a PART removed members that did not leave"
    print("ok test_a_part_event_removes_only_the_mover")



def test_connect_binds_the_packet_callback_the_real_link_actually_reads():
    # The first connect on hardware failed here: _session_task called
    # link.set_packet_callback(), which OutgoingLink does not define, so it
    # raised before identify() and the hub silently dropped an unidentified
    # link. Bind the attribute urns' OutgoingLink.receive() reads instead.
    g = _reset()
    restore, _transport = _install_session_stubs()
    try:
        _FakeOutgoingLink.initial = _FakeOutgoingLink.ACTIVE
        rrc_client._state = rrc_client.IDLE
        rrc_client._link = None
        coro = rrc_client._session_task(b"\x42" * 16)
        _step(coro, 40)
        link = _FakeOutgoingLink.instances[-1]
        assert link.packet_callback is rrc_client._on_packet, link.packet_callback
        assert link.identified is not None, "never identified"
        assert link.sent, "no HELLO was sent"
        env = P.parse(link.sent[0])
        assert env[P.K_T] == P.T_HELLO, env[P.K_T]
    finally:
        restore()
    print("ok test_connect_binds_the_packet_callback_the_real_link_actually_reads")


def test_the_fake_link_cannot_outgrow_the_real_one():
    # The bug above was invisible because the double implemented a method the
    # real class lacks. Any future convenience added to the fake re-opens that
    # hole, so pin the fake's surface against urns' actual OutgoingLink.
    from urns.link import OutgoingLink as RealOutgoingLink
    fake = set(n for n in dir(_FakeOutgoingLink) if not n.startswith("_"))
    real = set(n for n in dir(RealOutgoingLink) if not n.startswith("_"))
    extra = fake - real - {"instances", "initial"}
    assert not extra, "fake OutgoingLink exposes what urns does not: " + repr(sorted(extra))
    print("ok test_the_fake_link_cannot_outgrow_the_real_one")


def test_a_picker_refresh_does_not_reprint_the_room_list():
    """Opening the room picker re-asks the hub for /list so its rows are
    current. The reply used to be printed into the console as well, so
    every trackball click added another copy of the same list."""
    g, link = _session()
    g.lines = []
    body = "Registered public rooms:\n  varna - chat here\n  general"
    assert rrc_client.refresh_rooms()
    assert P.parse(link.sent[-1])[P.K_BODY] == "/list"
    rrc_client._on_packet(_env(P.T_NOTICE, body=body))
    assert g.lines == [], g.lines                       # not printed...
    assert g.rooms[-1] == [("varna", "chat here"), ("general", "")]   # ...but the picker has it
    # Only that one reply is quiet: a /list the user types is still answered
    # on screen, and so is every other notice.
    rrc_client._on_packet(_env(P.T_NOTICE, body=body))
    assert len(g.lines) == 1 and g.lines[0][0] == "notice", g.lines
    print("ok test_a_picker_refresh_does_not_reprint_the_room_list")


def test_a_quiet_refresh_swallows_only_a_room_list_and_expires():
    g, link = _session()
    g.lines = []
    assert rrc_client.refresh_rooms()
    rrc_client._on_packet(_env(P.T_NOTICE, body="Welcome to the hub"))
    assert [l[2] for l in g.lines] == ["Welcome to the hub"], g.lines
    # A reply that never came must not swallow a list asked for much later.
    rrc_client._quiet_list_until = rrc_client.time.time() - 1
    rrc_client._on_packet(_env(P.T_NOTICE, body="Registered public rooms:\n  varna"))
    assert len(g.lines) == 2, g.lines
    assert rrc_client._quiet_list_until == 0
    print("ok test_a_quiet_refresh_swallows_only_a_room_list_and_expires")


if __name__ == "__main__":
    for name in list(globals()):
        if name.startswith("test_"):
            globals()[name]()
    print("all rrc_client tests passed")
