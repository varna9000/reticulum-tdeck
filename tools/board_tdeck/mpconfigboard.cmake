set(IDF_TARGET esp32s3)

set(SDKCONFIG_DEFAULTS
    boards/sdkconfig.base
    ${SDKCONFIG_IDF_VERSION_SPECIFIC}
    boards/sdkconfig.usb
    # sdkconfig.ble left out: nothing uses Bluetooth (~160KB of flash,
    # ~14KB of IRAM). MICROPY_PY_BLUETOOTH is 0 in mpconfigboard.h.
    boards/sdkconfig.spiram_sx
    ${MICROPY_BOARD_DIR}/sdkconfig.board
)
