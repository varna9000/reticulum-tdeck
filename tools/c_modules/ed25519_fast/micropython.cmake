# ed25519_fast_xtensawin built-in module.
# Glue lives here; Monocypher sources are referenced unmodified from the
# uP-reticulum submodule (they are libc-free by design).
set(ED25519_NATMOD ${CMAKE_CURRENT_LIST_DIR}/../../../vendor/uP-reticulum/tools/natmod/ed25519_fast)

add_library(usermod_ed25519_fast INTERFACE)

target_sources(usermod_ed25519_fast INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/ed25519_fast_module.c
    ${ED25519_NATMOD}/monocypher.c
    ${ED25519_NATMOD}/monocypher-ed25519.c
)

target_include_directories(usermod_ed25519_fast INTERFACE
    ${ED25519_NATMOD}
)


target_link_libraries(usermod INTERFACE usermod_ed25519_fast)
