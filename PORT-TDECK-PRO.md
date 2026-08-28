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

## Three things worth knowing

**The panel is slow, so drawing must never touch it.** A partial refresh is
about 700 ms and a full one about 1100 ms. `ui.py` makes 86 `text()` calls to
paint one screen. The shim therefore accumulates dirty rectangles and only
talks to the panel on `flush()`, which the app calls once per rendered screen.
It escalates to a full refresh when the dirty area passes half the panel or
after 10 consecutive partials, to clear ghosting.

**The colours are inverted on purpose.** The app is dark themed and paints a
near-black background with light text. Rendered literally that floods an e-ink
panel with ink: slow, heavy ghosting, and ugly. The shim thresholds on
luminance and inverts, so a dark background becomes paper and light text
becomes ink.

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
| 0x1A | CST328 touch |
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

## Still unresolved

The CST328 touch controller answers at 0x1A but has no driver, and the BQ27220
fuel gauge answers at 0x55 but battery reporting is not wired up. Neither
blocks the messenger. The device at 0x28 is unidentified.

## Bring-up order

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
