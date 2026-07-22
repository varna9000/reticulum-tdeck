# codec2_fast_xtensawin built-in module.
# Glue lives here; the 16 c2_*.c codec sources are referenced from
# tools/natmod/codec2_fast. The natmod's -O0 and libm_clean.a hacks are
# natmod-linker artifacts and are deliberately NOT carried over: the
# firmware links real newlib libm and builds at the port's default -Os.
set(CODEC2_NATMOD ${CMAKE_CURRENT_LIST_DIR}/../../natmod/codec2_fast)

add_library(usermod_codec2_fast INTERFACE)

target_sources(usermod_codec2_fast INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/codec2_fast_module.c
    ${CODEC2_NATMOD}/c2_codec2.c
    ${CODEC2_NATMOD}/c2_fft.c
    ${CODEC2_NATMOD}/c2_kiss_fft.c
    ${CODEC2_NATMOD}/c2_kiss_fftr.c
    ${CODEC2_NATMOD}/c2_lpc.c
    ${CODEC2_NATMOD}/c2_lsp.c
    ${CODEC2_NATMOD}/c2_lsp_cb.c
    ${CODEC2_NATMOD}/c2_ge_cb.c
    ${CODEC2_NATMOD}/c2_nlp.c
    ${CODEC2_NATMOD}/c2_sine.c
    ${CODEC2_NATMOD}/c2_phase.c
    ${CODEC2_NATMOD}/c2_interp.c
    ${CODEC2_NATMOD}/c2_postfilter.c
    ${CODEC2_NATMOD}/c2_quantise.c
    ${CODEC2_NATMOD}/c2_mbest.c
    ${CODEC2_NATMOD}/c2_pack.c
)

target_include_directories(usermod_codec2_fast INTERFACE
    ${CODEC2_NATMOD}
)

target_link_libraries(usermod INTERFACE usermod_codec2_fast)
