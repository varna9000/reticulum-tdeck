# Aggregator for all T-Deck user C modules.
# build_firmware.sh points USER_C_MODULES here; st7789 is cloned into
# firmware_build by the script, the five codec/crypto modules are the
# static conversions of the former natmod .mpy files (same import names).
include(${CMAKE_CURRENT_LIST_DIR}/../firmware_build/st7789_mpy/st7789/micropython.cmake)
include(${CMAKE_CURRENT_LIST_DIR}/ed25519_fast/micropython.cmake)
include(${CMAKE_CURRENT_LIST_DIR}/bz2_fast/micropython.cmake)
include(${CMAKE_CURRENT_LIST_DIR}/tjpgd_fast/micropython.cmake)
include(${CMAKE_CURRENT_LIST_DIR}/webp_fast/micropython.cmake)
include(${CMAKE_CURRENT_LIST_DIR}/codec2_fast/micropython.cmake)
