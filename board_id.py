# Which board this install runs on. Read by board.py.
#
# This file is written by the deploy script, not edited by hand, and it is what
# selects the whole hardware layer. The checked-in value is the T-Deck v1,
# which is what upstream ships and what every existing install is; the T-Deck
# Pro deploy overwrites it with "tdeck_pro".
#
# It is a marker rather than a hardware probe on purpose. Probing means driving
# pins before anything knows which board they belong to, and the two boards
# disagree about what those pins are: GPIO 3 is a trackball axis on the v1 and
# the LoRa chip select on the Pro.

BOARD = "tdeck_v1"
