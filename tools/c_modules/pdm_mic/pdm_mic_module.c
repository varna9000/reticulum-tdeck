// pdm_mic: PDM microphone capture for the T-Deck Pro (ESP32-S3).
//
// The Pro's microphone is a two-wire PDM part (MIC_CLOCK 18, MIC_DATA 17 on
// LilyGO's V1.1 pinmap). MicroPython's machine.I2S speaks standard I2S only,
// so this module opens I2S port 0 -- the only port with a PDM receiver on the
// S3 -- in PDM RX mode through ESP-IDF's new driver (driver/i2s_pdm.h), the
// same driver family machine.I2S uses, so the two coexist. The hardware
// PDM-to-PCM filter hands back 16-bit mono PCM at the configured rate.
//
//   pdm_mic.init(clk_pin, data_pin, rate=16000)
//   pdm_mic.readinto(buf) -> bytes read   (blocks, releases the GIL)
//   pdm_mic.deinit()

#include "py/runtime.h"
#include "py/mphal.h"
#include "py/mpthread.h"
#include "driver/i2s_pdm.h"

static i2s_chan_handle_t pdm_rx = NULL;

static void pdm_close(void) {
    if (pdm_rx != NULL) {
        i2s_channel_disable(pdm_rx);
        i2s_del_channel(pdm_rx);
        pdm_rx = NULL;
    }
}

static mp_obj_t pdm_init(size_t n_args, const mp_obj_t *args) {
    int clk = mp_obj_get_int(args[0]);
    int din = mp_obj_get_int(args[1]);
    int rate = (n_args > 2) ? mp_obj_get_int(args[2]) : 16000;
    pdm_close();

    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    chan_cfg.dma_desc_num = 8;
    chan_cfg.dma_frame_num = 512;      // 8 x 512 x 2 B = 8 KiB of ring, ~256 ms at 16 kHz
    esp_err_t err = i2s_new_channel(&chan_cfg, NULL, &pdm_rx);
    if (err != ESP_OK) {
        pdm_rx = NULL;
        mp_raise_msg_varg(&mp_type_OSError, MP_ERROR_TEXT("i2s_new_channel: %d"), (int)err);
    }

    i2s_pdm_rx_config_t cfg = {
        .clk_cfg = I2S_PDM_RX_CLK_DEFAULT_CONFIG(rate),
        .slot_cfg = I2S_PDM_RX_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .clk = clk,
            .din = din,
            .invert_flags = { .clk_inv = false },
        },
    };
    err = i2s_channel_init_pdm_rx_mode(pdm_rx, &cfg);
    if (err == ESP_OK) {
        err = i2s_channel_enable(pdm_rx);
    }
    if (err != ESP_OK) {
        pdm_close();
        mp_raise_msg_varg(&mp_type_OSError, MP_ERROR_TEXT("pdm rx init: %d"), (int)err);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(pdm_init_obj, 2, 3, pdm_init);

static mp_obj_t pdm_readinto(mp_obj_t buf_in) {
    if (pdm_rx == NULL) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("pdm_mic not initialised"));
    }
    mp_buffer_info_t bufinfo;
    mp_get_buffer_raise(buf_in, &bufinfo, MP_BUFFER_WRITE);
    size_t got = 0;
    MP_THREAD_GIL_EXIT();
    esp_err_t err = i2s_channel_read(pdm_rx, bufinfo.buf, bufinfo.len, &got, 1000);
    MP_THREAD_GIL_ENTER();
    if (err != ESP_OK && err != ESP_ERR_TIMEOUT) {
        mp_raise_msg_varg(&mp_type_OSError, MP_ERROR_TEXT("pdm read: %d"), (int)err);
    }
    return MP_OBJ_NEW_SMALL_INT(got);
}
static MP_DEFINE_CONST_FUN_OBJ_1(pdm_readinto_obj, pdm_readinto);

static mp_obj_t pdm_deinit(void) {
    pdm_close();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(pdm_deinit_obj, pdm_deinit);

static const mp_rom_map_elem_t pdm_mic_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_pdm_mic) },
    { MP_ROM_QSTR(MP_QSTR_init), MP_ROM_PTR(&pdm_init_obj) },
    { MP_ROM_QSTR(MP_QSTR_readinto), MP_ROM_PTR(&pdm_readinto_obj) },
    { MP_ROM_QSTR(MP_QSTR_deinit), MP_ROM_PTR(&pdm_deinit_obj) },
};
static MP_DEFINE_CONST_DICT(pdm_mic_globals, pdm_mic_globals_table);

const mp_obj_module_t pdm_mic_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&pdm_mic_globals,
};
MP_REGISTER_MODULE(MP_QSTR_pdm_mic, pdm_mic_module);
