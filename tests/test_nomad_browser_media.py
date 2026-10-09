# Host-side test: the inline-image /media request must include a "key" field.
# Reference NomadNet's serve_media rejects requests without it
# (nomadnet/Node.py: `if not "key" in data: return None`), so an image fetch
# from a reference node returns nothing unless we send the key. Run:
#   python3 tests/test_nomad_browser_media.py

import os
import sys
import types
import asyncio as _real_asyncio
import time as _time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_time.ticks_ms = lambda: int(_time.time() * 1000)
_time.ticks_diff = lambda a, b: a - b

# uasyncio shim: map sleep_ms -> real asyncio.sleep so the coroutine can run.
_uasyncio = types.ModuleType("uasyncio")
_uasyncio.sleep_ms = lambda ms: _real_asyncio.sleep(ms / 1000)
_uasyncio.create_task = _real_asyncio.create_task
sys.modules["uasyncio"] = _uasyncio

# urns.link shim: _fetch_image only needs OutgoingLink.ACTIVE.
_urns = types.ModuleType("urns")
_urns.__path__ = []
_urns_link = types.ModuleType("urns.link")


class OutgoingLink:
    ACTIVE = 3


_urns_link.OutgoingLink = OutgoingLink
sys.modules["urns"] = _urns
sys.modules["urns.link"] = _urns_link

import nomad_browser

_failures = []


def check(cond, name, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  ->  " + detail))
    if not cond:
        _failures.append(name)


def test_pages_wrap_to_the_panels_columns():
    """The Pro's panel is 30 columns, the v1's 40. A page wrapped to 40
    on the Pro loses the right third of every line off the panel edge, so
    the wrap width has to be the UI's COLS, read when the page renders."""
    page = "the quick brown fox jumps over the lazy dog " * 3
    saved = sys.modules.get("ui")
    try:
        sys.modules["ui"] = types.SimpleNamespace(COLS=30)
        lines, _links = nomad_browser._render_page(page)
        widest = max(sum(len(sp[1]) for sp in row) for row in lines)
        check(widest <= 30 and widest > 20, "a page wraps to a 30-column panel",
              "widest row %d" % widest)
        sys.modules["ui"] = types.SimpleNamespace(COLS=40)
        lines, _links = nomad_browser._render_page(page)
        widest = max(sum(len(sp[1]) for sp in row) for row in lines)
        check(widest <= 40 and widest > 30, "and to a 40-column one",
              "widest row %d" % widest)
    finally:
        if saved is None:
            sys.modules.pop("ui", None)
        else:
            sys.modules["ui"] = saved


def test_fetch_image_request_includes_key():
    captured = {}

    def fake_request(path, data=None, response_callback=None, failed_callback=None,
                     progress_callback=None, max_response_size=None):
        captured["path"] = path
        captured["data"] = data
        # Simulate the media response arriving so the fetch loop completes.
        nomad_browser._on_response(b"\x00" * 16, b"IMGBYTES")
        return b"\x00" * 16

    g = types.SimpleNamespace(
        browser_status="", dirty=False, transfer_progress=None)
    g.set_transfer = lambda r: None
    g.clear_transfer = lambda: setattr(g, "transfer_progress", None)
    nomad_browser._gui = g
    nomad_browser._link = types.SimpleNamespace(
        status=OutgoingLink.ACTIVE, request=fake_request)
    nomad_browser._fetching = False
    nomad_browser._result = None

    out = _real_asyncio.run(nomad_browser._fetch_image(":/media/logo.webp"))

    check(out == b"IMGBYTES", "fetch returns the media bytes", repr(out))
    check(captured.get("path") == "/media", "request endpoint is /media",
          repr(captured.get("path")))
    data = captured.get("data")
    check(isinstance(data, dict) and "key" in data,
          "request data includes 'key' (reference serve_media requires it)",
          repr(data))
    check(isinstance(data, dict) and data.get("path") == "/media/logo.webp",
          "request data carries the media path", repr(data))


if __name__ == "__main__":
    for _k, _v in sorted(globals().items()):
        if _k.startswith("test_"):
            _v()
    print("\n%d checks failed" % len(_failures) if _failures
          else "\nall nomad_browser media tests passed")
    raise SystemExit(1 if _failures else 0)
