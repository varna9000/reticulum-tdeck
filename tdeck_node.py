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
gc.threshold(4096)
gc.collect()

from machine import Pin, SPI, SoftI2C
import time

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
    """Acquire SPI bus for LoRa: deassert display CS."""
    _disp_cs.value(1)


def spi_release_lora():
    """Release SPI bus from LoRa."""
    pass  # LoRa driver manages its own CS


# --- Init display ---
import st7789py as st7789
import vga2_8x16 as font

dc = Pin(DISP_DC, Pin.OUT)
bl = Pin(DISP_BL, Pin.OUT)
bl.value(1)

spi_acquire_display()
tft = st7789.ST7789(spi, 240, 320, dc=dc, cs=_disp_cs, backlight=bl, rotation=1)
tft.fill(0x0821)  # BG_DARK

# Splash: render 1bpp logo (180x180) centered, then "Loading..." below
try:
    _logo_w, _logo_h = 180, 180
    _stride = 23  # 180 bits = 22.5 bytes, padded to 23 (184 bits per row)
    _logo_x = (320 - _logo_w) // 2   # 70
    _logo_y = (240 - _logo_h - 20) // 2  # 20, leaves room for text below
    _fg = 0x07FF  # NEON_CYAN
    _bg = 0x0821  # BG_DARK
    # Byte-swap for ST7789 RGB565 little-endian wire format
    _fg_hi = (_fg >> 8) | ((_fg & 0xFF) << 8)
    _bg_hi = (_bg >> 8) | ((_bg & 0xFF) << 8)

    with open("logo.bin", "rb") as f:
        _row_buf = bytearray(_logo_w * 2)
        for _r in range(_logo_h):
            _bits = f.read(_stride)
            _idx = 0
            for _bi in range(_logo_w):
                _byte_idx = _bi >> 3
                _bit_idx = 7 - (_bi & 7)
                _px = _bg_hi if (_bits[_byte_idx] >> _bit_idx) & 1 else _fg_hi
                _row_buf[_idx] = _px & 0xFF
                _row_buf[_idx + 1] = (_px >> 8) & 0xFF
                _idx += 2
            tft.blit_buffer(_row_buf, _logo_x, _logo_y + _r, _logo_w, 1)

    del _row_buf, _bits
    _txt = "Loading..."
    _tx = (320 - len(_txt) * 8) // 2
    _ty = _logo_y + _logo_h + 6
    tft.text(font, _txt, _tx, _ty, 0x07E0, _bg)  # NEON_GREEN
except Exception as e:
    tft.text(font, "Starting...", 100, 112, 0x07FF, 0x0821)
    if DEBUG >= 1:
        print("Splash error:", e)

spi_release_display()
# Clean up splash temporaries
for _v in ('_row_buf', '_bits', '_logo_w', '_logo_h', '_stride',
           '_logo_x', '_logo_y', '_fg', '_bg', '_fg_hi', '_bg_hi',
           '_txt', '_tx', '_ty', '_r', '_bi', '_byte_idx', '_bit_idx',
           '_px', '_idx'):
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

gc.collect()

# --- Init Reticulum ---
from urns import Reticulum
from urns.lxmf import LXMRouter, FIELD_IMAGE
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
spi_acquire_lora()
rns.setup_interfaces()
spi_release_lora()
gc.collect()

if DEBUG >= 1:
    print("LXMF address:", dest.hexhash)
    print("Free memory:", gc.mem_free(), "bytes")


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
    content = message.content_as_string() or "(binary)"
    source_hash = message.source_hash

    # Extract image if present
    image_data = None
    fields = message.fields if hasattr(message, 'fields') else {}
    if FIELD_IMAGE in fields:
        img_field = fields[FIELD_IMAGE]
        if isinstance(img_field, (list, tuple)) and len(img_field) >= 2:
            if img_field[0] == "jpg" and isinstance(img_field[1], (bytes, bytearray)):
                image_data = bytes(img_field[1])
                if not content or content == "(binary)":
                    content = "[image]"

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

    # Add to chat under the GUI peer key
    gui.add_chat_message(peer_key, False, content, image=image_data)

    # Update RSSI/SNR from interface
    for iface in rns.interfaces:
        if iface.rssi is not None:
            gui.rssi = iface.rssi
            gui.snr = iface.snr
            break

    # Wake screen and play notification
    gui.wake_screen()
    sound.play_rx()
    gc.collect()


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
            _lxmf_to_peer[lxmf_hash] = existing
            gui.dirty = True
            if DEBUG >= 1:
                print("[Peer] (dedup)", display_name or "?",
                      "[" + destination_hash.hex()[:8] + " -> " + existing.hex()[:8] + "]")
            gc.collect()
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
    gui.add_peer(destination_hash, display_name, rssi=rssi)
    gui.wake_screen()
    if DEBUG >= 1:
        print("[Peer]", display_name or "?", "[" + destination_hash.hex()[:8] + "]")
    gc.collect()


router.register_delivery_callback(on_message)
router.register_announce_callback(on_announce)


# --- GUI -> LXMF wiring ---

def gui_send(dest_hash, text, msg_idx=None):
    """Called by GUI when user sends a message."""
    import uasyncio as asyncio
    asyncio.create_task(_async_send(dest_hash, text, msg_idx))


async def _async_send(dest_hash, text, msg_idx=None):
    """Send LXMF message as async task (crypto is slow)."""
    import uasyncio as asyncio
    await asyncio.sleep(0)

    # msg_idx is passed from GUI (message already added with status=1)
    if msg_idx is None:
        msg_idx = gui.add_chat_message(dest_hash, True, text, status=1)

    try:
        msg = router.send_message(dest_hash, text)
        if msg:
            sound.play_tx()
            if DEBUG >= 1:
                print("[TX] Sent to", dest_hash.hex()[:8])

            # Mark as sent — receipt tracking not available in upstream API
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
    from urns.transport import Transport
    # Check if already running
    for iface in rns.interfaces:
        if iface.__class__.__name__ == 'LoRaInterface':
            return
    import uasyncio as asyncio
    spi_acquire_lora()
    try:
        rns.config["interfaces"] = [LORA_CONFIG]
        for iface_config in rns.config.get("interfaces", []):
            if iface_config.get("type") == "LoRaInterface":
                from urns.interfaces.lora import LoRaInterface
                iface = LoRaInterface(iface_config)
                rns.interfaces.append(iface)
                Transport.register_interface(iface)
                asyncio.create_task(iface.poll_loop())
                if DEBUG >= 1:
                    print("[LoRa] Interface restarted")
    finally:
        spi_release_lora()


_tcp_iface = None  # track separately so rns.run() doesn't double-start it


def tcp_toggle(enabled, host=None, port=None):
    global _tcp_iface
    import uasyncio as asyncio
    from urns.transport import Transport
    if enabled:
        TCP_CONFIG["target_host"] = host
        TCP_CONFIG["target_port"] = port
        from urns.interfaces.tcp import TCPClientInterface
        iface = TCPClientInterface(TCP_CONFIG)
        if iface.online:
            # Stop LoRa — only one interface at a time
            _stop_lora()
            gui.clear_peers()
            _lxmf_to_peer.clear()
            Transport.register_interface(iface)
            asyncio.create_task(iface.poll_loop())
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


def set_node_name(name):
    """Called by GUI when user changes node name."""
    global NODE_NAME
    NODE_NAME = name
    gui.node_name = name
    dest.display_name = name
    settings = _load_settings()
    settings["node_name"] = name
    _save_settings(settings)
    if DEBUG >= 1:
        print("[Settings] Node name:", name)


gui.on_send = gui_send
gui.on_announce = gui_announce
gui.on_wifi_scan = wifi_scan
gui.on_wifi_connect = wifi_connect
gui.on_tcp_toggle = tcp_toggle
gui.on_node_name = set_node_name
gui._tcp_default = TCP_CONFIG["target_host"] + ":" + str(TCP_CONFIG["target_port"])


# --- Async tasks ---

async def initial_announce():
    import uasyncio as asyncio
    await asyncio.sleep(0.5)
    try:
        router.announce()
        if DEBUG >= 1:
            print("Announced as:", NODE_NAME)
    except Exception as e:
        if DEBUG >= 2:
            print("Initial announce error:", e)
    gc.collect()


async def reannounce_loop():
    import uasyncio as asyncio
    while True:
        await asyncio.sleep(300)
        try:
            router.announce()
            gui.announce_flash = time.time()
            gui.dirty = True
            if DEBUG >= 2:
                print("[Re-announced]")
        except Exception as e:
            if DEBUG >= 2:
                print("Re-announce error:", e)
        gc.collect()


# --- Main ---

def _auto_connect_wifi():
    """Restore WiFi and node name from saved settings on boot (synchronous)."""
    global NODE_NAME
    settings = _load_settings()
    saved_name = settings.get("node_name")
    if saved_name:
        NODE_NAME = saved_name
        gui.node_name = saved_name
        dest.display_name = saved_name
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

    gc.threshold(-1)  # Relax GC for runtime

    _auto_connect_wifi()

    if DEBUG >= 1:
        print("Starting event loop...")

    _original_run = rns.run

    async def run_all():
        asyncio.create_task(_auto_start_tcp())
        asyncio.create_task(initial_announce())
        asyncio.create_task(reannounce_loop())
        asyncio.create_task(gui.kbd_loop())
        asyncio.create_task(gui.gui_loop(spi_acquire_display, spi_release_display))
        asyncio.create_task(gui.battery_loop(spi_acquire_display, spi_release_display))
        await _original_run()

    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        sound.deinit()
        rns.shutdown()
        if DEBUG >= 1:
            print("Shutdown complete")


main()
