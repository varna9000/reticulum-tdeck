# Render the messenger UI on a T-Deck Pro, without the network stack.
#
# tdeck_node.py owns the REPL once it starts, so this exists to answer the one
# question that is hard to see from outside: does a screen actually reach the
# panel, and what does one cost? It builds the real ui.UI on the real board,
# paints each screen and times the draw and the flush separately.
#
#   ./tools/deploy_pro.sh /dev/cu.usbmodem101
#   mpremote connect /dev/cu.usbmodem101 run tools/tdeck_pro_ui_smoke.py

import gc
import time

import board
import spleen_8x16 as font
from ui import UI, STATE_NODES, STATE_SETTINGS, STATE_CHAT

print("board:", board.BOARD)
print("trackball:", board.HAS_TRACKBALL, " audio:", board.HAS_AUDIO,
      " mic:", board.HAS_MIC, " jpeg splash:", board.HAS_JPEG_SPLASH)
print("panel:", board.tft.width, "x", board.tft.height)

gui = UI(board.tft, font, board.get_key, node_name="TDeckPro-smoke",
         trackball=board.HAS_TRACKBALL)
gui.set_backlight(board.bl)
board.attach_ui(gui)
gui._screen_on = True
gc.collect()
print("UI built, free:", gc.mem_free())
print("cache slots:", len(gui._cache))

for label, state in (("nodes", STATE_NODES),
                     ("settings", STATE_SETTINGS),
                     ("nodes again", STATE_NODES)):
    gui.state = state
    gui.dirty = True
    gc.collect()

    board.spi_acquire_display()
    t0 = time.ticks_ms()
    gui.draw()
    t1 = time.ticks_ms()
    gui._flush()
    t2 = time.ticks_ms()
    board.spi_release_display()

    print("%-12s draw %4d ms   flush %4d ms   free %d"
          % (label, time.ticks_diff(t1, t0), time.ticks_diff(t2, t1),
             gc.mem_free()))

# An unchanged screen must cost nothing: the row cache skips every row and the
# shim has no dirty rectangle to push. This is what makes the UI usable on a
# panel that takes most of a second to refresh.
board.spi_acquire_display()
t0 = time.ticks_ms()
gui.draw()
gui._flush()
t1 = time.ticks_ms()
board.spi_release_display()
print("repaint of an unchanged screen: %d ms" % time.ticks_diff(t1, t0))

print()
print("Type on the keyboard; each key is echoed. Press Esc to finish.")
_seen = []
while True:
    k = board.get_key()
    if k != b'\x00':
        if k == b'\x1b':
            break
        _seen.append(k)
        print("  key:", k)
    time.sleep_ms(20)
print("captured %d keystrokes:" % len(_seen), b''.join(_seen))
