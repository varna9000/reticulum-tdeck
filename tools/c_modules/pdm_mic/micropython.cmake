# pdm_mic built-in module: PDM microphone capture (T-Deck Pro). See the .c.
add_library(usermod_pdm_mic INTERFACE)
target_sources(usermod_pdm_mic INTERFACE ${CMAKE_CURRENT_LIST_DIR}/pdm_mic_module.c)
target_link_libraries(usermod INTERFACE usermod_pdm_mic)
