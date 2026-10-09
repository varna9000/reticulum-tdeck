#ifndef MICROPY_HW_BOARD_NAME
// Can be set by mpconfigboard.cmake.
#define MICROPY_HW_BOARD_NAME               "Generic ESP32S3 module"
#endif
#define MICROPY_HW_MCU_NAME                 "ESP32S3"

// Enable UART REPL for modules that have an external USB-UART and don't use native USB.
// The T-Deck Pro variant turns this OFF (mpconfigvariant_TDECK_PRO.cmake):
// UART0's RX pin, GPIO44, is the Pro's GPS TX. The GPS stream lands in the
// stdin ring buffer the USB CDC shares, fills it within a second of boot, and
// a full buffer makes the CDC receive path drop every byte -- including
// Ctrl-C -- so the USB port goes deaf while the app runs. Confirmed on
// hardware 2026-10-09 (400 stray stdin bytes in 3 s with the app halted).
#ifndef MICROPY_HW_ENABLE_UART_REPL
#define MICROPY_HW_ENABLE_UART_REPL         (1)
#endif

#define MICROPY_HW_I2C0_SCL                 (9)
#define MICROPY_HW_I2C0_SDA                 (8)

// Features the T-Deck never uses, cut to keep the app image inside the 2MiB
// M5Launcher slot: Bluetooth (with sdkconfig.ble dropped from
// mpconfigboard.cmake), ESP-NOW, and SPI Ethernet (no PHY on this board;
// see sdkconfig.board).
#define MICROPY_PY_BLUETOOTH                (0)
#define MICROPY_PY_ESPNOW                   (0)
#define MICROPY_PY_NETWORK_LAN              (0)
