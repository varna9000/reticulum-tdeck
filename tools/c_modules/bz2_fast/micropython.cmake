# bz2_fast_xtensawin built-in module (self-contained single file).
add_library(usermod_bz2_fast INTERFACE)

target_sources(usermod_bz2_fast INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/bz2_fast_module.c
)


target_link_libraries(usermod INTERFACE usermod_bz2_fast)
