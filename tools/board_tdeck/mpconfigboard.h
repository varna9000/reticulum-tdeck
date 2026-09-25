#ifndef MICROPY_HW_BOARD_NAME
// Can be set by mpconfigboard.cmake.
#define MICROPY_HW_BOARD_NAME               "Generic ESP32S3 module"
#endif
#define MICROPY_HW_MCU_NAME                 "ESP32S3"

// Enable UART REPL for modules that have an external USB-UART and don't use native USB.
#define MICROPY_HW_ENABLE_UART_REPL         (1)

#define MICROPY_HW_I2C0_SCL                 (9)
#define MICROPY_HW_I2C0_SDA                 (8)

// Features the T-Deck never uses, cut to keep the app image inside the 2MiB
// M5Launcher slot: Bluetooth (with sdkconfig.ble dropped from
// mpconfigboard.cmake), ESP-NOW, and SPI Ethernet (no PHY on this board;
// see sdkconfig.board).
#define MICROPY_PY_BLUETOOTH                (0)
#define MICROPY_PY_ESPNOW                   (0)
#define MICROPY_PY_NETWORK_LAN              (0)
