# The T-Deck Pro's own node list and chrome: two-line rows a finger can hit,
# an actions sheet, icon tabs, an icon toolbar, and a top bar with the clock
# on the right. Drawing functions over the UI instance, the way rrc_ui.py
# works; ui.py hands over to these on a portrait panel and the T-Deck v1's
# image does not contain this file at all.
#
# Everything is laid out in native pixels for the 240x320 panel:
#
#   y   0..19   top bar      battery | radio state | clock
#   y  20..41   tab bar      four 60 px cells: icon + count
#   y  42..297  list         eight rows of 32 px
#   y 298..319  toolbar      four 60 px cells: icon + label
#
# The panel is 1-bit: "selected" is an ink block with paper marks.

# Constants first: ui.py imports this module at its very end and reads
# LIST_ROWS, and this module imports ui back just below.
LIST_ROWS = 8
ROW_H = 32
LIST_Y = 42
TAB_Y = 20
TAB_H = 22
TOOL_Y = 298
TOOL_H = 22
MARGIN = 16        # left margin: unread pill (line 1) and star (line 2)
DOTS_X = 216       # the "..." column starts here
INFO_R = 212       # right-hand second-line text ends here
NAME_COLS = 25
INFO_COLS = 22
SHEET_SLOT = 10    # ui._cache slot for the sheet

import time

import ui as _u
