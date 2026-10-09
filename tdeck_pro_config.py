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

# --- Board revision --------------------------------------------------------
# LilyGO ships three revisions (V1.0, V1.1, MAX) that differ in a few pins,
# two of which matter at boot: V1.1/MAX have a panel reset line on GPIO 16 and
# no 1.8 V gate on GPIO 38 (that pin is their touch reset). On the wrong map a
# deck can boot to a blank panel.
#
# "auto" detects the revision on the first boot from the I2C bus, the way
# LilyGO documents it (a DRV2605 at 0x5A means V1.1, an XL9555 at 0x20 means
# MAX), and saves it in /tdeck_pro_hw.json with the audio answer below.
# Set "1.0", "1.1" or "max" to override. See tdeck_pro_rev.py.
BOARD_REV = "auto"

# --- Power gates -----------------------------------------------------------
# Both must be driven high before the radio or the panel will answer. This is
# the single most common reason a correct pin map still reads back nothing.
# The 1.8 V gate (GPIO 38, V1.0 only) is resolved from BOARD_REV in
# board_tdeck_pro.py, as is the panel reset line.
LORA_EN      = 46

# --- Display (GDEQ031T10 e-ink on shared SPI1) -----------------------------
DISP_CS   = 34
DISP_DC   = 35
DISP_BUSY = 37
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

# --- Audio -----------------------------------------------------------------
# The Pro comes in two versions on the same three GPIOs: the PCM5102A I2S DAC
# (notification tones, voice playback) or the A7682E 4G modem. "auto" finds
# out on the FIRST boot the way LilyGO's factory firmware does -- power the
# slot, pulse the modem's power key, send AT for up to ~12 s; silence means
# the DAC -- and saves the answer (with the revision) in /tdeck_pro_hw.json,
# so later boots skip the probe. True/False overrides it; delete that file
# to detect again. Driving these pins as I2S on a modem unit would fight the
# modem's RI/ITR/RST lines, hence the care. Pin numbers are from
# LilyGO's README and their V1.1 pinmap (I2S_BCLK/DOUT/LRC); Meshtastic's
# 47/17/18 are wrong -- 47 is the LoRa/e-ink MISO and 17/18 are the board's
# microphone (MIC_DATA 17, MIC_CLOCK 18, a 2-wire part, so PDM) -- see
# HAS_MIC below.
HAS_AUDIO = "auto"
I2S_BCLK  = 7
I2S_DOUT  = 8
I2S_LRCK  = 9
# The module slot's power enable (LilyGO's BOARD_6609_EN, Meshtastic's
# MODEM_POWER_EN): their test_pcm5102a example raises it before playing, and
# the DAC is silent without it. Raised at boot when HAS_AUDIO.
AUDIO_EN  = 41
# The modem's pins, used only by the first-boot probe (A7682E RXD/TXD are
# named from the modem's side: the ESP transmits on 10 and listens on 11).
MODEM_PWRKEY = 40
MODEM_RXD    = 10
MODEM_TXD    = 11
# The module's speaker is small: the app's notification tones are played
# AUDIO_GAIN times louder and AUDIO_PITCH times higher (2 = one octave up)
# than on the v1, or they are inaudible on it.
# Voice playback gets the gain too (clipped at full scale, never wrapped).
AUDIO_GAIN  = 8
AUDIO_PITCH = 2
# The PDM microphone (MIC_CLOCK / MIC_DATA on LilyGO's V1.1 pinmap) makes
# Codec2 voice messages possible; it is read through the pdm_mic built-in.
HAS_MIC  = True
MIC_CLK  = 18
MIC_DATA = 17

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
    "target_host": "rns.varnatransport.com",
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
