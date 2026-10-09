# lowpower built-in module: light sleep with wake on any GPIO level. See the .c.
add_library(usermod_lowpower INTERFACE)
target_sources(usermod_lowpower INTERFACE ${CMAKE_CURRENT_LIST_DIR}/lowpower_module.c)
target_link_libraries(usermod INTERFACE usermod_lowpower)
