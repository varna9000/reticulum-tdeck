# T-Deck v1 Configuration
# Pin definitions and LoRa parameters

NODE_NAME = "T-Deck"
DEBUG = 2

# --- Display (ST7789 on shared SPI1) ---
DISP_SCK  = 40
DISP_MOSI = 41
DISP_CS   = 12
DISP_DC   = 11
DISP_BL   = 42

# --- LoRa SX1262 (on shared SPI1) ---
LORA_SCK  = 40
LORA_MOSI = 41
LORA_MISO = 38
LORA_CS   = 9
LORA_RST  = 17
LORA_BUSY = 13
LORA_DIO1 = 45

# --- Keyboard (I2C) ---
KBD_SCL  = 8
KBD_SDA  = 18
KBD_PWR  = 10  # also peripheral power
KBD_INT  = 46
KBD_ADDR = 0x55

# --- Trackball ---
TB_UP    = 3
TB_DOWN  = 15
TB_LEFT  = 1
TB_RIGHT = 2
TB_CLICK = 0

# --- Battery ADC ---
BAT_PIN = 4

# --- Speaker (I2S MAX98357A) ---
SPK_BCK  = 7
SPK_WS   = 5
SPK_DOUT = 6

# --- Microphone (ES7210 I2S ADC) ---
MIC_DIN  = 14   # I2S Data In
MIC_SCK  = 47   # I2S Serial Clock
MIC_LRCK = 21   # I2S Left/Right Clock
MIC_MCLK = 48   # Master Clock

# --- LoRa radio config ---
# Board wiring (pins, TCXO, DC-DC, battery sense) comes from the
# "tdeck_v1_sx1262" preset in lora_boards.py; explicit keys here override it.
LORA_CONFIG = {
    "type": "LoRaInterface",
    "name": "T-Deck LoRa",
    "enabled": True,
    "board": "tdeck_v1_sx1262",
    # Mesh radio params — must match every node on the mesh
    "freq_khz": 868800,
    "sf": 8,
    "bw": "125",
    "coding_rate": 5,
    "tx_power": 22,
    "preamble_len": 8,
    "crc_en": True,
    "syncword": 0x1424,
    # Listen-before-talk (CSMA): defer TX while the channel reads at or
    # above lbt_rssi dBm, up to lbt_max_ms. Set "lbt_rssi": None to disable.
    "lbt_rssi": -100,
    "lbt_max_ms": 2000,
    # spi, spi_acquire, spi_release, on_status injected at runtime by tdeck_node.py
}

# --- TCP interface config (WiFi) ---
TCP_CONFIG = {
    "type": "TCPClientInterface",
    "name": "WiFi TCP",
    "enabled": True,
    "target_host": "127.0.0.1",
    "target_port": 4242,
}

# --- Reticulum config ---
from lora_boards import LORA_BOARDS

CONFIG = {
    "loglevel": 3,
    "enable_transport": False,
    "lora_boards": LORA_BOARDS,
    "interfaces": [LORA_CONFIG],
    # Network time sync: adopt mesh time once per boot when min_sources
    # independent peers agree within tolerance seconds (the T-Deck has no
    # battery-backed RTC, and no NTP when LoRa-only). After a sync the
    # stack re-announces so peers accept our post-boot announces.
    "time_sync": {
        "enabled": True,
        "min_sources": 2,
        "tolerance": 120,
        # "trusted_nodes": ["<identity hash hex>"],  # optional authority mode
    },
    # Answer rnprobe probes (field debugging of multi-hop paths)
    "probe": {"enabled": True},
}
