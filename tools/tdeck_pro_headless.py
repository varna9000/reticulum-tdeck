# Headless Reticulum LXMF node for the T-Deck Pro.
#
# Phase 1 of the port: prove the radio is on air before the display and
# keyboard are in the picture at all. No UI, no e-ink, nothing to go wrong
# except the radio itself.
#
# Deploy with deploy_pro_headless.sh, then:
#   mpremote connect /dev/cu.usbmodem101 run tools/tdeck_pro_headless.py
#
# It announces on LoRa and prints every announce it hears. A peer running
# MeshChat, Sideband or NomadNet on the same radio parameters should see this
# node appear, and this node should list theirs.

import gc
import time
import uasyncio as asyncio
from machine import Pin, SPI

from tdeck_pro_config import (
    BOARD_1V8_EN, LORA_EN,
    LORA_SCK, LORA_MOSI, LORA_MISO,
    NODE_NAME, DEBUG, CONFIG, LORA_CONFIG,
)

# --- power ------------------------------------------------------------------
# Both gates high before anything touches the bus. Skipping this is the single
# most convincing way to make a correct pin map look like dead hardware.
Pin(BOARD_1V8_EN, Pin.OUT).value(1)
Pin(LORA_EN, Pin.OUT).value(1)
time.sleep_ms(100)

# --- SPI --------------------------------------------------------------------
# The panel shares this bus but is untouched here, so no arbitration is needed
# beyond keeping its chip select deasserted.
_SPI_PINS = {"sck": Pin(LORA_SCK), "mosi": Pin(LORA_MOSI),
             "miso": Pin(LORA_MISO)}
spi = SPI(1, baudrate=10_000_000, **_SPI_PINS)
Pin(34, Pin.OUT, value=1)          # e-ink CS, held high


def spi_acquire_lora():
    pass


def spi_release_lora():
    pass


LORA_CONFIG["spi"] = spi
LORA_CONFIG["spi_acquire"] = spi_acquire_lora
LORA_CONFIG["spi_release"] = spi_release_lora


def _lora_status(ok, detail=None):
    print("[LoRa] %s%s" % ("up" if ok else "DOWN",
                           (" (%s)" % detail) if detail else ""))


LORA_CONFIG["on_status"] = _lora_status

# --- Reticulum --------------------------------------------------------------
from urns import Reticulum
from urns.lxmf import LXMRouter
from urns.log import LOG_NONE, LOG_NOTICE, LOG_DEBUG

_log = {0: LOG_NONE, 1: LOG_NOTICE, 2: LOG_DEBUG}
rns = Reticulum(loglevel=_log.get(DEBUG, LOG_NOTICE))
rns.config = CONFIG
rns.config["interfaces"] = [LORA_CONFIG]
rns.setup_interfaces()

router = LXMRouter(identity=rns.identity)
# This call is what creates the delivery destination. Setting display_name on
# its own is not enough: LXMRouter.announce() is a silent no-op while
# delivery_destination is None, so the node looks like it is announcing into a
# dead radio when in fact it never built anything to announce.
router.register_delivery_identity(rns.identity, display_name=NODE_NAME)

peers = {}


def on_announce(destination_hash, display_name):
    key = destination_hash.hex() if hasattr(destination_hash, "hex") else \
        "".join("%02x" % b for b in destination_hash)
    if key not in peers:
        peers[key] = display_name
        print("[peer] %s  %s" % (key[:16], display_name or "(no name)"))


router.register_announce_callback(on_announce)


def on_message(message):
    try:
        print("[msg ] from %s: %s" %
              (message.source_hash.hex()[:16], message.content.decode()))
    except Exception as e:
        print("[msg ] undecodable:", e)


router.register_delivery_callback(on_message)

print("=" * 46)
print("T-Deck Pro headless Reticulum node")
print("name    :", NODE_NAME)
print("identity:", rns.identity.hash.hex())
print("lxmf    :", router.delivery_destination.hash.hex()
      if getattr(router, "delivery_destination", None) else "(pending)")
print("radio   : %d kHz sf%d bw%s cr%d %ddBm" % (
    LORA_CONFIG["freq_khz"], LORA_CONFIG["sf"], LORA_CONFIG["bw"],
    LORA_CONFIG["coding_rate"], LORA_CONFIG["tx_power"]))
for iface in rns.interfaces:
    print("iface   :", iface)
print("=" * 46)
gc.collect()
print("free heap:", gc.mem_free())


async def announce_loop():
    await asyncio.sleep(2)
    while True:
        try:
            router.announce()
            print("[announce] sent")
        except Exception as e:
            print("[announce] error:", e)
        await asyncio.sleep(120)


async def status_loop():
    while True:
        await asyncio.sleep(30)
        for iface in rns.interfaces:
            rssi = getattr(iface, "rssi", None)
            if rssi is not None:
                print("[rssi] %s" % rssi)
        print("[status] peers=%d heap=%d" % (len(peers), gc.mem_free()))


async def main():
    asyncio.create_task(announce_loop())
    asyncio.create_task(status_loop())
    await rns.run()


try:
    asyncio.run(main())
except KeyboardInterrupt:
    print("stopping")
    rns.shutdown()
