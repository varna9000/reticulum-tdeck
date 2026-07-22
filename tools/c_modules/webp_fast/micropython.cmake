# webp_fast_xtensawin built-in module. simplewebp.h is header-only; the
# implementation is instantiated inside webp_fast_module.c (via #define),
# so no extra sources and no global compile flags are needed.
set(WEBP_NATMOD ${CMAKE_CURRENT_LIST_DIR}/../../natmod/webp_fast)

add_library(usermod_webp_fast INTERFACE)

target_sources(usermod_webp_fast INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/webp_fast_module.c
)

target_include_directories(usermod_webp_fast INTERFACE
    ${WEBP_NATMOD}
)

target_link_libraries(usermod INTERFACE usermod_webp_fast)
