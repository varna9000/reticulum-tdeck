"""
T-Deck v1 LXMF Messaging Node
===============================
Standalone LoRa messaging device using LilyGO T-Deck v1.
ESP32-S3 + SX1262 LoRa + ST7789 display + keyboard + trackball.

Usage:
  1. Upload urns/ to /lib, t-deck files to root or /lib
  2. import tdeck_node
"""

import gc
gc.collect()

from machine import Pin, SPI, SoftI2C
import time

# --- Codec2 natmod: load FIRST, self-heal soft-reboot IRAM leak ---
# Natmod machine code lives in IRAM (heap_caps EXEC allocations) that a soft
# reboot never frees, so a relaunched session can't fit the ~40KB codec2 blob
# even with megabytes of GC heap free. Importing it before anything else gives
# it first claim on IRAM; if it still fails with MemoryError, hard-reset once
# to reclaim the leaked pool (RTC memory prevents a reset loop when IRAM is
# genuinely short). Doing this at the very top means the reset — which drops
# the USB connection (IDEs show a disconnect) — fires instantly at boot
# instead of mid-bring-up.
try:
    import codec2_fast_xtensawin as _codec2_mod
    from machine import RTC as _RTC
    _RTC().memory(b"")  # clear the retry guard
except MemoryError as _e:
    _codec2_mod = None
    from machine import RTC as _RTC, reset as _hard_reset
    _rtc = _RTC()
    if _rtc.memory() != b"c2rst":
        _rtc.memory(b"c2rst")
        print("Codec2 IRAM exhausted (soft-reboot natmod leak) — hard resetting")
        time.sleep_ms(500)
        _hard_reset()
    print("Codec2 load failed even after hard reset:", _e)
except Exception as _e:
    _codec2_mod = None
    print("Codec2 load failed:", _e)

from tdeck_config import (
    NODE_NAME, DEBUG, CONFIG, LORA_CONFIG, TCP_CONFIG,
    DISP_CS, DISP_DC, DISP_BL,
    LORA_CS, LORA_MISO,
    KBD_SCL, KBD_SDA, KBD_PWR, KBD_ADDR,
)

# --- Peripheral power ON ---
pwr = Pin(KBD_PWR, Pin.OUT)
pwr.on()
time.sleep_ms(100)

# --- Shared SPI bus (display + LoRa) ---
# Display: 40MHz (ST7789 max reliable on ESP32-S3)
# LoRa:    10MHz (SX1262 max=16MHz, 10MHz safe for T-Deck trace lengths)
_SPI_PINS = {"sck": Pin(40), "mosi": Pin(41), "miso": Pin(LORA_MISO)}
_SPI_DISP = 40_000_000
_SPI_LORA = 10_000_000
spi = SPI(1, baudrate=_SPI_LORA, **_SPI_PINS)

# Display CS — we manage this to keep it deasserted during LoRa ops.
# LoRa CS is managed internally by the lora-sx126x driver.
_disp_cs = Pin(DISP_CS, Pin.OUT, value=1)


def spi_acquire_display():
    """Acquire SPI bus for display: switch to 40MHz."""
    spi.init(baudrate=_SPI_DISP, **_SPI_PINS)


def spi_release_display():
    """Release SPI bus from display: deassert CS, restore LoRa speed."""
    _disp_cs.value(1)
    spi.init(baudrate=_SPI_LORA, **_SPI_PINS)


def spi_acquire_lora():
    """Acquire SPI bus for LoRa: deassert display CS, ensure 10MHz."""
    _disp_cs.value(1)
    spi.init(baudrate=_SPI_LORA, **_SPI_PINS)


def spi_release_lora():
    """Release SPI bus from LoRa."""
    pass  # LoRa driver manages its own CS


# --- Init display ---
try:
    import st7789                       # C driver (firmware-embedded)
    _st7789_c = True
except ImportError:
    import st7789py as st7789           # fallback: pure Python driver
    _st7789_c = False
import vga2_8x16_cp866 as font

dc = Pin(DISP_DC, Pin.OUT)
bl = Pin(DISP_BL, Pin.OUT)
bl.value(1)

spi_acquire_display()
tft = st7789.ST7789(spi, 240, 320, dc=dc, cs=_disp_cs, backlight=bl, rotation=1)
if _st7789_c:
    tft.init()                          # C driver requires explicit init
tft.fill(0x0821)  # BG_DARK

# Splash: render JPEG logo centered, then "Loading..." below
try:
    import tjpgd_fast_xtensawin as tjpgd
    with open("logo.jpg", "rb") as f:
        _jpeg_data = f.read()
    _w, _h, _rgb565 = tjpgd.decode(_jpeg_data, 320, 220)
    del _jpeg_data
    _logo_x = (320 - _w) // 2
    _logo_y = (220 - _h) // 2
    tft.blit_buffer(_rgb565, _logo_x, _logo_y, _w, _h)
    del _rgb565
    gc.collect()
    _txt = "Loading..."
    _tx = (320 - len(_txt) * 8) // 2
    tft.text(font, _txt, _tx, 224, 0x07E0, 0x0821)  # NEON_GREEN
except ImportError:
    # No JPEG decoder — fall back to simple text splash
    tft.text(font, "Starting...", 100, 112, 0x07FF, 0x0821)
except Exception as e:
    tft.text(font, "Starting...", 100, 112, 0x07FF, 0x0821)
    if DEBUG >= 1:
        print("Splash error:", e)

spi_release_display()
# Clean up splash temporaries
for _v in ('_jpeg_data', '_rgb565', '_w', '_h', '_logo_x', '_logo_y', '_txt', '_tx'):
    try:
        del globals()[_v]
    except KeyError:
        pass
del _v
gc.collect()

# --- Init keyboard ---
i2c = SoftI2C(scl=Pin(KBD_SCL), sda=Pin(KBD_SDA), freq=400000, timeout=50000)
time.sleep_ms(500)

# Drain startup garbage
for _ in range(20):
    try:
        i2c.readfrom(KBD_ADDR, 1)
    except OSError:
        pass
    time.sleep_ms(20)


def get_key():
    try:
        return i2c.readfrom(KBD_ADDR, 1)
    except OSError:
        return b'\x00'


gc.collect()

# --- Init sound ---
from sound import Sound
sound = Sound()
try:
    sound.init()
except Exception as e:
    if DEBUG >= 1:
        print("Sound init failed:", e)
    sound.enabled = False

# Init microphone (ES7210 on I2C)
try:
    from machine import SoftI2C
    _mic_i2c = SoftI2C(scl=Pin(8), sda=Pin(18), freq=100000)
    sound.init_mic(_mic_i2c)
    if DEBUG >= 1:
        print("Mic init OK")
except Exception as e:
    if DEBUG >= 1:
        print("Mic init failed:", e)

gc.collect()

# --- Init Reticulum ---
from urns import Reticulum
from urns.lxmf import LXMRouter, FIELD_IMAGE, FIELD_AUDIO
from urns.log import LOG_NONE, LOG_NOTICE, LOG_DEBUG

log_map = {0: LOG_NONE, 1: LOG_NONE, 2: LOG_DEBUG}
rns = Reticulum(loglevel=log_map.get(DEBUG, LOG_NOTICE))

# Inject shared SPI + bus arbitration into LoRa config
LORA_CONFIG["spi"] = spi
LORA_CONFIG["spi_acquire"] = spi_acquire_lora
LORA_CONFIG["spi_release"] = spi_release_lora

rns.config = CONFIG
gc.collect()

# --- Setup LXMF ---
router = LXMRouter(identity=rns.identity)
dest = router.register_delivery_identity(rns.identity, display_name=NODE_NAME)
gc.collect()

# --- Init GUI ---
from ui import UI

gui = UI(tft, font, get_key, node_name=NODE_NAME)
gui.set_backlight(bl)
gc.collect()

# --- Setup interfaces (LoRa comes online) ---
_announced_once = False  # set after the boot announce; gates recovery re-announce

def _lora_status(online):
    gui.lora_online = online
    gui._nav_mid_cache = ''
    gui.dirty = True
    if online and _announced_once:
        # Radio recovered after the boot announce went out into the void —
        # re-announce so the mesh learns we are here. (Only reached from
        # poll_loop recovery, so the event loop is running.)
        import uasyncio as asyncio
        async def _reannounce():
            try:
                router.announce()
                gui.announce_flash = time.ticks_ms()
                gui.dirty = True
            except Exception as e:
                if DEBUG >= 2:
                    print("Recovery announce error:", e)
        asyncio.create_task(_reannounce())

LORA_CONFIG["on_status"] = _lora_status
spi_acquire_lora()
rns.setup_interfaces()
spi_release_lora()
gc.collect()

# --- Battery sense (board-declared pin/divider; self-disabling) ---
import adc_reader
adc_reader.init_battery(CONFIG)

if DEBUG >= 1:
    print("LXMF address:", dest.hexhash)
    print("Free memory:", gc.mem_free(), "bytes")

# Codec2 natmod was pre-imported at the very top of this file (first claim
# on IRAM + soft-reboot self-heal); _codec2_mod holds it or None.
if DEBUG >= 1 and _codec2_mod:
    print("Codec2 module loaded, mem:", gc.mem_free())
gc.collect()


# --- Callbacks ---

# Maps LXMF delivery hash -> GUI peer key (populated by on_announce)
_lxmf_to_peer = {}


def _compute_lxmf_hash(dest_hash):
    """Compute the LXMF delivery hash for the identity behind dest_hash."""
    from urns.identity import Identity
    from urns.destination import Destination
    data = Identity.known_destinations.get(dest_hash)
    if data and data[2]:
        id_hash = Identity.truncated_hash(data[2])
        return Destination.hash(id_hash, "lxmf", "delivery")
    return None


def on_message(message):
    """Incoming LXMF message handler."""
    try:
        _on_message_inner(message)
    except Exception as e:
        import sys
        print("[RX] CRASH:", e)
        sys.print_exception(e)

def _on_message_inner(message):
    gui.transfer_progress = None
    gui._progress_dirty = False
    content = message.content_as_string() or "(binary)"
    source_hash = message.source_hash

    # Bump the sender's last-seen timestamp in the peer list
    _pk = _lxmf_to_peer.get(source_hash, source_hash)
    if _pk in gui.peers:
        gui.peers[_pk]["seen"] = time.time()

    # Extract image if present
    image_data = None
    fields = message.fields if hasattr(message, 'fields') else {}
    if FIELD_IMAGE in fields:
        img_field = fields[FIELD_IMAGE]
        if isinstance(img_field, (list, tuple)) and len(img_field) >= 2:
            if img_field[0] in ("jpg", "webp", "png") and isinstance(img_field[1], (bytes, bytearray)):
                image_data = bytes(img_field[1])
                if not content or content == "(binary)":
                    content = "[image]"

    # Extract audio if present
    audio_data = None
    if FIELD_AUDIO in fields:
        aud_field = fields[FIELD_AUDIO]
        if DEBUG >= 1:
            print("[Audio] field type:", type(aud_field).__name__,
                  "len:", len(aud_field) if hasattr(aud_field, '__len__') else "?")
            if isinstance(aud_field, (list, tuple)) and len(aud_field) >= 1:
                print("[Audio] mode:", aud_field[0], "type:", type(aud_field[0]).__name__)
                if len(aud_field) >= 2:
                    print("[Audio] data type:", type(aud_field[1]).__name__,
                          "len:", len(aud_field[1]) if hasattr(aud_field[1], '__len__') else "?")
        if isinstance(aud_field, (list, tuple)) and len(aud_field) >= 2:
            aud_mode = aud_field[0]
            if isinstance(aud_field[1], (bytes, bytearray)):
                if DEBUG >= 1:
                    d = aud_field[1]
                    print("[Audio]", len(d), "B mode", aud_mode)
                if aud_mode in (0x08, 0x09):  # AM_CODEC2_2400 or AM_CODEC2_3200
                    audio_data = bytes(aud_field[1])
                    # Map LXMF audio mode to codec2 internal mode
                    # CODEC2_MODE_3200=0, CODEC2_MODE_2400=1
                    audio_codec_mode = 0 if aud_mode == 0x09 else 1
                    if not content or content == "(binary)":
                        content = "[voice]"
                    else:
                        content = "[voice] " + content
                elif DEBUG >= 1:
                    print("[Audio] unsupported mode:", aud_mode)
    elif DEBUG >= 2:
        print("[Audio] no FIELD_AUDIO in fields, keys:", list(fields.keys()) if fields else "none")

    # Map LXMF source_hash to GUI peer key via precomputed mapping
    peer_key = _lxmf_to_peer.get(source_hash)

    if peer_key is None:
        # Fallback: compute LXMF hash for source and check mapping
        # (handles case where source IS the LXMF delivery hash)
        peer_key = source_hash

        # Try identity-based lookup: find any peer with same public key
        from urns.identity import Identity
        src_data = Identity.known_destinations.get(source_hash)
        if src_data and src_data[2]:
            src_pk = src_data[2]
            for pk in gui._peer_keys:
                if pk == source_hash:
                    continue
                pk_data = Identity.known_destinations.get(pk)
                if pk_data and pk_data[2] == src_pk:
                    peer_key = pk
                    # Cache for future lookups
                    _lxmf_to_peer[source_hash] = pk
                    break

    if DEBUG >= 1:
        print("[RX] from", source_hash.hex()[:8], "-> peer", peer_key.hex()[:8])

    # Ensure peer exists in GUI
    if peer_key not in gui.peers:
        gui.add_peer(peer_key, source_hash.hex()[:8])
    if DEBUG >= 1:
        print("[RX] peer ok")

    # Add to chat under the GUI peer key
    gui.add_chat_message(peer_key, False, content, image=image_data,
                         audio=audio_data, audio_mode=audio_codec_mode if audio_data else None)
    if DEBUG >= 1:
        print("[RX] chat ok")

    # Update RSSI/SNR from interface
    for iface in rns.interfaces:
        if iface.rssi is not None:
            gui.rssi = iface.rssi
            gui.snr = iface.snr
            break

    # Wake screen and play notification
    gui.wake_screen()
    if DEBUG >= 1:
        print("[RX] wake ok")
    sound.play_rx()
    if DEBUG >= 1:
        print("[RX] sound ok")
    gc.collect()
    if DEBUG >= 1:
        print("[RX] done, mem:", gc.mem_free())


def on_announce(destination_hash, display_name):
    """Peer announce handler. Builds LXMF hash mapping and deduplicates peers."""
    # Compute the LXMF delivery hash for this identity
    lxmf_hash = _compute_lxmf_hash(destination_hash)

    # Filter non-LXMF announces (e.g. nomadnetwork.node pages)
    if lxmf_hash is not None and lxmf_hash != destination_hash:
        if DEBUG >= 2:
            print("[Peer] Skip non-LXMF announce", destination_hash.hex()[:8])
        return

    # Deduplicate: if another peer key already maps to the same LXMF hash,
    # update that peer instead of adding a duplicate.
    if lxmf_hash:
        existing = _lxmf_to_peer.get(lxmf_hash)
        if existing and existing != destination_hash and existing in gui.peers:
            # Same node, different destination — update existing peer
            gui.peers[existing]["name"] = display_name or gui.peers[existing].get("name", "?")
            gui.peers[existing]["seen"] = time.time()
            _lxmf_to_peer[lxmf_hash] = existing
            gui.dirty = True
            if DEBUG >= 1:
                print("[Peer] (dedup)", display_name or "?",
                      "[" + destination_hash.hex()[:8] + " -> " + existing.hex()[:8] + "]")
            return
        # Store mapping: LXMF hash -> this GUI peer key
        _lxmf_to_peer[lxmf_hash] = destination_hash

    rssi = None
    for iface in rns.interfaces:
        if iface.rssi is not None:
            rssi = iface.rssi
            gui.rssi = iface.rssi
            gui.snr = iface.snr
            break

    # Route info from the transport path table (hops + next-hop relay)
    hops = None
    via = None
    try:
        from urns.transport import Transport
        from urns import const as _uc
        entry = Transport.path_table.get(destination_hash)
        if entry:
            hops = entry[_uc.IDX_PT_HOPS]
            if hops and hops > 1:
                via = entry[_uc.IDX_PT_NEXT_HOP].hex()[:4]
    except Exception:
        pass

    gui.add_peer(destination_hash, display_name, rssi=rssi, hops=hops, via=via)
    gui.wake_screen()
    if DEBUG >= 1:
        print("[Peer]", display_name or "?", "[" + destination_hash.hex()[:8] + "]",
              "hops:", hops)


def on_progress(resource):
    # Reference-RNS-shaped callback: one resource argument
    gui.transfer_progress = (resource.received_count, resource.total_parts)
    gui._progress_dirty = True

router.register_delivery_callback(on_message)
router.register_announce_callback(on_announce)
router.register_progress_callback(on_progress)


# --- GUI -> LXMF wiring ---

def gui_send(dest_hash, text, msg_idx=None):
    """Called by GUI when user sends a message."""
    import uasyncio as asyncio
    asyncio.create_task(_async_send(dest_hash, text, msg_idx))


async def _watch_queued(dest_hash, msg_idx):
    """A send was queued behind a path request (status 4). The router
    re-sends by itself once a path arrives — reflect that in the GUI."""
    import uasyncio as asyncio
    from urns.transport import Transport
    for _ in range(13):  # ~26s; router's path request times out at 15s
        await asyncio.sleep(2)
        if Transport.has_path(dest_hash):
            gui.update_message_status(dest_hash, msg_idx, 2)
            gui.dirty = True
            sound.play_tx()
            if DEBUG >= 1:
                print("[TX] Route found, message sent to", dest_hash.hex()[:8])
            return
    gui.update_message_status(dest_hash, msg_idx, 3)
    gui.dirty = True
    if DEBUG >= 1:
        print("[TX] No route found for", dest_hash.hex()[:8])


async def _track_delivery(msg, dest_hash, msg_idx, timeout=150):
    """Watch a DIRECT LXMessage (status 5) until the transfer concludes:
    DELIVERED -> checkmark, FAILED -> '!'. Covers link setup + resource
    transfer over multi-hop LoRa, hence the generous timeout."""
    import uasyncio as asyncio
    from urns.lxmf import LXMessage
    t = 0
    while t < timeout:
        await asyncio.sleep(2)
        t += 2
        state = getattr(msg, "state", None)
        if state == LXMessage.DELIVERED:
            gui.update_message_status(dest_hash, msg_idx, 2)
            gui.dirty = True
            if DEBUG >= 1:
                print("[TX] Delivered to", dest_hash.hex()[:8])
            return
        if state == LXMessage.FAILED:
            gui.update_message_status(dest_hash, msg_idx, 3)
            gui.dirty = True
            if DEBUG >= 1:
                print("[TX] Delivery failed to", dest_hash.hex()[:8])
            return
    # Still unresolved — leave the '>' marker as-is (honest: sent, unproven)


async def _async_send(dest_hash, text, msg_idx=None):
    """Send LXMF message as async task (crypto is slow)."""
    import uasyncio as asyncio
    await asyncio.sleep(0)

    # msg_idx is passed from GUI (message already added with status=1)
    if msg_idx is None:
        msg_idx = gui.add_chat_message(dest_hash, True, text, status=1)

    try:
        msg = router.send_message(dest_hash, text)
        if msg is True:
            # Queued behind a path request
            gui.update_message_status(dest_hash, msg_idx, 4)
            gui.dirty = True
            asyncio.create_task(_watch_queued(dest_hash, msg_idx))
            if DEBUG >= 1:
                print("[TX] Queued (path request) for", dest_hash.hex()[:8])
        elif msg:
            sound.play_tx()
            if DEBUG >= 1:
                print("[TX] Sent to", dest_hash.hex()[:8])

            from urns.lxmf import LXMessage
            if getattr(msg, "method", None) == LXMessage.DIRECT:
                # DIRECT transfers report real delivery — track it
                gui.update_message_status(dest_hash, msg_idx, 5)
                asyncio.create_task(_track_delivery(msg, dest_hash, msg_idx))
            else:
                # Opportunistic: no delivery proof surfaced — mark sent
                gui.update_message_status(dest_hash, msg_idx, 2)
            gui.dirty = True
        else:
            gui.update_message_status(dest_hash, msg_idx, 3)
            gui.add_chat_message(dest_hash, True, "(send failed: unknown peer)")
            gui.dirty = True
            if DEBUG >= 1:
                print("[TX] Failed: unknown identity for", dest_hash.hex()[:8])
    except Exception as e:
        gui.update_message_status(dest_hash, msg_idx, 3)
        gui.add_chat_message(dest_hash, True, "(send error)")
        gui.dirty = True
        if DEBUG >= 1:
            print("[TX] Error:", e)
    gc.collect()


def gui_announce():
    """Called by GUI when user presses 'a'."""
    sound.play_announce()
    try:
        router.announce()
        if DEBUG >= 1:
            print("[Announced as", NODE_NAME + "]")
    except Exception as e:
        if DEBUG >= 1:
            print("Announce error:", e)
    gc.collect()


# --- Settings persistence ---

_SETTINGS_PATH = "/rns/settings.json"


def _load_settings():
    try:
        import json
        with open(_SETTINGS_PATH, "r") as f:
            return json.load(f)
    except:
        return {}


def _save_settings(data):
    try:
        import json
        with open(_SETTINGS_PATH, "w") as f:
            json.dump(data, f)
    except Exception as e:
        if DEBUG >= 1:
            print("Settings save error:", e)


# --- WiFi / TCP ---

def _stop_wifi():
    """Deactivate WiFi radio."""
    import network
    wlan = network.WLAN(network.STA_IF)
    wlan.disconnect()
    wlan.active(False)
    if DEBUG >= 1:
        print("[WiFi] Disconnected")


def wifi_scan():
    import network
    # Pause LoRa — SX1262 SPI polling interferes with WiFi scanning
    for iface in rns.interfaces:
        if hasattr(iface, '_paused'):
            iface._paused = True
        iface.online = False
    spi_release_lora()
    time.sleep_ms(100)

    wlan = network.WLAN(network.STA_IF)
    try:
        wlan.disconnect()
    except:
        pass
    wlan.active(False)
    time.sleep_ms(200)
    wlan.active(True)
    time.sleep_ms(1000)
    try:
        results = wlan.scan()
        if not results:
            time.sleep_ms(500)
            results = wlan.scan()
    except Exception as e:
        if DEBUG >= 1:
            print("[WiFi] Scan error:", e)
        results = []

    # Resume LoRa
    for iface in rns.interfaces:
        if hasattr(iface, '_paused'):
            iface._paused = False
        iface.online = True

    if DEBUG >= 1:
        print("[WiFi] Scan found", len(results), "networks")
    return sorted([(r[0].decode(), r[3]) for r in results if r[0]],
                  key=lambda x: x[1], reverse=True)


def wifi_connect(ssid, password):
    import network
    wlan = network.WLAN(network.STA_IF)
    # Reset radio state — ESP32 throws "Wifi Internal State Error"
    # if connect() is called while radio is scanning or already connecting
    try:
        wlan.disconnect()
    except:
        pass
    wlan.active(False)
    time.sleep_ms(100)
    wlan.active(True)
    wlan.connect(ssid, password)
    for _ in range(40):  # ~8s timeout
        if wlan.isconnected():
            # Disable power saving — ESP32 drops broadcast packets in PS mode
            try:
                wlan.config(pm=0)  # WIFI_PS_NONE
            except Exception:
                pass
            ip = wlan.ifconfig()[0]
            settings = _load_settings()
            settings["wifi_ssid"] = ssid
            settings["wifi_pass"] = password
            _save_settings(settings)
            if DEBUG >= 1:
                print("[WiFi] Connected to", ssid, "IP:", ip)
            return ip
        time.sleep_ms(200)
    if DEBUG >= 1:
        print("[WiFi] Connection failed:", ssid)
    return False


def _stop_lora():
    """Disable LoRa interface (free SPI bus for display-only)."""
    from urns.transport import Transport
    for iface in list(rns.interfaces):
        if iface.__class__.__name__ == 'LoRaInterface':
            iface.online = False
            if hasattr(iface, 'close'):
                iface.close()
            rns.interfaces.remove(iface)
            Transport.deregister_interface(iface)
            if DEBUG >= 1:
                print("[LoRa] Interface stopped")
            return


def _start_lora():
    """Re-enable LoRa interface."""
    global _lora_task
    from urns.transport import Transport
    # Check if already running
    for iface in rns.interfaces:
        if iface.__class__.__name__ == 'LoRaInterface':
            return
    import uasyncio as asyncio
    # Cancel stale poll_loop task from previous interface
    if _lora_task is not None:
        try:
            _lora_task.cancel()
        except Exception:
            pass
        _lora_task = None
    spi_acquire_lora()
    try:
        rns.config["interfaces"] = [LORA_CONFIG]
        for iface_config in rns.config.get("interfaces", []):
            if iface_config.get("type") == "LoRaInterface":
                from urns.interfaces.lora import LoRaInterface
                # Manual path bypasses setup_interfaces(): resolve the
                # board pinout preset here too
                iface = LoRaInterface(rns._resolve_board(iface_config))
                rns.interfaces.append(iface)
                Transport.register_interface(iface)
                _lora_task = asyncio.create_task(iface.poll_loop())
                gui.lora_online = iface.online
                if DEBUG >= 1:
                    print("[LoRa] Interface restarted, online:", iface.online)
    finally:
        spi_release_lora()


def lora_reset():
    """Reset and reinitialize LoRa interface (called from UI)."""
    if DEBUG >= 1:
        print("[LoRa] Manual reset requested")
    _stop_lora()
    gui.lora_online = False
    gui.dirty = True
    _start_lora()
    gui.dirty = True


_tcp_iface = None  # track separately so rns.run() doesn't double-start it
_lora_task = None  # track poll_loop task to cancel on restart
_tcp_task = None


def tcp_toggle(enabled, host=None, port=None):
    global _tcp_iface, _tcp_task
    import uasyncio as asyncio
    from urns.transport import Transport
    if enabled:
        TCP_CONFIG["target_host"] = host
        TCP_CONFIG["target_port"] = port
        # Cancel stale TCP poll_loop task
        if _tcp_task is not None:
            try:
                _tcp_task.cancel()
            except Exception:
                pass
            _tcp_task = None
        from urns.interfaces.tcp import TCPClientInterface
        iface = TCPClientInterface(TCP_CONFIG)
        if iface.online:
            # Stop LoRa — only one interface at a time
            _stop_lora()
            gui.clear_peers()
            _lxmf_to_peer.clear()
            nomad_browser.clear_nodes()
            Transport.register_interface(iface)
            _tcp_task = asyncio.create_task(iface.poll_loop())
            _tcp_iface = iface
            settings = _load_settings()
            settings["tcp_enabled"] = True
            settings["tcp_host"] = host
            settings["tcp_port"] = port
            _save_settings(settings)
            if DEBUG >= 1:
                print("[TCP] Interface started ->", host + ":" + str(port))
            return True
        if DEBUG >= 1:
            print("[TCP] Interface failed to start")
        return False
    else:
        if _tcp_iface is not None:
            _tcp_iface.online = False
            _tcp_iface.enabled = False
            if hasattr(_tcp_iface, 'close'):
                _tcp_iface.close()
            Transport.deregister_interface(_tcp_iface)
            _tcp_iface = None
            settings = _load_settings()
            settings["tcp_enabled"] = False
            _save_settings(settings)
            gui.clear_peers()
            _lxmf_to_peer.clear()
            nomad_browser.clear_nodes()
            if DEBUG >= 1:
                print("[TCP] Interface stopped")
            # Disconnect WiFi
            _stop_wifi()
            gui._wifi_connected = False
            gui._wifi_ssid_current = ""
            gui._wifi_ip = ""
            # Restart LoRa
            _start_lora()
            return True
        return False


def _apply_display_name(name):
    """Propagate a new display name into the LXMF router and the announce
    app_data (announce() and path responses read the router/default data,
    not dest.display_name)."""
    from urns import umsgpack
    router.display_name = name
    dest.display_name = name
    dest.set_default_app_data(umsgpack.packb([name.encode("utf-8"), None, []]))


def set_node_name(name):
    """Called by GUI when user changes node name."""
    global NODE_NAME
    NODE_NAME = name
    gui.node_name = name
    _apply_display_name(name)
    settings = _load_settings()
    settings["node_name"] = name
    _save_settings(settings)
    if DEBUG >= 1:
        print("[Settings] Node name:", name)


def _kbd_backlight_cmd(on):
    """Drive the keyboard MCU's backlight PWM over I2C
    (LILYGO_KB_BRIGHTNESS_CMD 0x01 + value; 0 = off). Keyboards running
    pre-2023 stock firmware ignore the write — Alt+B still works there.
    Returns True when the I2C write succeeded."""
    try:
        i2c.writeto(KBD_ADDR, bytes([0x01, 160 if on else 0]))
        return True
    except OSError as e:
        if DEBUG >= 1:
            print("[Kbd] backlight cmd failed:", e)
        return False


def set_kbd_backlight(on):
    """Called by GUI settings toggle. Returns True if the command was sent."""
    if _kbd_backlight_cmd(on):
        settings = _load_settings()
        settings["kbd_backlight"] = bool(on)
        _save_settings(settings)
        return True
    return False


def get_radio_stats():
    """Rows for the GUI's Radio/Mesh stats page: [(label, value), ...]"""
    from urns.transport import Transport
    from urns.identity import Identity
    rows = []
    lora = None
    for iface in rns.interfaces:
        if iface.__class__.__name__ == 'LoRaInterface':
            lora = iface
            break
    if lora:
        rows.append(("LoRa", "online" if lora.online else "OFFLINE"))
        rows.append(("RF", str(lora._freq_khz) + "kHz SF" + str(lora._sf)
                     + " " + str(lora._tx_power) + "dBm"))
        if gui.rssi is not None:
            rows.append(("last RX", str(gui.rssi) + "dBm snr " + str(gui.snr)))
        rows.append(("TX", str(lora.tx) + " pkts " + str(lora.txb) + "B"))
        rows.append(("RX", str(getattr(lora, 'rx', 0)) + " pkts "
                     + str(getattr(lora, 'rxb', 0)) + "B"))
        rows.append(("CRC err", str(getattr(lora._modem, 'crc_errors', 0)
                                    if lora._modem else "?")))
        rows.append(("LBT", str(lora._lbt_waits) + " waits "
                     + str(lora._lbt_forced) + " forced"))
    else:
        rows.append(("LoRa", "not running"))
    rows.append(("paths", str(len(Transport.path_table))))
    rows.append(("identities", str(len(Identity.known_destinations))))
    rows.append(("free mem", str(gc.mem_free() // 1024) + "kB"))
    if time.localtime()[0] >= 2024:
        rows.append(("clock", "mesh-synced"))
    return rows


def on_ping(dest_hash):
    """Ping a peer's probe destination; RTT via delivery proof receipt."""
    import uasyncio as asyncio
    asyncio.create_task(_ping_task(dest_hash))


async def _ping_task(dest_hash):
    import uasyncio as asyncio
    import os as _os
    await asyncio.sleep(0)

    def _show(text):
        gui.ping_status = text
        gui._ping_status_ms = time.ticks_ms()
        gui._route_cache = ''
        gui.dirty = True

    try:
        from urns.identity import Identity
        from urns.destination import Destination
        from urns.packet import Packet

        identity = Identity.recall(dest_hash)
        if identity is None:
            _show("ping: no identity")
            return
        # Peers running uP-reticulum answer probes on urns.probe (PROVE_ALL)
        probe_dest = Destination(identity, Destination.OUT,
                                 Destination.SINGLE, "urns", "probe")
        pkt = Packet(probe_dest, _os.urandom(8), create_receipt=True)
        _t0 = time.ticks_ms()
        pkt.send()
        receipt = getattr(pkt, "receipt", None)
        if receipt is None:
            _show("ping: send failed")
            return
        receipt.timeout = 60  # multi-hop LoRa RTT

        def _delivered(r):
            rtt = time.ticks_diff(time.ticks_ms(), _t0) / 1000
            _show("ping: %.1fs" % rtt)
            if DEBUG >= 1:
                print("[Ping] reply from", dest_hash.hex()[:8], "in %.1fs" % rtt)

        def _timeout(r):
            _show("ping: no reply")
            if DEBUG >= 1:
                print("[Ping] no reply from", dest_hash.hex()[:8])

        receipt.delivery_callback = _delivered
        receipt.timeout_callback = _timeout
    except Exception as e:
        _show("ping: error")
        if DEBUG >= 1:
            print("[Ping] error:", e)


gui.on_send = gui_send
gui.on_announce = gui_announce
gui.on_wifi_scan = wifi_scan
gui.on_wifi_connect = wifi_connect
gui.on_tcp_toggle = tcp_toggle
gui.on_node_name = set_node_name
gui.on_lora_reset = lora_reset
def on_volume(v):
    """Settings volume slider: apply (tones regenerate, PCM attenuates)
    and persist."""
    sound.set_volume(v)
    settings = _load_settings()
    settings["volume"] = int(v)
    _save_settings(settings)

gui.on_volume = on_volume
gui.on_kbd_backlight = set_kbd_backlight
gui.on_ping = on_ping
gui.get_radio_stats = get_radio_stats
gui.my_address = dest.hexhash
def _on_audio_play(audio_data, audio_mode):
    import uasyncio as asyncio
    asyncio.create_task(_play_audio(audio_data, audio_mode))
gui.on_audio_play = _on_audio_play

# --- NomadNet page browser (NET tab) ---
import nomad_browser
nomad_browser.init(gui)
gui.on_browse = nomad_browser.browse
gui.on_browse_follow = nomad_browser.follow
gui.on_browse_back = nomad_browser.back
gui.on_browse_refresh = nomad_browser.refresh
gui.on_browser_exit = nomad_browser.browser_exit
gui.on_net_seed = nomad_browser.seed_nodes

_MAX_REC_SECS = 15  # max recording duration
_REC_CHUNK = 640    # samples per mic read (80ms) — larger chunks reduce GIL contention
_rec_buf = bytearray(_MAX_REC_SECS * 8000 * 2)  # permanent recording buffer — avoids fragmentation
_rec_pos = 0

def _on_record_start():
    global _rec_pos
    _rec_pos = 0
    sound.start_recording(_REC_CHUNK)
    import uasyncio as asyncio
    asyncio.create_task(_recording_loop())

def _on_record_stop(send=False):
    global _rec_pos
    sound.stop_recording()
    if send and _rec_pos > 0 and gui.selected_peer:
        import uasyncio as asyncio
        pcm = bytes(_rec_buf[:_rec_pos])
        _rec_pos = 0
        if DEBUG >= 1:
            print("[Audio] recorded", len(pcm), "B, mem:", gc.mem_free())
        asyncio.create_task(_encode_and_send_voice(gui.selected_peer, pcm))
    else:
        _rec_pos = 0

gui.on_record_start = _on_record_start
gui.on_record_stop = _on_record_stop
gui._tcp_default = TCP_CONFIG["target_host"] + ":" + str(TCP_CONFIG["target_port"])


# --- Async tasks ---

def _mic_thread():
    """Mic capture thread — runs on core 1, reads I2S continuously.
    I2S readinto() releases the GIL while waiting for DMA data,
    so the main thread (event loop, LoRa, keyboard) runs freely.
    Uses pre-allocated buffers — no allocations in the hot loop."""
    global _rec_pos
    chunk_bytes = _REC_CHUNK * 2
    try:
        # Flush stale DMA data — discard first few chunks
        flush = getattr(sound, '_flush_count', 0)
        for _ in range(flush):
            sound.read_mic_chunk(_REC_CHUNK)
        # Record
        mv = memoryview(_rec_buf)
        while sound.is_recording:
            if _rec_pos >= len(_rec_buf) - chunk_bytes:
                break
            out = sound.read_mic_chunk(_REC_CHUNK)
            if out:
                mv[_rec_pos:_rec_pos + chunk_bytes] = out
                _rec_pos += chunk_bytes
    except:
        pass

async def _recording_loop():
    """Start mic capture on a separate thread, poll keyboard on main thread."""
    import uasyncio as asyncio
    import _thread
    _thread.start_new_thread(_mic_thread, ())
    while sound.is_recording and _rec_buf:
        key = get_key()
        if key != b'\x00':
            gui.handle_key(key)
        await asyncio.sleep_ms(20)


async def _encode_and_send_voice(dest_hash, pcm_bytes):
    """Encode PCM to Codec2 3200 and send as LXMF voice message."""
    import uasyncio as asyncio
    dur = len(pcm_bytes) // (8000 * 2)
    gui._audio_status = "encode " + str(dur) + "s audio"
    gui._nav_mid_cache = ''
    gui.dirty = True
    await asyncio.sleep_ms(200)
    try:
        gc.collect()
        if DEBUG >= 1:
            print("[Audio] PCM", len(pcm_bytes), "B, mem:", gc.mem_free())
        c2_data = _codec2_mod.encode(pcm_bytes, 0)  # mode 0 = 3200
        del pcm_bytes
        gc.collect()
        if DEBUG >= 1:
            print("[Audio] encoded", len(c2_data), "B codec2 (mode 3200)")

        gui._audio_status = "sending"
        gui._nav_mid_cache = ''
        gui.dirty = True
        await asyncio.sleep_ms(100)  # let event loop process pending I/O

        from urns.lxmf import FIELD_AUDIO, LXMessage
        fields = {FIELD_AUDIO: [0x09, c2_data]}  # 0x09 = AM_CODEC2_3200
        msg_idx = gui.add_chat_message(dest_hash, True, "[voice]", status=1)
        msg = router.send_message(dest_hash, "[voice]", fields=fields,
                                  desired_method=LXMessage.DIRECT)
        if msg is True:
            # Queued behind a path request
            gui.update_message_status(dest_hash, msg_idx, 4)
            asyncio.create_task(_watch_queued(dest_hash, msg_idx))
        elif msg:
            # DIRECT: '>' until the resource transfer concludes
            gui.update_message_status(dest_hash, msg_idx, 5)
            asyncio.create_task(_track_delivery(msg, dest_hash, msg_idx))
            sound.play_tx()
        else:
            gui.update_message_status(dest_hash, msg_idx, 3)
    except Exception as e:
        if DEBUG >= 1:
            print("[Audio] encode/send error:", e)
    finally:
        gui._audio_status = None
        gui._nav_mid_cache = ''
        gui.dirty = True


_pcm_cache = {}  # id(codec2_bytes) -> pcm_bytes — decoded audio for instant replay


async def _play_audio(audio_data, codec_mode):
    """Decode (if needed) and play Codec2 audio. Shows status in navbar."""
    import uasyncio as asyncio
    cache_id = id(audio_data)

    # Decode on first click, cache for instant replay
    if cache_id not in _pcm_cache:
        bpf = 8 if codec_mode == 0 else 6
        n_frames = len(audio_data) // bpf
        dur = n_frames * 20 // 1000  # audio duration in seconds
        gui._audio_status = "decode " + str(dur) + "s audio"
        gui._nav_mid_cache = ''
        gui.dirty = True
        await asyncio.sleep_ms(200)  # let navbar draw before blocking
        try:
            gc.collect()
            if DEBUG >= 1:
                print("[Audio] decode start:", n_frames, "frames, mode:", codec_mode,
                      "mem:", gc.mem_free())
            # Batch decode — gain=2 for comfortable speaker volume
            pcm = _codec2_mod.decode(audio_data, codec_mode, 2)
            if DEBUG >= 1:
                print("[Audio] decoded", len(pcm), "B")
            gc.collect()
            if len(_pcm_cache) >= 1:
                _pcm_cache.pop(next(iter(_pcm_cache)))
            _pcm_cache[cache_id] = pcm
        except Exception as e:
            if DEBUG >= 1:
                import sys
                print("[Audio] decode error:", e)
                sys.print_exception(e)
            gui._audio_status = None
            gui._nav_mid_cache = ''
            gui.dirty = True
            return

    pcm = _pcm_cache.get(cache_id)
    if pcm is None:
        gui._audio_status = None
        gui._nav_mid_cache = ''
        gui.dirty = True
        return

    # Play on thread — I2S write() releases GIL while DMA drains,
    # so main event loop (LoRa, keyboard) keeps running without gaps
    gc.collect()
    await asyncio.sleep_ms(50)
    gui._audio_status = "playing"
    gui._nav_mid_cache = ''
    gui.dirty = True
    if DEBUG >= 1:
        print("[Audio] playing", len(pcm), "B PCM")

    import _thread
    _playing = True
    def _play_thread(pcm_data):
        nonlocal _playing
        try:
            chunk = 3200  # 200ms at 8kHz 16-bit mono — larger chunks reduce gaps
            mv = memoryview(pcm_data)  # zero-copy slicing
            i = 0
            while i < len(pcm_data):
                end = min(i + chunk, len(pcm_data))
                sound.play_pcm(mv[i:end])
                i = end
        except:
            pass
        _playing = False

    _thread.start_new_thread(_play_thread, (pcm,))
    while _playing:
        await asyncio.sleep_ms(50)

    if DEBUG >= 1:
        print("[Audio] playback complete")
    gui._audio_status = None
    gui._nav_mid_cache = ''
    gui.dirty = True


async def initial_announce():
    import uasyncio as asyncio
    global _announced_once
    await asyncio.sleep(0.5)
    try:
        router.announce()
        if DEBUG >= 1:
            print("Announced as:", NODE_NAME)
    except Exception as e:
        if DEBUG >= 2:
            print("Initial announce error:", e)
    _announced_once = True
    gc.collect()


async def reannounce_loop():
    import uasyncio as asyncio
    _interval = 90  # seconds between periodic re-announces
    while True:
        await asyncio.sleep(_interval)
        try:
            router.announce()
            gui.announce_flash = time.ticks_ms()
            gui.dirty = True
            if DEBUG >= 2:
                print("[Re-announced]")
        except Exception as e:
            if DEBUG >= 2:
                print("Re-announce error:", e)
        if DEBUG >= 2:
            print("[mem]", gc.mem_free())


# --- Main ---

def _auto_connect_wifi():
    """Restore WiFi and node name from saved settings on boot (synchronous)."""
    global NODE_NAME
    settings = _load_settings()
    saved_name = settings.get("node_name")
    if saved_name:
        NODE_NAME = saved_name
        gui.node_name = saved_name
        _apply_display_name(saved_name)
    if settings.get("kbd_backlight") and _kbd_backlight_cmd(True):
        gui._kbd_bl = True
    saved_vol = settings.get("volume")
    if saved_vol is not None:
        sound.set_volume(saved_vol)
        gui._volume = sound.volume
    ssid = settings.get("wifi_ssid")
    password = settings.get("wifi_pass")
    if ssid and password:
        if DEBUG >= 1:
            print("[Boot] Reconnecting WiFi:", ssid)
        ip = wifi_connect(ssid, password)
        if ip:
            gui._wifi_connected = True
            gui._wifi_ssid_current = ssid
            gui._wifi_ip = ip
        gc.collect()


async def _auto_start_tcp():
    """Start TCP interface if saved settings say so (needs event loop)."""
    import uasyncio as asyncio
    await asyncio.sleep(0)
    settings = _load_settings()
    # Always restore last used address for the TCP host input page
    host = settings.get("tcp_host")
    port = settings.get("tcp_port")
    if host and port:
        gui._tcp_target = host + ":" + str(port)
    # Auto-connect if it was enabled last session
    if gui._wifi_connected and settings.get("tcp_enabled") and host and port:
        if tcp_toggle(True, host, port):
            gui._tcp_enabled = True
    gc.collect()


def main():
    import uasyncio as asyncio

    gc.threshold(200000)  # Auto-GC after 200KB allocated (less frequent, fewer pauses)

    _auto_connect_wifi()

    if DEBUG >= 1:
        print("Starting event loop...")

    _original_run = rns.run

    async def run_all():
        asyncio.create_task(_auto_start_tcp())
        asyncio.create_task(initial_announce())
        # No auto re-announce — press 'a' to announce manually
        asyncio.create_task(gui.kbd_loop())
        asyncio.create_task(gui.gui_loop(spi_acquire_display, spi_release_display))
        asyncio.create_task(gui.battery_loop(spi_acquire_display, spi_release_display))
        asyncio.create_task(gui.ticker_loop())
        await _original_run()

    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        sound.deinit()
        rns.shutdown()
        if DEBUG >= 1:
            print("Shutdown complete")


main()
