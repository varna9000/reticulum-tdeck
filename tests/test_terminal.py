# Tests for terminal.py — the scrolling text-log renderer.
# Run:  python3 tests/test_terminal.py

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from terminal import Terminal


def test_plain_lines():
    t = Terminal(cols=40)
    t.feed(b"hello\nworld\n")
    assert t.lines[:3] == ["hello", "world", ""], t.lines


def test_carriage_return_overwrite():
    t = Terminal(cols=40)
    t.feed(b"progress 50%\rprogress 100%")
    assert t.lines[-1] == "progress 100%", t.lines


def test_backspace():
    t = Terminal(cols=40)
    t.feed(b"abcX")
    t.feed(b"\b \b")   # typical erase: back, space, back (leaves a trailing space)
    assert t.lines[-1].rstrip() == "abc", repr(t.lines[-1])
    assert t.cur_col == 3
    # overwriting after backspace works
    t.feed(b"Z")
    assert t.lines[-1][:4] == "abcZ", repr(t.lines[-1])


def test_tab_stops():
    t = Terminal(cols=40)
    t.feed(b"a\tb")
    assert t.lines[-1] == "a       b", repr(t.lines[-1])   # tab to col 8


def test_wrap():
    t = Terminal(cols=8)
    t.feed(b"0123456789AB")
    assert t.lines[-2] == "01234567", t.lines
    assert t.lines[-1] == "89AB", t.lines


def test_strip_sgr_colors():
    t = Terminal(cols=40)
    t.feed(b"\x1b[31mRED\x1b[0m done")
    assert t.lines[-1] == "RED done", repr(t.lines[-1])


def test_clear_screen():
    t = Terminal(cols=40)
    t.feed(b"junk\nmore\n")
    t.feed(b"\x1b[2J\x1b[H")   # clear + home
    t.feed(b"fresh")
    assert t.lines == ["fresh"], t.lines


def test_erase_in_line():
    t = Terminal(cols=40)
    t.feed(b"keep this XXXX")
    # move cursor back 4 and erase to end
    t.feed(b"\b\b\b\b\x1b[K")
    assert t.lines[-1] == "keep this ", repr(t.lines[-1])


def test_osc_stripped():
    t = Terminal(cols=40)
    t.feed(b"\x1b]0;window title\x07prompt$ ")
    assert t.lines[-1] == "prompt$ ", repr(t.lines[-1])


def test_incremental_utf8():
    t = Terminal(cols=40)
    # 'Привет' in UTF-8, fed split across chunks mid-character
    data = "Привет".encode("utf-8")
    t.feed(data[:3])
    t.feed(data[3:])
    assert t.lines[-1] == "Привет", repr(t.lines[-1])


def test_scrollback_bound():
    t = Terminal(cols=40, max_lines=10)
    for i in range(50):
        t.feed(("line%d\n" % i).encode())
    assert len(t.lines) <= 10
    assert t.lines[0].startswith("line4")   # oldest kept ~ line41
    print(t.lines[0])


if __name__ == "__main__":
    for name in list(globals()):
        if name.startswith("test_"):
            globals()[name]()
            print("ok", name)
    print("all terminal tests passed")
