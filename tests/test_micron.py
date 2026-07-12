# Micron renderer tests (host-side). Run:
#   python3 tests/test_micron.py

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import micron
from micron import render, color565


def _text(lines):
    """Flatten rendered rows back to plain strings (for content asserts)."""
    out = []
    for row in lines:
        s = ""
        for col, text, fg, bg, link in row:
            s += " " * (col - len(s)) + text
        out.append(s)
    return out


def test_color565():
    assert color565("fff") == 0xFFFF
    assert color565("000") == 0x0000
    assert color565("f00") == 0xF800  # pure red
    assert color565("0f0") == 0x07E0  # pure green
    assert color565("00f") == 0x001F  # pure blue
    assert color565("g99") == 0xFFFF  # grayscale white
    assert color565("g00") == 0x0000
    assert color565("zzz") is None
    assert color565("") is None
    assert color565("ab") is None


def test_plain_text_and_wrap():
    lines, links = render("hello world", width=40)
    assert _text(lines) == ["hello world"]
    assert links == []

    # word wrap at width
    lines, _ = render("aaa bbb ccc", width=7)
    assert _text(lines) == ["aaa bbb", "ccc"]

    # over-long word hard-breaks
    lines, _ = render("x" * 25, width=10)
    assert _text(lines) == ["x" * 10, "x" * 10, "x" * 5]

    # leading indentation preserved, wrap-continuation spaces dropped
    lines, _ = render("  indented text here", width=12)
    assert _text(lines)[0] == "  indented"
    assert _text(lines)[1] == "text here"


def test_unicode_sanitized():
    # Cyrillic must never reach the display layer (font/driver constraint)
    lines, _ = render("Здравей hello свят", width=40)
    flat = _text(lines)
    assert all(all(32 <= ord(c) < 127 for c in s) for s in flat)
    assert "hello" in flat[0]


def test_comments_skipped():
    lines, _ = render("#!c=30\n# comment\nvisible", width=40)
    assert _text(lines) == ["visible"]


def test_headings():
    lines, _ = render(">Title\n>>Sub\n>>>Deep", width=20)
    # h1: full-width inverted bar
    assert lines[0][0][3] == micron.FG_H1        # bg = H1 color
    assert lines[0][0][2] == 0x0000              # fg = black
    assert "Title" in lines[0][0][1]
    assert len(lines[0][0][1]) == 20
    # h2/h3: colored fg on default bg
    assert lines[1][0][2] == micron.FG_H2
    assert lines[2][0][2] == micron.FG_H3


def test_dividers():
    lines, _ = render("-\n-=", width=10)
    assert lines[0][0][1] == "-" * 10
    assert lines[1][0][1] == "=" * 10
    assert lines[0][0][2] == micron.FG_DIM


def test_literal_mode():
    src = "`=\n`F222 not a tag # not a comment\n`=\nafter"
    lines, _ = render(src, width=40)
    flat = _text(lines)
    assert flat[0] == "`F222 not a tag # not a comment"
    assert flat[1] == "after"


def test_inline_colors_and_reset():
    lines, _ = render("`F222red`f plain", width=40)
    row = lines[0]
    assert row[0][1] == "red"
    assert row[0][2] == color565("222")
    # after `f the color resets
    assert row[-1][2] == micron.FG_DEFAULT

    # background color + reset-all
    lines, _ = render("`B333on bg`` clean", width=40)
    row = lines[0]
    assert row[0][3] == color565("333")
    assert row[-1][3] == micron.BG_DEFAULT


def test_state_persists_across_lines():
    lines, _ = render("`F222\nstill colored\n``\nplain", width=40)
    assert lines[0][0][2] == color565("222")
    assert lines[1][0][2] == micron.FG_DEFAULT


def test_bold_maps_to_white():
    lines, _ = render("`!bold`! normal", width=40)
    assert lines[0][0][2] == micron.FG_BOLD
    assert lines[0][-1][2] == micron.FG_DEFAULT


def test_alignment():
    lines, _ = render("`cmid", width=10)
    assert lines[0][0][0] == 3  # (10-3)//2
    lines, _ = render("`rend", width=10)
    assert lines[0][0][0] == 7
    lines, _ = render("`r`aleft", width=10)
    assert lines[0][0][0] == 0


def test_links():
    src = "`[Board`:/page/board.mu] and `[:/page/raw.mu]"
    lines, links = render(src, width=40)
    assert links == [(":/page/board.mu", "Board"), (":/page/raw.mu", ":/page/raw.mu")]
    row = lines[0]
    link_spans = [s for s in row if s[4] is not None]
    assert len(link_spans) >= 2
    assert link_spans[0][4] == 0 and "Board" in link_spans[0][1]
    assert link_spans[0][2] == micron.FG_LINK

    # cross-node link with fields section (fields ignored)
    src2 = "`[Chat`abcdef0123456789abcdef0123456789:/page/c.mu`user] x"
    _, links2 = render(src2, width=60)
    assert links2 == [("abcdef0123456789abcdef0123456789:/page/c.mu", "Chat")]


def test_unterminated_link_survives():
    lines, links = render("`[broken no close\nnext line", width=40)
    assert links == []
    assert _text(lines)[-1] == "next line"


def test_input_field_placeholder():
    lines, _ = render("Name: `<user`> done", width=40)
    flat = _text(lines)[0]
    assert "[......]" in flat
    assert "done" in flat


def test_unknown_tags_stripped():
    lines, _ = render("a`Zb `q c", width=40)
    assert _text(lines) == ["ab  c"]


def test_empty_and_tag_only_lines():
    lines, _ = render("a\n\nb\n`F222\nc", width=40)
    flat = _text(lines)
    assert flat[0] == "a"
    assert flat[1] == ""
    assert flat[2] == "b"
    assert flat[3] == "c"  # tag-only line emits no row


def test_truncation_cap():
    src = "\n".join("line %d" % i for i in range(micron.MAX_LINES + 50))
    lines, _ = render(src, width=40)
    assert len(lines) == micron.MAX_LINES + 1
    assert "truncated" in lines[-1][0][1]


def test_realistic_page():
    src = (
        "#!c=30\n"
        ">T-Deck Node\n"
        "\n"
        "Welcome to `F0f0my node`f running on a `!T-Deck`!.\n"
        "-\n"
        ">>Pages\n"
        "`[Message board`:/page/board.mu]\n"
        "`[Files`:/page/files.mu]\n"
        ">>Info\n"
        "Uptime: 12345s\n"
        "`cCentered footer\n"
    )
    lines, links = render(src, width=40)
    assert len(links) == 2
    flat = _text(lines)
    assert any("Welcome" in s for s in flat)
    assert any(s.startswith("-") for s in flat)
    # every span fits the display width
    for row in lines:
        for col, text, fg, bg, link in row:
            assert col + len(text) <= 40, (col, text)


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
