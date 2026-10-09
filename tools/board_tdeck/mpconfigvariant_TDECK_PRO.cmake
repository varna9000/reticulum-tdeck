# LilyGO T-Deck Pro: same ESP32-S3 module family as the v1 but with QUAD
# PSRAM, so this variant deliberately does NOT pull in sdkconfig.spiram_oct.
# boards/sdkconfig.spiram_sx (always included by mpconfigboard.cmake) already
# sets CONFIG_SPIRAM_MODE_QUAD=y. Octal firmware on this board finds no PSRAM
# and the app cannot fit in internal RAM.
set(SDKCONFIG_DEFAULTS
    ${SDKCONFIG_DEFAULTS}
    boards/sdkconfig.240mhz
)

list(APPEND MICROPY_DEF_BOARD
    MICROPY_HW_BOARD_NAME="LilyGO T-Deck Pro"
    # No UART REPL: UART0 RX (GPIO44) is the GPS TX on this board, and the
    # GPS stream floods the stdin ring buffer shared with USB CDC, which then
    # stops accepting input (Ctrl-C included). See mpconfigboard.h.
    MICROPY_HW_ENABLE_UART_REPL=0
)
