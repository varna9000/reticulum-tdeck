# T-Deck LXMF Messenger

Standalone LoRa + TCP messaging device running on the LilyGO T-Deck v1.
Uses [micropython-reticulum](https://github.com/varna9000/micropython-reticulum/README.md) for Reticulum-compatible encrypted messaging over LoRa or WiFi TCP.


Supports both **opportunistic** (single-packet) and **link-based** (direct) messaging, including **JPEG image transfer** with on-device decoding and full-screen display, and **Codec2 voice messages** compatible with meshchat.


![splash](images/splash.jpeg "Splash Screen")
![node list](images/node_list.jpeg "Node List")

## Hardware

- **Board**: LilyGO T-Deck v1 (ESP32-S3)
- **Radio**: Semtech SX1262 LoRa transceiver (shared SPI bus with display)
- **Display**: ST7789 320x240 TFT (landscape)
- **Input**: QWERTY keyboard (I2C) + trackball with click button
- **Audio**: MAX98357A I2S amplifier + ES7210 ADC microphone — Codec2 voice messaging
- **Battery**: LiPo with ADC voltage monitoring

## Setup

### Option A: Flash Pre-built Firmware (Recommended)

The pre-built firmware includes everything — MicroPython, the ST7789 C display driver, all frozen Python modules, native C modules, LoRa drivers, and app files. One flash, zero extra steps.

**Requirements:** `esptool` (`pip install esptool` or `brew install esptool`)

**Flash:**
```bash
esptool --chip esp32s3 --port /dev/cu.usbmodem* erase-flash
esptool --chip esp32s3 --port /dev/cu.usbmodem* write-flash 0x0 tools/firmware_build/tdeck_firmware.bin
```

The T-Deck must be in **download mode** to flash: short GPIO0 to GND while pressing reset, then release. The device boots automatically after flashing.

**What's in the firmware:**

| Layer | Contents |
|---|---|
| **Frozen in ROM** | ui.py, sound.py, es7210.py, vga2_8x16 font, full urns/ stack (reticulum, LXMF, crypto, interfaces) |
| **C driver in ROM** | Russ Hughes st7789_mpy — DMA-accelerated ST7789 display driver |
| **On filesystem** | main.py (tdeck_node.py), tdeck_config.py, natmod .mpy files, lora-sx126x + lora-sync drivers, logo.jpg |

Frozen modules execute directly from flash ROM — zero RAM overhead, instant imports. The two user-editable files (`main.py` and `tdeck_config.py`) are on the filesystem so users can modify pin configs, radio parameters, or app behavior without rebuilding firmware.

### Option B: Manual Setup (Development)

For development or if you prefer stock MicroPython without a custom firmware build.

#### 1. Install mpremote

```
brew install mpremote        # macOS
pip install mpremote         # any platform
```

#### 2. Flash MicroPython

Flash MicroPython v1.24+ for ESP32-S3 (with Octal-SPIRAM) to the T-Deck.

Download from: https://micropython.org/download/ESP32_GENERIC_S3/

#### 3. Install LoRa driver

```
mpremote mip install lora-sx126x
mpremote mip install lora-sync
```

#### 4. Upload files

The Reticulum stack is not vendored in this repo — it lives in the `vendor/uP-reticulum` git submodule. The shipped `.mpy` files are cross-compiled for the `xtensawin` architecture using `mpy-cross -march=xtensawin`; urns uploads as `.py` (optionally compile it yourself for faster imports).

```bash
# Upload native C modules (crypto + JPEG + Codec2)
mpremote cp lib/ed25519_fast_xtensawin.mpy lib/bz2_fast_xtensawin.mpy lib/tjpgd_fast_xtensawin.mpy lib/codec2_fast_xtensawin.mpy :/lib/

# Upload uP-reticulum library from the submodule
# (clone with --recursive, or run: git submodule update --init)
mpremote cp -r vendor/uP-reticulum/firmware/urns/ :/lib/urns/
mpremote cp vendor/uP-reticulum/firmware/lora_boards.py :
mpremote cp vendor/uP-reticulum/firmware/peripherals/adc_reader.py :

# Upload T-Deck app files
mpremote cp tdeck_node.py ui.py sound.py es7210.py tdeck_config.py :
mpremote cp lib/st7789py.mpy lib/vga2_8x16.mpy :/lib/

# Upload assets
mpremote cp logo.jpg :
```

> **Note**: Upload `.mpy` files instead of `.py` for faster boot, lower RAM usage, and ~65% less flash storage. The entry point `tdeck_node.py` can also be `.mpy`.

### Configure

Edit `tdeck_config.py` (on-device or before flashing):

- `NODE_NAME` — default display name broadcast in announces (default: `"T-Deck"`). Can be changed at runtime from Settings.
- `DEBUG` — `0` = silent, `1` = basic, `2` = verbose
- `LORA_CONFIG` — mesh radio parameters (frequency, SF, BW, TX power, syncword) and listen-before-talk settings. Board wiring (pins, TCXO, DC-DC, battery sense) comes from the `tdeck_v1_sx1262` preset in `lora_boards.py`.
- `TCP_CONFIG` — default TCP server address and port for WiFi mode
- `CONFIG` — Reticulum options: transport mode, network time sync (adopts mesh time once per boot, then re-announces), rnprobe responder

Default radio settings: **868.8 MHz, SF8, BW125, CR5, 22 dBm, syncword 0x1424**.
These are compatible with RNode firmware and reference Reticulum.

Default TCP settings: connects to `TCP_CONFIG["target_host"]` on port `4242`. The remote machine needs a Reticulum `TCPServerInterface` listening on that port.

### Run

The pre-built firmware starts automatically — `main.py` runs `tdeck_node` on boot.

For manual setup:
```
mpremote cp tdeck_node.py :/main.py
```

## Usage

### Node List Screen

The device starts on the node list screen showing discovered peers. Peers with unread messages are marked with `*` and bubbled to the top of the list.

| Action | Input |
|---|---|
| Select peer | Trackball up/down |
| Open chat | Trackball click |
| Send announce | Press `a` |
| Open settings | Press `s` |

### Chat Screen

| Action | Input |
|---|---|
| Type message | Keyboard |
| Send message | Enter |
| Navigate messages | Trackball up/down (moves highlight cursor) |
| View image | Click trackball on a highlighted `[image]` line |
| Record voice | Press `r` or `0` (empty input) |
| Back to node list | Backspace (empty input) or Escape |

Message delivery status is shown after each sent message:
- `..` — pending (waiting for delivery confirmation)
- checkmark — delivered
- `!` — failed (no acknowledgement received)

### Image Viewing

When a peer sends an image (JPEG via LXMF `FIELD_IMAGE`), it appears as `[image]` in magenta in the chat. Use the trackball to highlight the image line — the hint bar changes to `[click=view]`. Click to open a full-screen view scaled to 320x240. Press any key to return to chat.

Images are decoded on-device using the native `tjpgd_fast` TJpgDec module with nearest-neighbor scaling. Up to 3 recent images are cached in RAM; older images appear dimmed with a strikethrough to indicate they've been evicted.

### Voice Messages

Press `r` (or `0`) with an empty input field to start recording a voice message. Speak into the mic, then press any key to stop and send, or Escape/Backspace to cancel. Voice messages are encoded with **Codec2 3200 bps** and sent via LXMF `FIELD_AUDIO` using link-based (DIRECT) delivery. They are compatible with [meshchat](https://github.com/liamcottle/reticulum-meshchat) and other LXMF clients that support Codec2.

Received voice messages appear as `[voice]` in the chat. Highlight with the trackball and click to play.

#### ES7210 Microphone — Technical Details

Getting usable audio from the T-Deck's ES7210 ADC for Codec2 encoding required solving several hardware and software challenges:

**MicroPython I2S limitations:**
- MicroPython's `machine.I2S` has no MCLK output support — the ESP-IDF I2S peripheral can generate a phase-locked MCLK, but MicroPython hardcodes `mclk = I2S_GPIO_UNUSED`. MCLK is generated via PWM at 4.096 MHz on GPIO 48 as a workaround. This is asynchronous to BCLK/LRCK, causing periodic frame misalignment.
- MicroPython forces stereo mode for I2S RX internally, even when `format=I2S.MONO` is specified. In MONO mode it extracts the **right** channel, but the T-Deck mic is on ADC1 (left channel). The driver uses `format=I2S.STEREO` and extracts the left channel manually.
- The async MCLK causes a `[L, R, 0, 0]` repeating pattern — only every other stereo pair contains valid data. The driver reads at 16 kHz stereo and extracts valid left-channel samples with stride-8 byte offset, yielding 8 kHz mono for Codec2.

**ES7210 register configuration:**
- Register `0x08` must be `0x20` (slave mode). The default `0x00` is master mode — both ESP32 and ES7210 driving BCLK/LRCK causes bus contention and 88% zero samples.
- LRCK divider = 256 (registers `0x04`/`0x05`) with 4.096 MHz MCLK gives 16 kHz sample rate.
- PGA gain at maximum (37.5 dB, register value `0x1E`) for the MEMS microphone.
- DLL power down (`0x06 = 0x04`) works better with async PWM MCLK than DLL enabled.

**Codec2 encoding on ESP32 single-precision float:**
- The ESP32-S3 has no double-precision FPU — all `double` operations are software-emulated (~5x slower).
- Codec2's LPC-to-LSP conversion (`lpc_to_lsp()`) uses Chebyshev polynomial root-finding that fails systematically on single-precision float. The upstream codec2 was designed for x86 `double` precision. Meshtastic's ESP32 port only decodes — encoding was never shipped on ESP32.
- **Fix**: The entire autocorrelation → Levinson-Durbin → bandwidth expansion → LSP root-finding pipeline runs in `double` precision. The Chebyshev polynomial evaluation uses the Clenshaw algorithm in double. The P/Q polynomial arrays and root search variables are all double. An adaptive step size (from Speex) narrows the search grid near interval edges where roots cluster.
- Without this fix: 0/10 LSP roots found → codec2 output is silence or screeching. With the fix: 10/10 roots, 0% failure rate.
- Post-filter is disabled (`lpc_pf = 0`) as it amplifies numerical errors from the remaining single-precision arithmetic in the synthesis path.

**Recording architecture:**
- Mic capture runs on a **separate thread** (`_thread`) on ESP32-S3's second core. The I2S `readinto()` call releases the GIL while waiting for DMA data, so the main async event loop (LoRa polling, keyboard, UI) runs freely on the first core.
- The 240 KB recording buffer (15 seconds at 8 kHz 16-bit) is **pre-allocated at boot** and never freed, preventing heap fragmentation that would block the 39 KB codec2 native module from loading.
- IIR DC offset removal is applied per-sample during capture: `dc = (dc * 31 + sample) >> 5`.

### Settings

Press `s` from the node list to open settings. Navigate with trackball, select with trackball click, go back with backspace.

**WiFi** — Scan for networks, select one, enter password. After connecting, the TCP host entry page opens automatically.

**TCP** — Connect to a remote Reticulum node over WiFi. When TCP is OFF, click to enter a server address (pre-filled with the last used address or the default from `tdeck_config.py`). When TCP is ON, click to disconnect — this also disconnects WiFi and restarts LoRa. Only one interface (LoRa or TCP) is active at a time.

**Name** — Change the node's display name. Saved to flash and persisted across reboots.

All settings (WiFi credentials, TCP host/port, node name, TCP enabled state) are saved to `/rns/settings.json` and restored on boot. If WiFi and TCP were enabled when the device was last used, they reconnect automatically on startup.

### Screen Power-Off

The screen turns off automatically after 10 seconds of inactivity to save battery. Any keypress, trackball event, incoming message, or peer announce wakes the screen. The first input after wake is consumed (not processed) to prevent accidental actions. The MCU stays awake to receive LoRa packets — only the backlight is toggled. All SPI display writes are skipped while the screen is off, freeing the bus for LoRa.

### Status Bar

Top bar shows: battery voltage, active interface (`[LoRa]` or `[TCP]`), RSSI of last received packet, node name, and `[A]` flash on announce.

## Networking

### LoRa (default)

LoRa is the default interface, active on boot. All Reticulum peers within radio range are discovered automatically via announces.

### TCP over WiFi

The T-Deck can connect to a remote Reticulum node over WiFi using a TCP client interface with HDLC framing. This is useful for bridging to the wider Reticulum network.

The remote node needs a `TCPServerInterface` in its Reticulum config:

```
[[TCP Server Interface]]
  type = TCPServerInterface
  interface_enabled = True
  listen_ip = 0.0.0.0
  listen_port = 4242
```

When TCP is activated, LoRa is stopped (only one interface at a time). When TCP is deactivated, WiFi is disconnected and LoRa restarts.

**Why TCP instead of UDP?** The ESP32 cannot reliably receive UDP broadcast packets, even with power saving disabled. TCP provides reliable bidirectional communication.

## Pin Map

| Function | Pin(s) |
|---|---|
| SPI SCK/MOSI | 40, 41 |
| Display CS/DC/BL | 12, 11, 42 |
| LoRa CS/RST/BUSY/DIO1/MISO | 9, 17, 13, 45, 38 |
| Keyboard SCL/SDA/PWR | 8, 18, 10 |
| Trackball U/D/L/R/Click | 3, 15, 1, 2, 0 |
| Speaker BCK/WS/DOUT | 7, 5, 6 |
| Mic SCK/LRCK/DIN/MCLK | 47, 21, 14, 48 |
| Battery ADC | 4 |

## Architecture

```
main.py             App entry (tdeck_node.py renamed) — on filesystem, user-editable
tdeck_config.py     Pin definitions, radio parameters — on filesystem, user-editable
ui.py               Async GUI: node list, chat, settings, image viewer  [frozen in ROM]
sound.py            I2S audio: notification tones, mic capture, playback [frozen in ROM]
es7210.py           ES7210 ADC microphone driver (I2C register config)  [frozen in ROM]
lib/vga2_8x16.py    8x16 VGA font                                      [frozen in ROM]
vendor/uP-reticulum µReticulum stack submodule (urns/, boards, tests)   [frozen in ROM]
st7789              Russ Hughes C display driver (DMA-accelerated)      [compiled in firmware]
```

### Native C Modules (.mpy) — on filesystem

| Module | Size | Purpose |
|---|---|---|
| `ed25519_fast_xtensawin.mpy` | 50 KB | Ed25519 signing/verification (~160x faster than pure Python) |
| `bz2_fast_xtensawin.mpy` | 5 KB | BZ2 compression/decompression for message payloads |
| `tjpgd_fast_xtensawin.mpy` | 5 KB | TJpgDec JPEG decoder with nearest-neighbor scaling |
| `codec2_fast_xtensawin.mpy` | 46 KB | Codec2 3200/2400 bps voice codec (full double-precision LSP pipeline) |
| `webp_fast_xtensawin.mpy` | — | WebP image decoder |

These are compiled as MicroPython native modules using `mpy-cross` and the ESP-IDF Xtensa toolchain. Source and Makefiles are in `tools/natmod/`.

### SPI Bus Sharing

Display and LoRa share SPI1 (SCK=40, MOSI=41). Bus arbitration is CS-based only — display CS is deasserted during LoRa operations and vice versa. No SPI reinit at runtime.

### ST7789 C Display Driver

The custom firmware embeds the [Russ Hughes st7789_mpy](https://github.com/russhughes/st7789_mpy) C driver as a `USER_C_MODULE`. This provides DMA-accelerated SPI writes — `fill_rect`, `text`, and `blit_buffer` are 10-50x faster than the pure Python `st7789py` driver.

**Key integration details:**

- **Explicit `init()` required.** Unlike the pure Python driver which initializes in `__init__`, the C driver's constructor does not send the ST7789 init sequence. `tft.init()` must be called after constructing the `ST7789` object. Without this, the display stays blank (backlight on, no pixel data).
- **Fallback mechanism.** `tdeck_node.py` tries `import st7789` (C driver) first, falling back to `import st7789py as st7789` (pure Python). A `_st7789_c` flag tracks which driver loaded so `init()` is only called for the C driver.
- **API compatibility.** The C driver's `text()`, `fill()`, `fill_rect()`, and `blit_buffer()` have identical signatures to the pure Python driver. The `vga2_8x16` bitmap font works with both.
- **GIL behavior.** The C driver holds the Python GIL during SPI transfers (all SPI operations happen in C code). The pure Python driver released the GIL on each `spi.write()` call. This affects concurrent I2S mic recording — see below.

### I2S Mic Buffer and GIL Contention

The C display driver creates a GIL contention issue with mic recording. During display updates, the C driver holds the GIL for 10-30ms while pushing pixel data over SPI. The mic capture thread (running on core 1) cannot acquire the GIL during this time, so it cannot read from the I2S DMA buffer. If the DMA buffer fills up and wraps, captured audio has gaps and pitch artifacts.

**Fix:** The I2S mic DMA buffer (`ibuf`) is set to 65536 bytes (~1 second at 16kHz stereo 16-bit) instead of the original 16384 bytes (256ms). This provides sufficient headroom for the mic DMA to buffer audio during any C driver SPI operation without overflow. The pure Python driver never needed this because each `spi.write()` released the GIL, giving the mic thread regular windows to read.

### Display Optimization

The GUI uses diff-based drawing: a 15-slot cache tracks what's currently on screen. Only changed rows trigger SPI writes, reducing traffic by ~80% on typical redraws. The navbar, footer hints, separator line, and scroll indicator are all cached — scrolling the trackball redraws only the 2 affected rows (old + new cursor position). The trackball uses edge detection (HIGH-to-LOW transitions) to prevent noisy pins from flooding scroll events.

### Interface Switching

Only one network interface is active at a time. Switching from LoRa to TCP stops the LoRa radio and deregisters it from Transport. Switching back closes the TCP socket, disconnects WiFi, and re-initializes LoRa. The peer list and chat history are cleared on each switch since peers from one interface won't be reachable on the other.

### Settings Persistence

Settings are stored as JSON in `/rns/settings.json` on the device flash. Saved fields: `wifi_ssid`, `wifi_pass`, `tcp_enabled`, `tcp_host`, `tcp_port`, `node_name`. On boot, WiFi and TCP are automatically restored if they were active in the previous session.

### SX1262 Notes

- **DC-DC regulator mode** is required for TX (`use_dcdc: True`). The driver defaults to LDO which produces no RF output on the T-Deck.
- **TCXO supply** must be set to 3.3V (`dio3_tcxo_millivolts: 3300`). Without it, modem init fails.

## Firmware Integration Fixes

Issues discovered and fixed when integrating the st7789 C driver into the custom firmware build:

### 1. ST7789 C Driver: Missing `init()` Call

**Symptom:** Display blank after boot — backlight on, no pixels.

**Root cause:** The Russ Hughes C driver's `ST7789()` constructor does not auto-call `init()`. The pure Python `st7789py` driver sends the full ST7789 initialization sequence (SLPOUT, COLMOD, porch control, gamma, INVON, DISPON) inside `__init__`. The C driver defers this to an explicit `init()` method.

**Fix:** Added `tft.init()` after `ST7789()` construction in `tdeck_node.py`, guarded by a `_st7789_c` flag so it's only called for the C driver.

### 2. Frozen String/Bytes Concatenation in Link Handler

**Symptom:** Incoming link requests fail with `TypeError: unsupported types for __add__: 'str', 'bytes'`. Outgoing links and opportunistic messages work fine.

**Root cause:** A multi-line log statement in `link.py` line 91-94 used `+` concatenation to build a debug string. When compiled as frozen bytecode, MicroPython's optimizer evaluates multi-line `+` expressions differently, and one intermediate result triggered a `str + bytes` type error that doesn't occur when the same code runs from `.mpy` bytecode files.

**Fix:** Replaced the `+` concatenation chain with `%` format string interpolation, which handles all types safely:
```python
# Before (fails when frozen):
log("Link request on " + destination.hexhash[:8] + " link_id=" + self.link_id.hex()[:8] + ...)

# After:
_dbg = "Link request on %s link_id=%s mtu=%d ..." % (destination.hexhash[:8], self.link_id.hex()[:8], ...)
log(_dbg, LOG_VERBOSE)
```

### 3. I2S Mic DMA Buffer Overflow During Recording

**Symptom:** Voice recordings have gaps and high-pitched artifacts — patches of voice with stretches of silence.

**Root cause:** The C display driver holds the Python GIL during all SPI operations (10-30ms per draw call). The mic capture thread on core 1 cannot acquire the GIL to read from the I2S DMA buffer during this time. With the original 16 KB I2S buffer (256ms at 16kHz stereo), the buffer overflows during display updates, causing the DMA to wrap and corrupt captured audio.

The pure Python driver never had this issue because each `spi.write()` call released the GIL, giving the mic thread regular windows to read.

**Fix:** Increased the I2S mic DMA buffer from 16 KB to 65 KB (~1 second of buffering). This provides sufficient headroom for the DMA to accumulate audio during any C driver GIL hold without overflow.

```python
# sound.py — I2S mic init
ibuf=65536,  # 1s buffer — C display driver holds GIL during SPI
```

### 4. C Driver UTF-8 Font Rendering for Chars > 0x7F

**Symptom:** The `√` delivery checkmark (`\xfb` in the vga2_8x16 bitmap font) renders as two wrong characters.

**Root cause:** The C driver's `text()` method receives Python strings via `mp_obj_str_get_str()`, which returns UTF-8 encoded C strings. Characters above 0x7F (like `\xfb` = U+00FB) become multi-byte UTF-8 sequences (`0xC3 0xBB`). The rendering loop then treats each UTF-8 byte as a separate character index into the font bitmap, producing two wrong glyphs.

The pure Python driver doesn't have this issue because Python's `ord()` correctly returns the single integer 0xFB for indexing.

**Fix:** Added a `_tb()` helper in `ui.py` that converts strings to raw bytes via `ord()` before passing to `tft.text()`. The C driver has a separate code path for `bytes` arguments that reads raw byte values without UTF-8 interpretation. MicroPython lacks a `latin-1` codec (`str.encode('latin-1')` silently falls back to UTF-8), so the conversion must be done manually.

```python
@staticmethod
def _tb(text):
    """Convert text to bytes for C display driver."""
    return bytes([ord(c) for c in text]) if isinstance(text, str) else text
```

Applied to all `tft.text()` calls that may contain chars > 0x7F (the full chat row draw and the colored status suffix overlay).

## Building the Firmware

The pre-built `tdeck_firmware.bin` bundles everything into a single flashable image. To rebuild it from source:

### Prerequisites

```bash
brew install cmake ninja dfu-util    # macOS
pip install esptool
```

### Build

```bash
cd tools && bash build_firmware.sh
```

First build takes ~20 minutes (clones ESP-IDF v5.2.3 + MicroPython v1.24.1 + st7789_mpy, installs Xtensa toolchain). Subsequent builds take ~30 seconds.

The script:
1. Clones and installs ESP-IDF v5.2.3 with ESP32-S3 toolchain
2. Clones MicroPython v1.24.1 and builds `mpy-cross` (with `-Wno-error=gnu-folding-constant` for Apple Clang compatibility)
3. Clones [Russ Hughes st7789_mpy](https://github.com/russhughes/st7789_mpy) C display driver
4. Fetches all required submodules (berkeley-db, micropython-lib, tinyusb, micro-ecc, bt/lib_esp32c3_family)
5. Builds MicroPython for `ESP32_GENERIC_S3` with `SPIRAM_OCT` variant and st7789 as `USER_C_MODULE`
6. Freezes Python modules via `tdeck_manifest.py`
7. Creates a FAT filesystem image (`vfs.bin`) with natmod files, LoRa drivers, app files, and logo
8. Merges bootloader + partition table + app + VFS into a single `tdeck_firmware.bin` using `esptool merge-bin`

### Firmware Image Layout

| Offset | Contents | Size |
|---|---|---|
| `0x000000` | Bootloader | 19 KB |
| `0x008000` | Partition table | 3 KB |
| `0x010000` | MicroPython app (frozen modules + st7789 C driver) | ~1.7 MB |
| `0x200000` | FAT VFS filesystem (natmods, lora, main.py, config, logo) | 6 MB |

Total image: 8 MB (matches T-Deck flash size).

### Frozen Module Manifest (`tdeck_manifest.py`)

Modules frozen into the firmware ROM (not editable without rebuild):

| Module | Purpose |
|---|---|
| `ui.py` | GUI state machine, cached drawing, image viewer |
| `sound.py` | I2S audio, mic capture, PCM playback |
| `es7210.py` | ES7210 ADC microphone I2C driver |
| `lora_boards.py` | LoRa board pinout presets (incl. `tdeck_v1_sx1262`) |
| `adc_reader.py` | Board-declared battery/ADC voltage reader |
| `vga2_8x16.py` | 8x16 bitmap font |
| `urns/` | Full µReticulum stack — transport, LXMF, crypto, all interfaces |

Intentionally **not frozen** (on filesystem, user-editable):

| File | Purpose |
|---|---|
| `main.py` | App entry point (tdeck_node.py renamed) — hardware init, callbacks, event loop |
| `tdeck_config.py` | Pin definitions, radio parameters, TCP config, node name |

### VFS Filesystem Contents

Files on the FAT filesystem partition (editable via mpremote):

| File | Purpose |
|---|---|
| `main.py` | App entry — `tdeck_node.py` renamed for auto-start |
| `tdeck_config.py` | User-editable hardware and radio config |
| `lib/ed25519_fast_xtensawin.mpy` | Native Ed25519 crypto (natmod) |
| `lib/bz2_fast_xtensawin.mpy` | Native BZ2 compression (natmod) |
| `lib/codec2_fast_xtensawin.mpy` | Native Codec2 voice codec (natmod) |
| `lib/tjpgd_fast_xtensawin.mpy` | Native JPEG decoder (natmod) |
| `lib/webp_fast_xtensawin.mpy` | Native WebP decoder (natmod) |
| `lib/lora/__init__.mpy` | lora-sx126x driver |
| `lib/lora/modem.mpy` | lora-sx126x modem |
| `lib/lora/sx126x.mpy` | SX1262 radio driver |
| `lib/lora/sync_modem.mpy` | lora-sync synchronous modem |
| `logo.jpg` | Splash screen image |

Natmod `.mpy` files contain native machine code and cannot be frozen — they must stay on the filesystem. The LoRa driver packages (`lora-sx126x`, `lora-sync`) are third-party and installed via `mip`; copies are cached in `tools/firmware_build/vfs_staging/` for offline builds.

### Build Options

```bash
bash build_firmware.sh              # Full build with frozen modules + VFS
bash build_firmware.sh --no-freeze  # st7789 C driver only, no frozen modules
```

### Why Not Freeze Natmods?

MicroPython's freeze system compiles `.py` files to bytecode and embeds them in ROM. Natmod `.mpy` files contain **native Xtensa machine code** (compiled C), not bytecode — they are loaded by the dynamic linker at runtime and cannot be frozen. They must remain on the FAT filesystem.

## Files

### App Files

| File | Location | Description |
|---|---|---|
| `tdeck_node.py` | Filesystem (as `main.py`) | Main app — hardware init, Reticulum/LXMF setup, async event loop |
| `tdeck_config.py` | Filesystem | All pin definitions, radio config, and TCP config |
| `ui.py` | Frozen in ROM | GUI state machine with cached drawing, image viewer, and async input |
| `sound.py` | Frozen in ROM | I2S audio: tones, mic capture (ES7210 stride extraction), PCM playback |
| `es7210.py` | Frozen in ROM | ES7210 ADC mic driver — I2C register config, gain, slave mode |
| `lib/st7789py.py` | Filesystem (`/lib`, as `.mpy`) | Pure Python ST7789 driver (fallback if C driver unavailable) |
| `lib/vga2_8x16.py` | Frozen in ROM | Bitmap font (8x16 pixels per character, 40 columns) |
| `vendor/uP-reticulum/` | Git submodule | The full µReticulum stack (urns/), board presets, adc_reader, and host test suite — [uP-reticulum](https://github.com/varna9000/micropython-reticulum). All former T-Deck patches are upstreamed; see `TDECK-PATCHES.md` for the update procedure |
| `lora_boards.py` (submodule) | Frozen in ROM | Board pinout presets (incl. `tdeck_v1_sx1262`) |
| `adc_reader.py` (submodule) | Frozen in ROM | Battery voltage via board-declared ADC pin + divider |

### Native Modules (on filesystem)

| File | Description |
|---|---|
| `lib/ed25519_fast_xtensawin.mpy` | Native Ed25519 crypto module |
| `lib/bz2_fast_xtensawin.mpy` | Native BZ2 compression module |
| `lib/tjpgd_fast_xtensawin.mpy` | Native JPEG decoder (TJpgDec) |
| `lib/codec2_fast_xtensawin.mpy` | Native Codec2 voice codec |
| `lib/webp_fast_xtensawin.mpy` | Native WebP image decoder |
| `lib/lora/` | SX1262 LoRa driver (lora-sx126x + lora-sync) |

### Build Tools

| File | Description |
|---|---|
| `tools/build_firmware.sh` | Builds custom MicroPython firmware with st7789 C driver + frozen modules |
| `tools/flash_tdeck.sh` | Flashes firmware + uploads natmod files via mpremote |
| `tools/tdeck_manifest.py` | MicroPython frozen module manifest |
| `tools/natmod/tjpgd_fast/` | TJpgDec native module source + Makefile |
| `tools/natmod/codec2_fast/` | Codec2 native module source + Makefile |
| `tools/natmod/webp_fast/` | WebP native module source + Makefile |
