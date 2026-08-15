# ed25519_fast_xtensawin built-in module.
# Glue lives here; Monocypher sources are referenced unmodified from the
# uP-reticulum submodule (they are libc-free by design).
set(ED25519_NATMOD ${CMAKE_CURRENT_LIST_DIR}/../../../vendor/uP-reticulum/tools/natmod/ed25519_fast)

# --- Monocypher core: its own archive, linked into IRAM ---------------------
# A STATIC library rather than INTERFACE sources, for two reasons:
#
#   1. ldgen linker fragments can only target ARCHIVES. INTERFACE sources are
#      compiled straight into micropython.elf (verified in the map file), where
#      no fragment can reach them — so there is no way to place them in IRAM
#      without an archive of their own.
#   2. -ffunction-sections/-fdata-sections lets --gc-sections drop the ~2/3 of
#      Monocypher this firmware never calls (Argon2, Blake2b, ChaCha20,
#      Poly1305). Before this, monocypher.c contributed 122.7KB of flash text
#      as one indivisible .text blob; only the Ed25519/X25519/SHA-512 paths are
#      actually reachable, and only those now get pulled into IRAM.
#
# linker.lf maps the archive with the `noflash` scheme, reproducing what the
# ed25519_iram.mpy natmod did at import — which is what makes a filesystem-less
# (Launcher-installed) build possible without giving up crypto performance.
idf_build_get_property(_mono_copts COMPILE_OPTIONS)
idf_build_get_property(_mono_c_copts C_COMPILE_OPTIONS)
idf_build_get_property(_mono_cdefs COMPILE_DEFINITIONS)

add_library(ed25519_mono STATIC
    ${ED25519_NATMOD}/monocypher.c
    ${ED25519_NATMOD}/monocypher-ed25519.c
)
target_compile_options(ed25519_mono PRIVATE
    ${_mono_copts} ${_mono_c_copts}
    -ffunction-sections -fdata-sections
)
target_compile_definitions(ed25519_mono PRIVATE ${_mono_cdefs})
target_include_directories(ed25519_mono PUBLIC ${ED25519_NATMOD})

# Register the archive with ldgen (so the mapping resolves) and add the
# fragment. These are IDF's own helpers — poking __LDGEN_* properties directly
# would skip the dependency wiring they set up.
__ldgen_add_component(ed25519_mono)
__ldgen_add_fragment_files("${CMAKE_CURRENT_LIST_DIR}/linker.lf")

# --- MicroPython glue -------------------------------------------------------
# Must stay an INTERFACE source so MicroPython's QSTR / MP_REGISTER_MODULE
# scan picks it up; moving it into the archive would silently unregister the
# module. It stays in flash, which costs nothing.
add_library(usermod_ed25519_fast INTERFACE)

target_sources(usermod_ed25519_fast INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/ed25519_fast_module.c
)

target_include_directories(usermod_ed25519_fast INTERFACE
    ${ED25519_NATMOD}
)

target_link_libraries(usermod_ed25519_fast INTERFACE ed25519_mono)
target_link_libraries(usermod INTERFACE usermod_ed25519_fast)
