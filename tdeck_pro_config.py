# T-Deck Pro Configuration
# Pin definitions and LoRa parameters
#
# The Pro is a different machine from the T-Deck v1, not a revision of it:
#   display   GDEQ031T10 e-ink (UC8253), 240x320, no backlight
#   keyboard  TCA8418 I2C key-matrix controller (v1 uses an ESP32-C3 at 0x55)
#   pointing  CST328 capacitive touch, no trackball
#   audio     PCM5102A I2S DAC (v1 uses MAX98357A + ES7210)
#   extras    GPS, BQ25896 PMU, BQ27220 fuel gauge, DRV2605 haptics, 4G modem slot
#
# Pins follow LilyGO's board schematic, hardware/T-Deckpro v1.0 24-05-16/ in
# Xinyuan-LilyGO/T-Deck-Pro, which is the authority where it and Meshtastic's
# variants/esp32s3/t-deck-pro/variant.h disagree -- see GPS_EN below.
# Anything still marked UNVERIFIED needs a bench check before it is trusted.
#
# This is board revision v1.0. LilyGO's v1.1 moves five pins; a v1.1 deck needs
# its own config.

import binascii
import machine

NODE_NAME = "TDeckPro-" + binascii.hexlify(machine.unique_id()[-2:]).decode().upper()

# 0 = silent (release default), 1 = app prints, 2 = + full Reticulum debug log.
DEBUG = 0

# --- Power gates -----------------------------------------------------------
# Both must be driven high before the radio or the panel will answer. This is
# the single most common reason a correct pin map still reads back nothing.
BOARD_1V8_EN = 38
LORA_EN      = 46

# --- Display (GDEQ031T10 e-ink on shared SPI1) -----------------------------
DISP_CS   = 34
DISP_DC   = 35
DISP_BUSY = 37
DISP_RES  = -1     # no dedicated reset line; panel resets over command
DISP_SCK  = 36
# CONFIRMED on hardware 2026-08-28: the panel is on MOSI 33, shared with the
# radio, and Meshtastic's PIN_EINK_MOSI 47 define is wrong. Driving the panel
# on 47 produced no response at all; on 33 it held BUSY low for 3.07 s, a real
# refresh. So the display and the radio share one pin set here, exactly as they
# do on the T-Deck v1, and only the clock rate changes between them.
DISP_MOSI = 33

DISP_W = 240
DISP_H = 320

# --- LoRa SX1262 (on shared SPI1) ------------------------------------------
LORA_SCK  = 36
LORA_MOSI = 33
LORA_MISO = 47
LORA_CS   = 3
LORA_RST  = 4
LORA_BUSY = 6      # variant.h calls this LORA_DIO2; it is the SX1262 BUSY line
LORA_DIO1 = 5      # SX1262 IRQ

# --- I2C bus ---------------------------------------------------------------
# CONFIRMED on hardware 2026-08-28 by scanning candidate pairs. variant.h never
# names these; it defers to the Arduino core's defaults.
# Devices found: 0x1A CST328 touch, 0x28, 0x34 TCA8418 keyboard,
#                0x55 BQ27220 fuel gauge, 0x5A DRV2605 haptics, 0x6B BQ25896 PMU
I2C_SDA = 13
I2C_SCL = 14

# --- Keyboard (TCA8418 over I2C) -------------------------------------------
KBD_ADDR   = 0x34  # CONFIRMED present
KBD_BL_PIN = 42    # PWM backlight, not an I2C command like the v1
KBD_INT    = 15    # TCA8418 interrupt. Unused: get_key() polls. Schematic
                   # KEY_INT-----IO15; see the GPS_EN note below.

# --- Touch (CST328) --------------------------------------------------------
TOUCH_INT  = 12
TOUCH_RST  = 45
TOUCH_ADDR = 0x1A  # CONFIRMED present

# --- GPS -------------------------------------------------------------------
# 39, not the 15 Meshtastic's t-deck-pro variant.h gives. The schematic reads
# GPS_EN-----IO39 and KEY_INT-----IO15, LilyGO's own header says 39, and their
# t-deck-pro-v1_1 variant corrects it to 39. Driving 15 here would toggle the
# keyboard controller's interrupt line and leave the GPS unpowered.
GPS_EN     = 39
GPS_RX     = 44
GPS_TX     = 43
GPS_PPS    = 1
GPS_BAUD   = 38400

# --- Misc ------------------------------------------------------------------
PIN_VIBRATION = 2   # DRV2605 haptic driver
SDCARD_CS     = 48

# --- Battery ---------------------------------------------------------------
# The Pro has a BQ27220 fuel gauge over I2C rather than a plain ADC divider,
# so there is no BAT_PIN. Battery reporting needs a BQ27220 read, not
# peripherals/adc_reader.py. Left unwired until the gauge driver exists.
BQ27220_DESIGN_CAPACITY = 1400
BQ27220_ADDR = 0x55  # CONFIRMED present

# --- Audio (deferred) ------------------------------------------------------
# PCM5102A I2S DAC. Codec2 voice messaging is not ported: the Pro has no
# ES7210 microphone ADC, and PCM5102A_SCK collides with pin 47, which the
# LoRa/e-ink bus already contends for.
PCM5102A_SCK  = 47
PCM5102A_DIN  = 17
PCM5102A_LRCK = 18

# --- LoRa radio config -----------------------------------------------------
# Board wiring comes from the "tdeck_pro_sx1262" preset in lora_boards.py;
# explicit keys here override it.
LORA_CONFIG = {
    "type": "LoRaInterface",
    "name": "T-Deck Pro LoRa",
    "enabled": True,
    "board": "tdeck_pro_sx1262",
    # Mesh radio params. These must match every node on the mesh.
    # US ISM band (902-928 MHz), unlike the v1 default of 868800 (EU).
    # CONFIRMED 2026-08-28 against the RNode on pi5-llm
    # (~/.reticulum/config, "RNode LoRa Interface"): frequency 914875000,
    # bandwidth 125000, spreadingfactor 8, codingrate 5, txpower 17.
    "freq_khz": 914875,
    "sf": 8,
    "bw": "125",
    "coding_rate": 5,
    "tx_power": 17,
    "preamble_len": 8,
    "crc_en": True,
    "syncword": 0x1424,
    # Listen-before-talk. "auto" calibrates 6dB above the board's own noise
    # floor. The Pro's self-EMI floor is not yet characterised, so this may
    # need a fixed dBm value once measured.
    "lbt_rssi": "auto",
    "lbt_max_ms": 2000,
    # spi, spi_acquire, spi_release, on_status injected at runtime by the node
}

# --- TCP interface config (WiFi) -------------------------------------------
TCP_CONFIG = {
    "type": "TCPClientInterface",
    "name": "WiFi TCP",
    "enabled": True,
    "target_host": "127.0.0.1",
    "target_port": 4242,
}

# --- Reticulum config ------------------------------------------------------
from lora_boards import LORA_BOARDS

CONFIG = {
    "loglevel": 3,
    "enable_transport": False,
    "lora_boards": LORA_BOARDS,
    "interfaces": [LORA_CONFIG],
    "time_sync": {
        "enabled": True,
        "min_sources": 2,
        "tolerance": 120,
    },
    "probe": {"enabled": True},
}
