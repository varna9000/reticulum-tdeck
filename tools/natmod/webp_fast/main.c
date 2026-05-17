/*
 * webp_fast — MicroPython native module for WebP -> RGB565 decoding
 *
 * Wraps SimpleWebP (single-header VP8/VP8L decoder) for in-memory decode.
 * Output is big-endian RGB565 (ready for ST7789 SPI blit_buffer).
 *
 * API:
 *   import webp_fast_xtensawin as webp
 *   w, h, rgb565 = webp.decode(webp_bytes)                   # native size
 *   w, h, rgb565 = webp.decode(webp_bytes, target_w, target_h)  # scaled
 *   w, h = webp.info(webp_bytes)
 */

#include "py/dynruntime.h"
#include "compat.h"

/* SIMPLEWEBP_IMPLEMENTATION and SIMPLEWEBP_DISABLE_STDIO are set via CFLAGS */
#include "simplewebp.h"

/* --- Allocator callbacks for MicroPython heap --- */

static void *mp_alloc(void *userdata, size_t size) {
    (void)userdata;
    return m_malloc(size);
}

static void mp_dealloc(void *userdata, void *mem) {
    (void)userdata;
    if (mem) m_free(mem);
}

/* Allocator must be set up at runtime — natmod can't have
   initialized structs with function pointers (.data.rel.local) */
static simplewebp_allocator mp_allocator;

static void init_allocator(void) {
    mp_allocator.alloc = mp_alloc;
    mp_allocator.free  = mp_dealloc;
    mp_allocator.userdata = NULL;
}

/* --- Nearest-neighbor resize (RGB565) --- */

static void nn_resize(const uint8_t *src, uint16_t sw, uint16_t sh,
                      uint8_t *dst, uint16_t dw, uint16_t dh) {
    for (uint16_t dy = 0; dy < dh; dy++) {
        uint16_t sy = (uint16_t)((uint32_t)dy * sh / dh);
        for (uint16_t dx = 0; dx < dw; dx++) {
            uint16_t sx = (uint16_t)((uint32_t)dx * sw / dw);
            size_t si = (sy * sw + sx) * 2;
            size_t di = (dy * dw + dx) * 2;
            dst[di]     = src[si];
            dst[di + 1] = src[si + 1];
        }
    }
}

/* --- RGBA8 -> big-endian RGB565 conversion --- */

static void rgba_to_rgb565_be(const uint8_t *rgba, uint8_t *rgb565, size_t npixels) {
    for (size_t i = 0; i < npixels; i++) {
        uint8_t r = rgba[i * 4 + 0];
        uint8_t g = rgba[i * 4 + 1];
        uint8_t b = rgba[i * 4 + 2];
        /* RGB565: RRRRRGGG GGGBBBBB (big-endian for ST7789) */
        uint16_t px = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3);
        rgb565[i * 2 + 0] = (uint8_t)(px >> 8);
        rgb565[i * 2 + 1] = (uint8_t)(px & 0xFF);
    }
}

/* decode(webp_bytes [, target_w, target_h]) -> (width, height, rgb565_bytes) */
static mp_obj_t mod_decode(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t webp_buf;
    mp_get_buffer_raise(args[0], &webp_buf, MP_BUFFER_READ);

    uint16_t target_w = 0, target_h = 0;
    if (n_args >= 3) {
        target_w = (uint16_t)mp_obj_get_int(args[1]);
        target_h = (uint16_t)mp_obj_get_int(args[2]);
    }

    /* Load and parse WebP */
    simplewebp *swp = NULL;
    simplewebp_error err = simplewebp_load_from_memory(
        webp_buf.buf, webp_buf.len, &mp_allocator, &swp
    );
    if (err != SIMPLEWEBP_NO_ERROR || swp == NULL) {
        if (swp) simplewebp_unload(swp);
        mp_raise_ValueError(MP_ERROR_TEXT("WebP load fail"));
    }

    /* Get dimensions */
    size_t img_w = 0, img_h = 0;
    simplewebp_get_dimensions(swp, &img_w, &img_h);
    if (img_w == 0 || img_h == 0) {
        simplewebp_unload(swp);
        mp_raise_ValueError(MP_ERROR_TEXT("WebP zero size"));
    }

    /* Allocate RGBA buffer and decode */
    size_t rgba_size = img_w * img_h * 4;
    uint8_t *rgba = m_malloc(rgba_size);

    err = simplewebp_decode(swp, rgba, NULL);
    simplewebp_unload(swp);  /* Free decoder state */
    swp = NULL;

    if (err != SIMPLEWEBP_NO_ERROR) {
        m_free(rgba);
        mp_raise_ValueError(MP_ERROR_TEXT("WebP decode fail"));
    }

    /* Convert RGBA -> RGB565 */
    size_t npixels = img_w * img_h;
    size_t rgb565_size = npixels * 2;
    uint8_t *rgb565 = m_malloc(rgb565_size);
    rgba_to_rgb565_be(rgba, rgb565, npixels);
    m_free(rgba);  /* Free RGBA immediately */

    uint16_t final_w = (uint16_t)img_w;
    uint16_t final_h = (uint16_t)img_h;
    uint8_t *final_buf = rgb565;

    /* Nearest-neighbor resize if target specified */
    if (target_w && target_h && (final_w != target_w || final_h != target_h)) {
        size_t dst_size = (size_t)target_w * target_h * 2;
        uint8_t *dst = m_malloc(dst_size);
        nn_resize(final_buf, final_w, final_h, dst, target_w, target_h);
        m_free(final_buf);
        final_buf = dst;
        final_w = target_w;
        final_h = target_h;
    }

    size_t final_size = (size_t)final_w * final_h * 2;
    mp_obj_t result_bytes = mp_obj_new_bytes(final_buf, final_size);
    m_free(final_buf);

    mp_obj_t tuple[3] = {
        mp_obj_new_int(final_w),
        mp_obj_new_int(final_h),
        result_bytes,
    };
    return mp_obj_new_tuple(3, tuple);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_decode_obj, 1, 3, mod_decode);

/* info(webp_bytes) -> (width, height) */
static mp_obj_t mod_info(mp_obj_t webp_obj) {
    mp_buffer_info_t webp_buf;
    mp_get_buffer_raise(webp_obj, &webp_buf, MP_BUFFER_READ);

    simplewebp *swp = NULL;
    simplewebp_error err = simplewebp_load_from_memory(
        webp_buf.buf, webp_buf.len, &mp_allocator, &swp
    );
    if (err != SIMPLEWEBP_NO_ERROR || swp == NULL) {
        if (swp) simplewebp_unload(swp);
        mp_raise_ValueError(MP_ERROR_TEXT("WebP parse fail"));
    }

    size_t w = 0, h = 0;
    simplewebp_get_dimensions(swp, &w, &h);
    simplewebp_unload(swp);

    mp_obj_t tuple[2] = {
        mp_obj_new_int(w),
        mp_obj_new_int(h),
    };
    return mp_obj_new_tuple(2, tuple);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_info_obj, mod_info);

mp_obj_t mpy_init(mp_obj_fun_bc_t *self, size_t n_args, size_t n_kw, mp_obj_t *args) {
    MP_DYNRUNTIME_INIT_ENTRY
    init_allocator();
    mp_store_global(MP_QSTR_decode, MP_OBJ_FROM_PTR(&mod_decode_obj));
    mp_store_global(MP_QSTR_info, MP_OBJ_FROM_PTR(&mod_info_obj));
    MP_DYNRUNTIME_INIT_EXIT
}
