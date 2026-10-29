# Porting the messenger to the LilyGO T-Deck Pro

Tracking issue: [#1](https://github.com/varna9000/reticulum-tdeck/issues/1).

The Pro is a different machine from the T-Deck v1 rather than a revision of it.
Same MCU and same radio, everything else changed:

| | T-Deck v1 | T-Deck Pro |
|---|---|---|
| Display | ST7789 TFT, 320x240 landscape | GDEQ031T10 e-ink (UC8253), 240x320 portrait |
| Keyboard | ESP32-C3 co-processor at 0x55, returns ASCII | TCA8418 raw 4x10 matrix scanner |
| Pointing | trackball, 5 GPIOs with interrupts | none (capacitive touch only) |
| Radio bus | display and radio share MOSI | different MOSI pins, shared SCK |
| PSRAM | 8 MB octal | 8 MB **quad** (QSPI) |
| Battery | ADC through a divider | BQ27220 fuel gauge over I2C |
| Audio | MAX98357A + ES7210 mic | PCM5102A DAC, no mic |

## What is done

Host-side tests pass, and the display and radio are confirmed working on real
hardware (2026-08-28, MicroPython v1.24.1).

- `lib/eink_gdeq031t10.py` -- UC8253 panel driver, ported from GxEPD2 1.6.9.
- `lib/eink_shim.py` -- the five-method surface `ui.py` draws through, over a
  1-bit framebuffer, plus the refresh batching policy.
- `lib/tca8418.py` -- key matrix driver with the Pro's keymap, taken from
  Meshtastic's `TDeckProKeyboard.cpp`.
- `board_tdeck_pro.py` -- power gates, SPI arbitration, wires the three above.
- `board.py`, `board_id.py`, `board_tdeck_v1.py` -- the board layer that lets
  one `tdeck_node.py` serve both machines.
- `tdeck_pro_config.py` -- pin map.
- `board_geometry_tdeck_pro.py` -- 240x320, copied to `board_geometry.py` on
  deploy.
- `lora_boards.py` -- `tdeck_pro_sx1262` preset (in the uP-reticulum submodule).
- `tools/tdeck_pro_bringup.py` -- hardware bring-up, run this first.
- `tools/deploy_pro.sh` -- deploys the full app onto stock MicroPython.
- `tests/test_eink_shim.py`, `tests/test_tca8418.py`,
  `tests/test_board_geometry.py`, `tests/test_board.py`,
  `tests/test_ui_flush.py` -- host-side checks, 14 suites in all.

## The board layer

`tdeck_node.py` used to bring up the T-Deck v1 inline in its first 230 lines.
It now imports `board`, and `board.py` re-exports one of two implementations:

    board_id.BOARD  ->  board_tdeck_v1  |  board_tdeck_pro

The re-export list at the bottom of `board.py` is the contract, written out by
name rather than with `import *` so that a board missing a piece fails at
import instead of at first use. It covers the config module, the shared SPI bus
and its arbitration, the display surface and its `flush`, the keyboard, and
four traits the app branches on: `HAS_TRACKBALL`, `HAS_AUDIO`, `HAS_MIC`,
`HAS_JPEG_SPLASH`.

`board_id.py` is a marker the deploy script writes, not a hardware probe.
Probing would mean driving pins before anything knows which board they belong
to, and the two boards disagree about what those pins are -- GPIO 3 is a
trackball axis on the v1 and the LoRa chip select on the Pro. It is checked in
as `tdeck_v1`, which is what every existing install is, and `tools/deploy_pro.sh`
overwrites it on a Pro.

Two calls the Pro needs and the v1 does not, both no-ops on the v1:

- `board.attach_ui(gui)` right after the UI is constructed, or the arrow keys
  go nowhere.
- `board.flush()` / `UI._flush()` once per rendered screen. Nothing reaches the
  panel until something flushes.

## Changes to shared code

All are backward compatible, and the existing v1 test suites still pass.

**`ui.py` layout constants are now derived from screen size.** They used to be
hardcoded for 320x240. `ui.py` imports `board_geometry` if it exists and falls
back to the v1 numbers otherwise. The v1 values come out byte-identical
(`COLS=40`, `INPUT_Y=224`, `SEP_Y=222`, `BODY_ROWS=12`); the Pro reflows to
`COLS=30`, `BODY_ROWS=17`.

**`UI.__init__` takes `trackball=True`.** This is not cosmetic. The trackball
block claims GPIO 0, 1, 2, 3 and 15 as pulled-up inputs with falling-edge
interrupts. On the Pro those pins are the side button, GPS PPS, the vibration
motor, **LoRa chip select** and the keyboard interrupt. Running it unmodified
takes the radio's CS line away and the radio never works. Boards passing
`trackball=False` drive the same counters through the new `UI.nav_event()`.

**The row cache is sized from the panel.** `ui.py` skips drawing a row whose
text has not changed, keyed by slot in a fixed 15-entry list: navbar, 12 body
rows, footer, input. The Pro has 17 body rows, which walked off the end of that
list and collided the footer with a body row. The slots are now `CACHE_ROWS`,
`FOOT_SLOT` and `INPUT_SLOT`, derived from `BODY_ROWS`; on the v1 they come out
15, 13 and 14, the constants they replace.

**`UI` flushes the panel once per rendered screen.** `UI._flush()` calls
`tft.flush()` where the display object has one and does nothing where it does
not, so the v1's ST7789 is untouched. Every call site sits inside the
`spi_acquire_display()` / `spi_release_display()` window, because on e-ink the
flush *is* the SPI transfer. `tests/test_ui_flush.py` holds that: drawing
alone never reaches the panel, and no flush happens with the bus released.

**The keyboard backlight follows the screen where nothing else can show it.**
`sleep_screen()` dims the display backlight and `wake_screen()` brings it back,
which on the v1 both saves the power and shows the state. The Pro has no
display backlight at all: `board.bl` is `None`, e-ink holds its last frame with
no power, and a sleeping deck is indistinguishable from a crashed one. So where
`self._bl is None`, the two transitions drive the keyboard light instead. It
becomes the only visible sign the deck is awake, and it is the largest
discretionary draw on the board, which makes the inactivity timeout worth
something on the Pro for the first time.

Two things this keeps separate. The user's preference is the master, held in
`_kbd_bl`, and a wake never lights a deck whose owner turned the light off;
`_kbd_bl_lit` is what the hardware is actually doing. And sleep and wake go
through a new `on_kbd_backlight_drive` rather than `on_kbd_backlight`, because
the latter persists the setting -- routing an inactivity timeout through it
would rewrite `settings.json` on every idle and every keypress that wakes the
deck. `tests/test_kbd_backlight_sleep.py` holds both, and holds that a board
with a display backlight keeps its old behaviour.

## Three things worth knowing

**The panel is slow, so drawing must never touch it.** A partial refresh is
about 700 ms and a full one about 1100 ms. `ui.py` makes 86 `text()` calls to
paint one screen. The shim therefore accumulates dirty rectangles and only
talks to the panel on `flush()`, which the app calls once per rendered screen.
A full refresh is the whole-panel black/white flash, so the shim spends it
on a screen change (`ui.py` flushes with `force_full` when the state
changes), on a sizeable user redraw (`flush(user=True)`, set after a key
press; at least 1/8 of the panel) that finds a ghost-clearing debt owed
after 10 partials, and at a hard cap of 40 partials as the backstop. Nothing
else flashes: a cursor step, a one-line scroll that shifts every row, an
announce landing in the node list, the navbar clock ticking -- each is one
quiet partial of the rows that changed, however far apart or large. Area
alone never decides, because a partial costs the same ~700 ms whatever its
window and the panel drives only the pixels that differ. Before this, the
tab count at the top and a new peer near the bottom spanned more than half
the panel and flashed on every new announce, and later every line scroll
flashed for the same reason.

**Photos are dithered, not thresholded.** The full-screen image viewer
(chat attachments, a NomadNet image opened from its placeholder row) decodes
to RGB565 as on the v1 and then, on a mono panel, hands the buffer to
`EinkShim.blit_dither`: Floyd-Steinberg on 8-bit luma, dark = ink, in a viper
inner loop (a few hundred ms for the whole panel). The shim's plain
`blit_buffer` is the wrong tool for a picture twice over -- no grey, and the
UI's inverted polarity, so it showed a negative. Inline images inside browser
pages are still not rendered on the Pro; only the placeholder row and the
full-screen view.

**Selected rows are inverted.** The selection tint `SEL_BG` is paper on
the threshold, indistinguishable from the background, so on a mono panel
`ui.py` sets it to a colour that is ink, and the shim draws glyphs on an ink
background as paper. Every selected row -- lists, Setup, chat cursor,
browser cursor, the RRC panels -- is then a black band with white text, and
the unread pill's digits (white on magenta, both ink before) became legible
by the same rule. The accent bars the colour UI draws beside a selected row
are ink-on-ink there and simply vanish.

**Voice messages record through a PDM microphone.** The Pro's mic is a
two-wire PDM part (MIC_CLOCK 18, MIC_DATA 17), which `machine.I2S` cannot
read, so `tools/c_modules/pdm_mic` opens I2S port 0 in PDM RX mode through
ESP-IDF's new driver (`driver/i2s_pdm.h`, the family `machine.I2S` already
uses, so they coexist) and hands back 16 kHz mono PCM. `sound.py`'s PDM path
halves that to 8 kHz with a 2-tap average and DC removal in a viper loop and
feeds the unchanged recording task and Codec2 encoder. Playback moves to I2S
port 1 on this board (`board.SPK_PORT`), because PDM receive exists on port
0 only. A PDM mic needs no priming: `prime_mic()` just drains the DMA ring.
`HAS_MIC` in the config gates all of it.

**Sleep shows a sleep screen.** With no backlight to switch off, a sleeping
deck kept its last frame and looked awake, and the key that woke it (which
the keyboard loop swallows) looked ignored, so people pressed it twice.
`ui.sleep_screen()` on a board without a backlight pin paints a centred
frame -- node name, clock, "sleeping", "press any key" -- with a full
refresh (which also clears the ghosting of whatever was up), and
`wake_screen()` forces the next redraw full so the frame is replaced
cleanly. Boards with a backlight are untouched.

**The shell keeps its small fonts, one column short.** `ui._ShellFont`
composites rows into RGB565 pre-swapped for the ST7789, which the shim
reads in the same big-endian order, so its blit decodes correctly (bright =
ink). What went wrong on the Pro was width: 40 columns of the 6 px font
fill its 240 px exactly, and the scroll lane painted over the last column.
On a mono panel the grid is sized to stop short of the lane (39x22 at
6x12). The shim's `blit_buffer` is a viper loop for this: a Python
per-pixel blit of a 240x12 row per changed line made shell output crawl.

**The 1-bit panel has no dim and no tint.** The app marks its active tab and
its unlit battery segments with colour alone (teal vs dark background, dim vs
bright text), and on the shim's luminance threshold those pairs land on the
same side. Such states are drawn inverted on a mono panel instead: `ui.py`
checks `tft.mono` and paints the active tab as paper text on an ink block.

**The colours are inverted on purpose.** The app is dark themed and paints a
near-black background with light text. Rendered literally that floods an e-ink
panel with ink: slow, heavy ghosting, and ugly. The shim thresholds on
luminance and inverts, so a dark background becomes paper and light text
becomes ink.

**e and x move the selection where nothing is being typed.** Navigation is on
the alt layer behind a sticky modifier, which makes moving one row down a list
two keystrokes on a device whose main screen is a list. On a screen with no
text field there is nothing for a bare letter to collide with: the node list
binds a, s, b, m, d and p, the browser r, n, p, b, g and G, and neither takes
e or x. So `UI.handle_key()` maps them to `nav_event()` unless
`_accepting_text()` says otherwise -- chat, the shell, and the four settings
sub-pages that are text fields. Alt+E and alt+X keep working everywhere, so
nothing is taken away. Chat is the honest edge: it navigates message history
*and* types, so it is the one screen that wants this and cannot have it.

**There are no arrow keys.** Navigation is on the alt layer: E, X, S, F are
up, down, left, right. `board_tdeck_pro.get_key()` consumes those and calls
`UI.nav_event()` instead of passing them through, so every navigation path in
`ui.py` stays identical across both boards. Modifiers are sticky with a 1.5 s
timeout, matching the vendor firmware.

## Confirmed on hardware

**Meshtastic's `PIN_EINK_MOSI 47` is wrong.** The panel's data line is GPIO
**33**, the same MOSI the radio uses. Driving the panel on 47 produced no
response at all; on 33 it held BUSY low for 3.07 s, a real refresh cycle.
Commands travel over MOSI too, so a panel on the wrong data pin receives
nothing and never asserts BUSY, which makes this an unambiguous test. Display
and radio therefore share one pin set, exactly as on the v1, and only the clock
rate changes between them.

**I2C is on SDA 13 / SCL 14.** `variant.h` never names these; it defers to the
Arduino core. Found by probing candidate pairs. Devices on the bus:

| Address | Device |
|---|---|
| 0x1A | CST328 touch, or CST3530 on later V1.1 boards (issue #19) |
| 0x28 | unidentified |
| 0x34 | TCA8418 keyboard |
| 0x55 | BQ27220 fuel gauge |
| 0x5A | DRV2605 haptics |
| 0x6B | BQ25896 PMU |

**The radio works on the pin map above.** A write/read round trip on the
sync-word registers returned what was written (`1424`), and `GetStatus` answered
`0x2a`.

**Panel timings match the datasheet.** Measured through the driver: 1270 ms for
a full refresh (spec 1100 ms plus power-on and transfer) and 712 ms for a
partial (spec 700 ms). Text renders legibly as black on white, so the luminance
inversion is right.

**The radio is on the mesh.** Running `tools/tdeck_pro_headless.py`, the node
announces over LoRa and the Reticulum instance on another host learns a route
to it:

```
<694f9404fc6df2a80077aaf4d3b167e5> is 1 hop away via
<694f9404...> on RNodeInterface[RNode LoRa Interface]
```

Three things cost time getting there, all worth knowing:

- **`lib/lora/` has to be deployed.** Without the SX126x driver the interface
  still registers and the node prints a healthy banner, but it is offline
  forever with `no module named 'lora'` and every announce is dropped in
  silence. `iface.txb` staying at 0 is the tell.
- **`LXMRouter.announce()` is a silent no-op until
  `register_delivery_identity()` has been called.** Setting `display_name` on
  the router is not enough. Same symptom as above, so the two are easy to
  confuse: check `txb` to tell them apart.
- **The modem throws `OpError 0x200` / `0x3d00` on the first init attempts** and
  comes up on a later retry. The interface's own retry loop handles it, so it
  is noise rather than a fault, but it looks alarming in the log.

## Two config choices: revision and audio (2026-10-09)

`tdeck_pro_config.py` carries two per-deck switches, both read by
`board_tdeck_pro.py` with safe fallbacks for an older config file:

- **`BOARD_REV`** (`"1.0"` default, or `"1.1"`). The revisions differ in two
  pins that matter at boot: v1.1 has a real panel reset line on GPIO 16
  (`DISP_RES`, pulsed by the driver) and no 1.8 V gate (`BOARD_1V8_EN` is
  None; GPIO 38 is its CST328 touch reset). A v1.1 on the v1.0 map boots to
  a blank panel -- the report in issue #1. Sources: Meshtastic's
  `t-deck-pro` and `t-deck-pro-v1_1` variants, and the schematic reading in
  that issue. v1.1 is untested here; the two decks this was written on are
  v1.0.
- **`HAS_AUDIO`** (`"auto"` default). The Pro ships either with the PCM5102A
  I2S DAC or with the A7682E 4G modem, on the same GPIO 7/8/9 (the modem's
  RI/ITR/RST). Set True on a DAC unit: `board.SPK_PINS` becomes LilyGO's
  BCLK 7 / LRC 9 / DOUT 8 and `sound.Sound.init()` takes them. Meshtastic's
  `PCM5102A_SCK 47 / DIN 17 / LRCK 18` is wrong (47 is the shared MISO), and
  so was the copy of it this file used to carry. The v1's `sound.py` pins
  (7/5/6) would land on the Pro's LoRa INT and BUSY, which is why the DAC
  was off until the pins moved into the board layer. The DAC also sits
  behind the module slot's power enable, GPIO 41 (`AUDIO_EN`; LilyGO's
  `BOARD_6609_EN`, which their `test_pcm5102a` example raises first), so
  `power_up()` raises it when `HAS_AUDIO`. Its speaker is small and
  near-silent at the v1's chirps (440-660 Hz, amplitude ~2400): a loudness
  and pitch ladder on hardware showed the same chirps an octave up at 8x the
  level carry fine, so the board hands `sound.py` `AUDIO_GAIN = 8` and
  `AUDIO_PITCH = 2` (config-editable) and voice playback is scaled by the
  gain with saturation. The v1 keeps 1 and 1. Mono is the right format: a
  mono/stereo/left/right comparison found mono loudest.

LilyGO's "T-Deck Pro (4G / I2S) V1.1 PINMAP" graphic is the best single
reference for a v1.1 and corrects a few things in this file's older notes:
there IS a microphone (MIC_DATA 17, MIC_CLOCK 18 -- two wires, so a PDM
part, which the S3's I2S can read; no driver here yet), the vibration motor
driver DRV2605 is on IO02 on v1.1, the touch reset moved to IO38, GPIO 45 is
a "pre-reserved backlight pin" with nothing on it (confirmed: driving it high
lights nothing), and the audio module supports earphone and speaker.

Both answers are found **once**: on the first boot with the defaults the
board scans I2C for the revision and, after the panel is up, powers the
module slot, pulses the modem's power key and sends AT for up to ~12 s
(LilyGO's factory firmware does the same) with a notice on the panel. The
result goes to `/tdeck_pro_hw.json` (`tdeck_pro_rev.PROFILE_PATH`) and every
later boot reads it back in milliseconds. A config value other than `"auto"`
overrides the saved one; deleting the file re-runs detection. A found modem
is powered back down (GPIO 41 low), since nothing here drives it.

## Touch controller: two chips at 0x1A

The touch driver (`lib/cst328.py`) handles both parts LilyGO has fitted. The
original CST328 is a register chip; later V1.1 batches carry the CST3530
(the CST328 went out of production), which speaks Hynitron's 4-byte
command-word protocol instead and auto-sleeps its I2C when idle. The two
are not told apart by board revision -- a V1.1 with a DRV2605 can carry
either -- but by the chip's own answer each time it leaves reset: the
CST3530 info probe (`D0 03 00 00`, 0xCA 0xCA in the reply) goes first,
LilyGO's own order, because a CST3530 ACKs CST328 register reads and the
other order misdetects it. The boot log says which was found
(`[board] touch: cst3530 at 0x1a, panel 240x320, firmware ...`). The
CST3530 path is written from LilyGO's `touch_hyn_core`, Hynitron's
`hyn_driver`, SensorLib and Meshtastic, which agree byte for byte, but has
not run on a CST3530 board here; `tools/tdeck_pro_touch_probe.py` phases
0, 1-3, 5, 8 and 9 are what to run on one.

## Still unresolved

The device at 0x28 is unidentified. It does not block the messenger.

## Firmware image (2026-10-09)

The Pro now has a frozen-firmware build of its own, the same shape as the v1's:

```bash
bash tools/build_firmware.sh --pro
esptool --chip esp32s3 --port /dev/cu.usbmodem* erase-flash
esptool --chip esp32s3 --port /dev/cu.usbmodem* write-flash 0x0 tools/firmware_build/pro/tdeck_pro_firmware.bin
```

`--pro` selects the `TDECK_PRO` variant in `tools/board_tdeck/` (quad PSRAM,
240 MHz, separate build dir), freezes `board_tdeck_pro.py`,
`tdeck_pro_config.py`, `board_geometry_tdeck_pro.py` as `board_geometry`, a
generated `board_id.py` (`tdeck_pro`) and the e-ink/TCA8418/BQ27220 drivers
from `lib/`, and ships a filesystem holding only `tdeck_pro_config.py`.
Outputs go to `tools/firmware_build/pro/` so v1 and Pro builds never clobber
each other. Verified on hardware: boots, announces over LoRa, 8 MB PSRAM free,
USB REPL reachable with the app running.

Two things found the hard way:

- **The UART REPL must be off on the Pro** (`MICROPY_HW_ENABLE_UART_REPL=0`
  in the variant). UART0 RX is GPIO44, the GPS module's TX. Its stream lands
  in the stdin ring buffer that USB CDC shares, fills it within a second of
  boot, and `tud_cdc_rx_cb` refuses to read *anything* -- Ctrl-C included --
  while the buffer is full. The app never reads stdin, so with the UART REPL
  on, USB is deaf for as long as the app runs, while the app itself is fine.
  Measured with the app halted: 400 stray stdin bytes in 3 s (0x1c/0xe0/0xfc,
  a 38400-baud stream read at 115200). With the REPL on, it drains and echoes
  them instead, which also corrupts anything pasted into raw REPL.
- **The loader re-enters itself.** esptool talks to the Pro's USB-serial-JTAG
  loader directly (unlike the v1), but on this board the chip reliably comes
  back up in download mode after esptool closes the port, and opening that
  0x1001 node from any program re-strapped it too (`rst:0x15 ... boot:0x0
  (DOWNLOAD)` on its console). Press reset once after flashing, and never
  open the serial node while the PID is 0x1001. The USB PID tells the state:
  0x1001 loader, 0x4001 MicroPython.

The file-by-file route below still works for development on stock MicroPython.

## Bring-up order (stock MicroPython route)

MicroPython **v1.24.1**, not the latest. `tools/build_firmware.sh` pins that
version to match `.mpy` version 6, which the precompiled native modules in
`lib/` are built for. Use the plain `ESP32_GENERIC_S3` build, **not**
`SPIRAM_OCT`: the Pro has quad PSRAM, unlike the v1.

```bash
# 1. Back up whatever is on the device first, it is not coming back otherwise
esptool --chip esp32s3 --port /dev/cu.usbmodem101 read-flash 0 0x1000000 backup.bin

# 2. Stock MicroPython
esptool --chip esp32s3 --port /dev/cu.usbmodem101 erase-flash
esptool --chip esp32s3 --port /dev/cu.usbmodem101 write-flash 0 \
    ESP32_GENERIC_S3-20241129-v1.24.1.bin

# 3. Bring-up. Do not go further until the radio checks pass.
mpremote connect /dev/cu.usbmodem101 run tools/tdeck_pro_bringup.py
```

Then headless uReticulum with the `tdeck_pro_sx1262` preset to prove the radio
on air, and only then the display and keyboard:

```bash
# 4. Radio only, no display or keyboard involved
./tools/deploy_pro_headless.sh /dev/cu.usbmodem101
mpremote connect /dev/cu.usbmodem101 run tools/tdeck_pro_headless.py

# 5. The whole messenger
./tools/deploy_pro.sh /dev/cu.usbmodem101
mpremote connect /dev/cu.usbmodem101 exec 'import tdeck_node'
```

**Set a US frequency.** `tdeck_config.py` ships 868800 kHz, which is EU. The Pro
config defaults to 914875 kHz, and it has to match every other node on your
mesh, so check it before expecting contact.
