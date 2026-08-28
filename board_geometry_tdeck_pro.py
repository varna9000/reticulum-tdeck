# Screen geometry for the LilyGO T-Deck Pro.
#
# Deploying to a Pro copies this file to board_geometry.py, which ui.py imports
# if it is present. The T-Deck v1 ships without that file and falls back to its
# own 320x240 landscape numbers, so one ui.py serves both boards.
#
# The Pro's panel is 240x320 portrait: 30 columns instead of 40, but 17 body
# rows instead of 12.

SCREEN_W = 240
SCREEN_H = 320
