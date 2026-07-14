# NomadNet page browser controller for the T-Deck.
#
# Owns node discovery (nomadnetwork.node announces), the outgoing link to
# the node being browsed, and the page fetch state machine. The UI layer
# (ui.py) renders what this module hands it via gui.show_page() and the
# NET tab's gui.nomad_nodes; tdeck_node.py wires the gui.on_* callbacks
# to the functions below.
#
# Fetch flow: recall identity -> ensure path -> OutgoingLink ->
# link.request(path) -> micron.render() -> gui.show_page(). Responses
# larger than one packet arrive as a Resource (handled inside urns).

import gc
import time

MAX_NODES = 16
PATH_WAIT = 30        # seconds to wait for a path after request_path()
FETCH_CAP = 600       # outer safety cap; link layer handles real timeouts
INDEX_PAGE = "/page/index.mu"

_gui = None
_link = None          # OutgoingLink to the node being browsed
_link_dest = None     # dest_hash the current link points at
_fetching = False
_result = None        # None | ("ok", data) | ("fail", reason)
_history = []         # [(dest_hash, path), ...]; last entry = current page
_seeded = False

# Rendered-page cache for instant back-navigation (LoRa fetches are slow).
# (dest_hash, path) -> (title, lines, links); small LRU, cheap on 8MB PSRAM.
_PAGE_CACHE_MAX = 3
_page_cache = {}
_page_cache_order = []


def _cache_page(dest_hash, path, title, lines, links):
    key = (dest_hash, path)
    if key in _page_cache:
        _page_cache_order.remove(key)
    _page_cache[key] = (title, lines, links)
    _page_cache_order.append(key)
    while len(_page_cache_order) > _PAGE_CACHE_MAX:
        old = _page_cache_order.pop(0)
        _page_cache.pop(old, None)


def init(gui):
    """Hook announce observation. Call once at boot, after Reticulum init."""
    global _gui
    _gui = gui
    from urns.transport import Transport
    Transport.register_announce_handler(_on_announce)


# --- Node discovery ---------------------------------------------------------

def _nomad_hash(dest_hash):
    """The nomadnetwork.node hash for the identity behind dest_hash, or None."""
    from urns.identity import Identity
    from urns.destination import Destination
    data = Identity.known_destinations.get(dest_hash)
    if data and data[2]:
        id_hash = Identity.truncated_hash(data[2])
        return Destination.hash(id_hash, "nomadnetwork", "node")
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


def _decode_name(app_data):
    if app_data:
        try:
            return app_data.decode("utf-8")
        except Exception:
            pass
    return None


def _on_announce(dest_hash, app_data, packet):
    """Transport announce observer — collect nomadnetwork.node announces."""
    if _nomad_hash(dest_hash) != dest_hash:
        return
    _gui.add_nomad_node(dest_hash, _decode_name(app_data),
                        hops=_node_hops(dest_hash))
    _gui.wake_screen()


def seed_nodes():
    """Populate the NET tab from persisted announces (first tab open)."""
    global _seeded
    if _seeded:
        return
    _seeded = True
    from urns.identity import Identity
    for dh in Identity.known_destinations:
        if _nomad_hash(dh) == dh:
            entry = Identity.known_destinations[dh]
            _gui.add_nomad_node(dh, _decode_name(entry[3]),
                                hops=_node_hops(dh), seen=entry[0])


def clear_nodes():
    """Interface switched — reachability changed, start over."""
    global _seeded
    _seeded = False
    _history.clear()
    _page_cache.clear()
    _page_cache_order.clear()
    _teardown()
    if _gui:
        _gui.clear_nomad_nodes()


# --- Link lifecycle ---------------------------------------------------------

def _teardown():
    global _link, _link_dest
    link = _link
    _link = None
    _link_dest = None
    if link is not None:
        try:
            link.teardown()
        except Exception:
            pass


def _on_link_closed(link):
    global _link, _link_dest
    if link is _link:
        _link = None
        _link_dest = None


# --- Fetching ---------------------------------------------------------------

def browse(dest_hash, path=INDEX_PAGE):
    """GUI: open a page on a node (NET tab click / link follow)."""
    import uasyncio as asyncio
    asyncio.create_task(_fetch_task(dest_hash, path, push=True))


def follow(url):
    """GUI: follow a micron link from the current page."""
    if not _history:
        return
    cur_dest = _history[-1][0]
    dest = cur_dest
    path = url
    if ":" in url:
        hexpart, _, path = url.partition(":")
        if hexpart:
            try:
                from binascii import unhexlify
                dest = unhexlify(hexpart)
                if len(dest) != 16:
                    raise ValueError
            except Exception:
                _status("bad link")
                return
    if not path.startswith("/"):
        path = "/" + path
    browse(dest, path)


def back():
    """GUI: go back one page. Uses the rendered-page cache for an instant
    result; falls back to a re-fetch on a cache miss."""
    import uasyncio as asyncio
    if len(_history) < 2:
        return False
    _history.pop()
    dest_hash, path = _history[-1]
    cached = _page_cache.get((dest_hash, path))
    if cached is not None:
        title, lines, links = cached
        _gui.browser_status = None
        _gui.show_page(title, path, lines, links, can_back=len(_history) > 1)
        _gui.dirty = True
        return True
    asyncio.create_task(_fetch_task(dest_hash, path, push=False))
    return True


def refresh():
    """GUI: re-fetch the current page, keeping scroll position."""
    import uasyncio as asyncio
    if _history:
        dest_hash, path = _history[-1]
        asyncio.create_task(_fetch_task(dest_hash, path, push=False, keep_pos=True))


def browser_exit():
    """GUI: user left the browser — free the link."""
    _teardown()
    _history.clear()
    _gui.browser_status = None


def _status(text):
    _gui.browser_status = text
    _gui.dirty = True


def _on_response(request_id, data):
    global _result
    _result = ("ok", data)


def _on_req_failed(request_id):
    global _result
    _result = ("fail", "no response")


def _on_progress(resource):
    _gui.transfer_progress = (resource.received_count, resource.total_parts)
    _gui._progress_dirty = True


async def _fetch_task(dest_hash, path, push, keep_pos=False):
    global _fetching
    if _fetching:
        _status("busy - fetch in progress")
        return
    _fetching = True
    try:
        await _fetch(dest_hash, path, push, keep_pos)
    except Exception as e:
        _status("error: " + str(e))
    _fetching = False
    _gui.transfer_progress = None
    _gui.wake_screen()
    _gui.dirty = True
    gc.collect()


async def _fetch(dest_hash, path, push, keep_pos=False):
    global _link, _link_dest, _result
    import uasyncio as asyncio
    from urns.identity import Identity
    from urns.transport import Transport

    # 1. Path + identity (the path response carries the announce, so a
    #    missing identity resolves together with the path).
    if not Transport.has_path(dest_hash) or Identity.recall(dest_hash) is None:
        _status("finding path...")
        Transport.request_path(dest_hash)
        for _ in range(PATH_WAIT):
            await asyncio.sleep(1)
            if Transport.has_path(dest_hash) and Identity.recall(dest_hash):
                break
        else:
            _status("no path to node")
            return
    identity = Identity.recall(dest_hash)
    if identity is None:
        _status("unknown identity")
        return

    # 2. Link (reuse while browsing the same node; auto re-link when the
    #    old one was stale-closed by the remote).
    from urns.link import OutgoingLink
    if _link is None or _link_dest != dest_hash or _link.status != OutgoingLink.ACTIVE:
        _teardown()
        _status("linking...")
        await asyncio.sleep_ms(50)  # let the status row draw before keygen
        from urns.destination import Destination
        dest = Destination(identity, Destination.OUT, Destination.SINGLE,
                           "nomadnetwork", "node")
        link = OutgoingLink(dest, closed_callback=_on_link_closed)
        _link = link
        _link_dest = dest_hash
        t0 = time.time()
        while link.status == OutgoingLink.PENDING and time.time() - t0 < link.establishment_timeout + 5:
            await asyncio.sleep_ms(200)
        if link.status != OutgoingLink.ACTIVE:
            _teardown()
            _status("link failed")
            return

    # 3. Request
    _status("requesting " + path.split("/")[-1])
    _result = None
    rid = _link.request(path, response_callback=_on_response,
                        failed_callback=_on_req_failed,
                        progress_callback=_on_progress)
    if rid is None:
        _status("request failed")
        return
    t0 = time.time()
    while _result is None and time.time() - t0 < FETCH_CAP:
        await asyncio.sleep_ms(200)
    if _result is None or _result[0] != "ok":
        _status(_result[1] if _result else "timeout")
        return

    # 4. Render
    data = _result[1]
    _result = None
    if isinstance(data, bytes) or isinstance(data, bytearray):
        try:
            data = bytes(data).decode("utf-8")
        except Exception:
            _status("bad page encoding")
            return
    if not isinstance(data, str):
        _status("unsupported response")
        return
    import micron
    lines, links = micron.render(data, 40)
    del data
    gc.collect()

    if push:
        if not _history or _history[-1] != (dest_hash, path):
            _history.append((dest_hash, path))
        if len(_history) > 16:
            _history.pop(0)

    node = _gui.nomad_nodes.get(dest_hash)
    title = (node.get("name") if node else None) or dest_hash.hex()[:8]
    _cache_page(dest_hash, path, title, lines, links)
    _gui.browser_status = None
    _gui.show_page(title, path, lines, links, can_back=len(_history) > 1,
                   keep_pos=keep_pos)
