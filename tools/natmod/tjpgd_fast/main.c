/*
 * tjpgd_fast — MicroPython native module for JPEG -> RGB565 decoding
 *
 * Wraps TJpgDec (elm-chan.org) for in-memory JPEG decode.
 * Output is big-endian RGB565 (ready for ST7789 SPI blit_buffer).
 *
 * API:
 *   import tjpgd_fast_xtensawin as tjpgd
 *   w, h, rgb565 = tjpgd.decode(jpeg_bytes)                  # native size
 *   w, h, rgb565 = tjpgd.decode(jpeg_bytes, target_w, target_h)  # scaled
 *   w, h = tjpgd.info(jpeg_bytes)
 */

#include "py/dynruntime.h"
#include "compat.h"
#include "tjpgd.h"
#include "tjpgdcnf.h"

typedef struct {
    const uint8_t *jpeg_data;
    size_t         jpeg_len;
    size_t         jpeg_pos;
    uint8_t       *out_buf;
    uint16_t       out_width;
    uint16_t       out_height;
} decode_ctx_t;

static size_t tjpgd_input(JDEC *jd, uint8_t *buff, size_t ndata) {
    decode_ctx_t *ctx = (decode_ctx_t *)jd->device;
    size_t avail = ctx->jpeg_len - ctx->jpeg_pos;
    if (ndata > avail) ndata = avail;
    if (buff) {
        memcpy(buff, ctx->jpeg_data + ctx->jpeg_pos, ndata);
    }
    ctx->jpeg_pos += ndata;
    return ndata;
}

/* Output callback: copy MCU block into RGB565 buffer (native byte order) */
static int tjpgd_output(JDEC *jd, void *bitmap, JRECT *rect) {
    decode_ctx_t *ctx = (decode_ctx_t *)jd->device;
    uint16_t *src = (uint16_t *)bitmap;
    uint16_t *dst = (uint16_t *)ctx->out_buf;
    uint16_t w = ctx->out_width;

    for (uint16_t y = rect->top; y <= rect->bottom; y++) {
        for (uint16_t x = rect->left; x <= rect->right; x++) {
            dst[y * w + x] = *src++;
        }
    }
    return 1;
}

/* Nearest-neighbor resize in-place from src to dst buffer */
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

/* decode(jpeg_bytes [, target_w, target_h]) -> (width, height, rgb565_bytes)
 * With no size args: decode at native JPEG resolution.
 * With target_w, target_h: decode then nearest-neighbor scale to target. */
static mp_obj_t mod_decode(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t jpeg_buf;
    mp_get_buffer_raise(args[0], &jpeg_buf, MP_BUFFER_READ);

    uint16_t target_w = 0, target_h = 0;
    if (n_args >= 3) {
        target_w = (uint16_t)mp_obj_get_int(args[1]);
        target_h = (uint16_t)mp_obj_get_int(args[2]);
    }

    decode_ctx_t ctx;
    ctx.jpeg_data = jpeg_buf.buf;
    ctx.jpeg_len = jpeg_buf.len;
    ctx.jpeg_pos = 0;
    ctx.out_buf = NULL;

    void *work = m_malloc(TJPGD_WORKSPACE_SIZE);
    JDEC jd;

    JRESULT rc = jd_prepare(&jd, tjpgd_input, work, TJPGD_WORKSPACE_SIZE, &ctx);
    if (rc != JDR_OK) {
        m_free(work);
        mp_raise_ValueError(MP_ERROR_TEXT("JPEG prepare fail"));
    }

    /* Pick best TJpgDec scale: smallest where decoded >= target */
    uint8_t scale = 0;
    if (target_w && target_h) {
        for (uint8_t s = 1; s <= 3; s++) {
            if ((jd.width >> s) >= target_w && (jd.height >> s) >= target_h)
                scale = s;
            else
                break;
        }
    }

    ctx.out_width = jd.width >> scale;
    ctx.out_height = jd.height >> scale;
    if (ctx.out_width == 0 || ctx.out_height == 0) {
        m_free(work);
        mp_raise_ValueError(MP_ERROR_TEXT("JPEG too small"));
    }

    size_t dec_size = (size_t)ctx.out_width * ctx.out_height * 2;
    ctx.out_buf = m_malloc(dec_size);

    rc = jd_decomp(&jd, tjpgd_output, scale);
    m_free(work);

    if (rc != JDR_OK) {
        m_free(ctx.out_buf);
        mp_raise_ValueError(MP_ERROR_TEXT("JPEG decode fail"));
    }

    uint16_t final_w = ctx.out_width;
    uint16_t final_h = ctx.out_height;
    uint8_t *final_buf = ctx.out_buf;

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

/* info(jpeg_bytes) -> (width, height) */
static mp_obj_t mod_info(mp_obj_t jpeg_obj) {
    mp_buffer_info_t jpeg_buf;
    mp_get_buffer_raise(jpeg_obj, &jpeg_buf, MP_BUFFER_READ);

    decode_ctx_t ctx;
    ctx.jpeg_data = jpeg_buf.buf;
    ctx.jpeg_len = jpeg_buf.len;
    ctx.jpeg_pos = 0;
    ctx.out_buf = NULL;

    void *work = m_malloc(TJPGD_WORKSPACE_SIZE);
    JDEC jd;

    JRESULT rc = jd_prepare(&jd, tjpgd_input, work, TJPGD_WORKSPACE_SIZE, &ctx);
    m_free(work);

    if (rc != JDR_OK) {
        mp_raise_ValueError(MP_ERROR_TEXT("JPEG parse fail"));
    }

    mp_obj_t tuple[2] = {
        mp_obj_new_int(jd.width),
        mp_obj_new_int(jd.height),
    };
    return mp_obj_new_tuple(2, tuple);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_info_obj, mod_info);

mp_obj_t mpy_init(mp_obj_fun_bc_t *self, size_t n_args, size_t n_kw, mp_obj_t *args) {
    MP_DYNRUNTIME_INIT_ENTRY
    mp_store_global(MP_QSTR_decode, MP_OBJ_FROM_PTR(&mod_decode_obj));
    mp_store_global(MP_QSTR_info, MP_OBJ_FROM_PTR(&mod_info_obj));
    MP_DYNRUNTIME_INIT_EXIT
}
