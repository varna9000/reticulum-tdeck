# tjpgd_fast_xtensawin built-in module.
# Glue lives here; TJpgDec source + tjpgdcnf.h config are referenced from
# tools/natmod/tjpgd_fast (do NOT add its fake libc include/ shim dir —
# the firmware links real newlib).
set(TJPGD_NATMOD ${CMAKE_CURRENT_LIST_DIR}/../../natmod/tjpgd_fast)

add_library(usermod_tjpgd_fast INTERFACE)

target_sources(usermod_tjpgd_fast INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/tjpgd_fast_module.c
    ${CMAKE_CURRENT_LIST_DIR}/tjpgd_renamed.c
)

target_include_directories(usermod_tjpgd_fast INTERFACE
    ${TJPGD_NATMOD}
)


target_link_libraries(usermod INTERFACE usermod_tjpgd_fast)
