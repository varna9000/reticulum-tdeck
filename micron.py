# Micron markup renderer for the T-Deck NomadNet browser.
#
# Pure Python (runs under CPython for tests and MicroPython on device).
# Renders a micron (.mu) page into fixed-width rows of styled spans ready
# for the 40-column display — no display or urns imports here.
#
#   render(text, width=40) -> (lines, links)
#
#   lines: list of rows; each row is a list of spans
#          (col, text, fg565, bg565, link_idx_or_None)
#   links: list of (url, label)
#
# Supported micron subset (unknown tags are stripped, never fatal):
#   #        comment lines (incl. #! page directives)
#   >, >>    headings (depth-styled; section indentation is ignored)
#   -        divider line (optional fill char: "-=")
#   `=       literal mode toggle
#   `Fxxx/`f foreground color (3 hex digits or gNN grayscale), reset
#   `Bxxx/`b background color, reset
#   `c/`l/`r/`a  alignment (center/left/right/default)
#   `!       bold (rendered as white — single font)
#   `*, `_   italic/underline (ignored — single font)
#   ``       reset all formatting
#   `[label`URL] / `[URL]  links (third `fields section ignored)
#   `<...`name`>  input fields -> non-interactive [____] placeholder
#
# All output text is sanitized to printable ASCII 32-126 plus basic
# Cyrillic (U+0400-U+045F): the display font (vga2_8x16_cp866) covers
# exactly those, and the UI transcodes codepoints to glyph bytes.

# RGB565 palette (matches ui.py aesthetics)
FG_DEFAULT = 0xC618  # light grey — body text
BG_DEFAULT = 0x0821  # BG_DARK
FG_H1      = 0xFFE0  # yellow
FG_H2      = 0x07E0  # neon green
FG_H3      = 0x07FF  # neon cyan
FG_LINK    = 0x07FF  # neon cyan
FG_DIM     = 0x0514  # dim cyan — dividers, field placeholders
FG_BOLD    = 0xFFFF  # white — `! replacement

MAX_LINES = 1000

_HEX = "0123456789abcdef"


def _rgb565(r8, g8, b8):
    return ((r8 & 0xF8) << 8) | ((g8 & 0xFC) << 3) | (b8 >> 3)


def color565(code):
    """Micron color code -> RGB565. 3 hex digits ("f00") or grayscale
    ("g00".."g99"). Returns None for anything unparseable."""
    if not code:
        return None
    code = code.lower()
    if len(code) == 3 and code[0] == "g":
        if code[1].isdigit() and code[2].isdigit():
            v = (int(code[1:]) * 255) // 99
            return _rgb565(v, v, v)
        return None
    if len(code) == 3:
        for c in code:
            if c not in _HEX:
                return None
        r = _HEX.index(code[0]) * 17
        g = _HEX.index(code[1]) * 17
        b = _HEX.index(code[2]) * 17
        return _rgb565(r, g, b)
    return None


def _clean(s):
    """Printable ASCII (32-126) plus basic Cyrillic (U+0400-U+045F, which
    the UI transcodes to font glyphs; rare unmapped ones render '?') —
    everything else is dropped."""
    return "".join(c for c in s if 32 <= ord(c) < 127 or 0x400 <= ord(c) <= 0x45F)


class _State:
    def __init__(self):
        self.fg = FG_DEFAULT
        self.bg = BG_DEFAULT
        self.bold = False
        self.align = "l"

    def reset(self):
        self.fg = FG_DEFAULT
        self.bg = BG_DEFAULT
        self.bold = False
        # alignment survives `` (matches nomadnet: reset with `a)

    def style(self):
        return (FG_BOLD if self.bold else self.fg), self.bg


def _flush_runs(runs, out, width, align):
    """Word-wrap styled runs [(text, fg, bg, link)] into span rows."""
    # Tokenize into words and spaces, preserving style per token.
    tokens = []
    for text, fg, bg, link in runs:
        i = 0
        n = len(text)
        while i < n:
            if text[i] == " ":
                j = i
                while j < n and text[j] == " ":
                    j += 1
                tokens.append((text[i:j], fg, bg, link, True))
                i = j
            else:
                j = i
                while j < n and text[j] != " ":
                    j += 1
                tokens.append((text[i:j], fg, bg, link, False))
                i = j

    rows = [[]]
    used = 0
    for tok, fg, bg, link, is_space in tokens:
        while tok:
            free = width - used
            if len(tok) <= free:
                # Drop spaces at the start of wrap-continuation rows, but
                # keep leading indentation on the first row.
                if is_space and used == 0 and len(rows) > 1:
                    break
                rows[-1].append((tok, fg, bg, link))
                used += len(tok)
                break
            if is_space:
                # spaces never wrap — start a fresh row, drop the rest
                rows.append([])
                used = 0
                break
            if len(tok) > width:
                # hard-break an over-long word
                if free > 0:
                    rows[-1].append((tok[:free], fg, bg, link))
                    tok = tok[free:]
                rows.append([])
                used = 0
                continue
            # word fits on a fresh row
            rows.append([])
            used = 0

    # Convert rows of runs into positioned spans (+ alignment offset).
    for row in rows:
        if not row and len(rows) == 1:
            out.append([])  # genuinely empty source line
            continue
        if not row:
            continue
        # Trim trailing spaces for alignment math
        while row and row[-1][0].strip() == "":
            row.pop()
        if not row:
            out.append([])
            continue
        rowlen = 0
        for t in row:
            rowlen += len(t[0])
        if align == "c":
            col = max(0, (width - rowlen) // 2)
        elif align == "r":
            col = max(0, width - rowlen)
        else:
            col = 0
        spans = []
        for text, fg, bg, link in row:
            if spans and spans[-1][2] == fg and spans[-1][3] == bg and spans[-1][4] == link:
                # merge with previous span (same style)
                prev = spans[-1]
                spans[-1] = (prev[0], prev[1] + text, fg, bg, link)
            else:
                spans.append((col, text, fg, bg, link))
            col += len(text)
        out.append(spans)


def _parse_inline(line, st, links, runs):
    """Consume inline micron tags in `line`, appending styled runs."""
    buf = ""
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if c != "`":
            buf += c
            i += 1
            continue
        # flush text before the tag
        if buf:
            fg, bg = st.style()
            runs.append((_clean(buf), fg, bg, None))
            buf = ""
        if i + 1 >= n:
            break
        t = line[i + 1]
        if t == "`":                      # `` reset all
            st.reset()
            i += 2
        elif t == "F":
            col = color565(line[i + 2:i + 5])
            if col is not None:
                st.fg = col
                i += 5
            else:
                i += 2
        elif t == "B":
            col = color565(line[i + 2:i + 5])
            if col is not None:
                st.bg = col
                i += 5
            else:
                i += 2
        elif t == "f":
            st.fg = FG_DEFAULT
            i += 2
        elif t == "b":
            st.bg = BG_DEFAULT
            i += 2
        elif t == "!":
            st.bold = not st.bold
            i += 2
        elif t in "*_":                   # italic/underline: single font
            i += 2
        elif t in "clra":
            st.align = "l" if t == "a" else t
            i += 2
        elif t == "[":                    # link `[label`URL`fields]
            end = line.find("]", i + 2)
            if end < 0:
                i += 2
                continue
            parts = line[i + 2:end].split("`")
            if len(parts) == 1:
                label, url = parts[0], parts[0]
            else:
                label, url = parts[0], parts[1]
            url = _clean(url).strip()
            label = _clean(label).strip() or url
            if url:
                links.append((url, label))
                runs.append((label, FG_LINK, st.bg, len(links) - 1))
            i = end + 1
        elif t == "<":                    # input field -> placeholder
            end = line.find(">", i + 2)
            if end < 0:
                i += 2
                continue
            runs.append(("[......]", FG_DIM, st.bg, None))
            i = end + 1
        elif t == "=":
            # literal toggle mid-line: rare; treat rest of line literally
            buf += line[i + 2:]
            i = n
        else:                             # unknown tag — strip it
            i += 2
    if buf:
        fg, bg = st.style()
        runs.append((_clean(buf), fg, bg, None))


def render(text, width=40):
    """Render micron source to (lines, links). Never raises on content."""
    lines = []
    links = []
    st = _State()
    literal = False

    for raw_line in text.split("\n"):
        if len(lines) >= MAX_LINES:
            lines.append([(0, "(truncated)", FG_DIM, BG_DEFAULT, None)])
            break
        line = raw_line.rstrip("\r")

        if line.strip() == "`=":
            literal = not literal
            continue
        if literal:
            clean = _clean(line)
            if not clean:
                lines.append([])
                continue
            # hard-wrap literal text, no styling
            for o in range(0, len(clean), width):
                lines.append([(0, clean[o:o + width], FG_DEFAULT, BG_DEFAULT, None)])
            continue

        if line.startswith("#"):
            continue

        if line.startswith(">"):
            depth = 0
            while depth < len(line) and line[depth] == ">":
                depth += 1
            title = _clean(line[depth:]).strip()
            if depth == 1:
                # full-width inverted bar
                bar = (" " + title)[:width]
                bar = bar + " " * (width - len(bar))
                lines.append([(0, bar, 0x0000, FG_H1, None)])
            else:
                fg = FG_H2 if depth == 2 else FG_H3
                lines.append([(0, title[:width], fg, BG_DEFAULT, None)])
            continue

        if line.startswith("-"):
            fill = line[1] if len(line) > 1 else "-"
            fill = _clean(fill) or "-"
            lines.append([(0, fill * width, FG_DIM, BG_DEFAULT, None)])
            continue

        if line == "":
            lines.append([])
            continue

        runs = []
        _parse_inline(line, st, links, runs)
        if not runs:
            continue  # tag-only line (e.g. a bare alignment/color set)
        _flush_runs(runs, lines, width, st.align)

    return lines, links
