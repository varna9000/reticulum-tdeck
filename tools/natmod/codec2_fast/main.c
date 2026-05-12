/*
 * codec2_fast — MicroPython native module for Codec2 decoding
 *
 * Wraps Blues Codec2 fork for voice message playback.
 * Output is 16-bit signed LE PCM at 8kHz mono (ready for I2S).
 *
 * Streaming API (non-blocking, decode one frame at a time):
 *   import codec2_fast_xtensawin as codec2
 *   handle = codec2.create(mode)       # mode: 0=3200, 1=2400
 *   pcm = codec2.decode_frame(handle, frame_bytes, gain)  # 320B PCM per frame
 *   codec2.destroy(handle)
 *
 * Batch API (convenience, blocks for full decode):
 *   pcm_bytes = codec2.decode(codec2_bytes, mode, gain)
 *
 *   n_frames, duration_ms = codec2.info(codec2_bytes, mode)
 */

#include "py/dynruntime.h"
#include "compat.h"
#include "c2_codec2.h"
#include "c2_internal.h"
#include "c2_quantise.h"
#include "c2_lsp.h"
#include "c2_lpc.h"
/* CODEC2_MODE_3200 defined in c2_codec2.h */
/* --- codec2_malloc/calloc/free for MicroPython heap ---
 *
 * Uses m_malloc (GC-tracked). Safe as long as GC doesn't run
 * between allocations. We call gc.collect() from Python BEFORE
 * entering C, ensuring enough free space for all allocations
 * without triggering GC internally.
 */

void *codec2_malloc(size_t size) {
    return m_malloc(size);
}

void *codec2_calloc(size_t nmemb, size_t size) {
    size_t total = nmemb * size;
    void *ptr = m_malloc(total);
    if (ptr) {
        uint8_t *p = (uint8_t *)ptr;
        for (size_t i = 0; i < total; i++) p[i] = 0;
    }
    return ptr;
}

void codec2_free(void *ptr) {
    if (ptr) m_free(ptr);
}

/* --- Streaming API: create / decode_frame / destroy --- */

/* create(mode) -> handle (int)
 * mode: 0=3200bps, 1=2400bps */
static mp_obj_t mod_create(mp_obj_t mode_obj) {
    int mode = mp_obj_get_int(mode_obj);

    struct CODEC2 *c2 = codec2_create(mode);
    if (!c2) {
        mp_raise_ValueError(MP_ERROR_TEXT("codec2 create fail"));
    }
    return mp_obj_new_int((mp_int_t)(uintptr_t)c2);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_create_obj, mod_create);

/* decode_frame(handle, frame_bytes, gain) -> pcm_bytes (320B)
 * Decodes one frame. Call repeatedly for each frame. */
static mp_obj_t mod_decode_frame(mp_obj_t handle_obj, mp_obj_t frame_obj, mp_obj_t gain_obj) {
    struct CODEC2 *c2 = (struct CODEC2 *)(uintptr_t)mp_obj_get_int(handle_obj);
    int gain = mp_obj_get_int(gain_obj);

    mp_buffer_info_t buf;
    mp_get_buffer_raise(frame_obj, &buf, MP_BUFFER_READ);

    int samples = codec2_samples_per_frame(c2);  /* 160 */
    int pcm_size = samples * 2;                   /* 320 bytes */
    short *pcm = (short *)m_malloc(pcm_size);

    codec2_decode(c2, pcm, (const unsigned char *)buf.buf);

    /* Apply gain */
    if (gain > 1) {
        for (int i = 0; i < samples; i++) {
            int32_t s = (int32_t)pcm[i] * gain;
            if (s > 32767) s = 32767;
            if (s < -32767) s = -32767;
            pcm[i] = (short)s;
        }
    }

    mp_obj_t result = mp_obj_new_bytes((uint8_t *)pcm, pcm_size);
    m_free(pcm);
    return result;
}
static MP_DEFINE_CONST_FUN_OBJ_3(mod_decode_frame_obj, mod_decode_frame);

/* encode_frame(handle, pcm_bytes) -> codec2_bytes
 * Encodes one frame: 160 PCM samples (320B) -> 8 bytes (mode 3200). */
static mp_obj_t mod_encode_frame(mp_obj_t handle_obj, mp_obj_t pcm_obj) {
    struct CODEC2 *c2 = (struct CODEC2 *)(uintptr_t)mp_obj_get_int(handle_obj);

    mp_buffer_info_t buf;
    mp_get_buffer_raise(pcm_obj, &buf, MP_BUFFER_READ);

    int bits_per_frame = codec2_bits_per_frame(c2);
    int bytes_per_frame = (bits_per_frame + 7) / 8;
    uint8_t *out = (uint8_t *)m_malloc(bytes_per_frame);

    codec2_encode(c2, out, (short *)buf.buf);

    mp_obj_t result = mp_obj_new_bytes(out, bytes_per_frame);
    m_free(out);
    return result;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_encode_frame_obj, mod_encode_frame);

/* destroy(handle) -> None */
static mp_obj_t mod_destroy(mp_obj_t handle_obj) {
    struct CODEC2 *c2 = (struct CODEC2 *)(uintptr_t)mp_obj_get_int(handle_obj);
    if (c2) {
        codec2_destroy(c2);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_destroy_obj, mod_destroy);

/* --- Batch API (convenience) --- */

/* decode(codec2_bytes [, mode [, gain]]) -> pcm_bytes */
static mp_obj_t mod_decode(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t buf;
    mp_get_buffer_raise(args[0], &buf, MP_BUFFER_READ);

    int mode = CODEC2_MODE_3200;
    if (n_args >= 2) {
        mode = mp_obj_get_int(args[1]);
    }
    int gain = 1;
    if (n_args >= 3) {
        gain = mp_obj_get_int(args[2]);
    }

    struct CODEC2 *c2 = codec2_create(mode);
    if (!c2) {
        mp_raise_ValueError(MP_ERROR_TEXT("codec2 create fail"));
    }

    int samples_per_frame = codec2_samples_per_frame(c2);
    int bits_per_frame = codec2_bits_per_frame(c2);
    int bytes_per_frame = (bits_per_frame + 7) / 8;
    int pcm_bytes_per_frame = samples_per_frame * 2;

    if (bytes_per_frame == 0 || buf.len < (size_t)bytes_per_frame) {
        codec2_destroy(c2);
        mp_raise_ValueError(MP_ERROR_TEXT("codec2 data too short"));
    }

    int n_frames = buf.len / bytes_per_frame;
    size_t pcm_size = (size_t)n_frames * pcm_bytes_per_frame;
    short *pcm_buf = (short *)m_malloc(pcm_size);

    const uint8_t *src = (const uint8_t *)buf.buf;
    for (int i = 0; i < n_frames; i++) {
        codec2_decode(c2, pcm_buf + i * samples_per_frame,
                      src + i * bytes_per_frame);
    }

    codec2_destroy(c2);

    if (gain > 1) {
        size_t n_samples = pcm_size / 2;
        for (size_t i = 0; i < n_samples; i++) {
            int32_t s = (int32_t)pcm_buf[i] * gain;
            if (s > 32767) s = 32767;
            if (s < -32767) s = -32767;
            pcm_buf[i] = (short)s;
        }
    }

    mp_obj_t result = mp_obj_new_bytes((uint8_t *)pcm_buf, pcm_size);
    m_free(pcm_buf);

    return result;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_decode_obj, 1, 3, mod_decode);

/* encode(pcm_bytes [, mode]) -> codec2_bytes
 * PCM: 16-bit LE signed, 8kHz mono. 160 samples (320B) per frame.
 * Returns concatenated codec2 bitstream. */
static mp_obj_t mod_encode(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t buf;
    mp_get_buffer_raise(args[0], &buf, MP_BUFFER_READ);

    int mode = CODEC2_MODE_3200;
    if (n_args >= 2) {
        mode = mp_obj_get_int(args[1]);
    }

    struct CODEC2 *c2 = codec2_create(mode);
    if (!c2) {
        mp_raise_ValueError(MP_ERROR_TEXT("codec2 create fail"));
    }

    int samples_per_frame = codec2_samples_per_frame(c2);  /* 160 */
    int bits_per_frame = codec2_bits_per_frame(c2);
    int bytes_per_frame = (bits_per_frame + 7) / 8;         /* 8 for 3200 */
    int pcm_bytes_per_frame = samples_per_frame * 2;        /* 320 */

    if (buf.len < (size_t)pcm_bytes_per_frame) {
        codec2_destroy(c2);
        mp_raise_ValueError(MP_ERROR_TEXT("PCM too short"));
    }

    int n_frames = buf.len / pcm_bytes_per_frame;
    size_t out_size = (size_t)n_frames * bytes_per_frame;
    uint8_t *out_buf = (uint8_t *)m_malloc(out_size);

    const short *pcm = (const short *)buf.buf;
    for (int i = 0; i < n_frames; i++) {
        codec2_encode(c2, out_buf + i * bytes_per_frame,
                      (short *)(pcm + i * samples_per_frame));
    }

    codec2_destroy(c2);

    mp_obj_t result = mp_obj_new_bytes(out_buf, out_size);
    m_free(out_buf);
    return result;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_encode_obj, 1, 2, mod_encode);

/* info(codec2_bytes [, mode]) -> (n_frames, duration_ms, bytes_per_frame) */
static mp_obj_t mod_info(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t buf;
    mp_get_buffer_raise(args[0], &buf, MP_BUFFER_READ);

    int mode = CODEC2_MODE_3200;
    if (n_args >= 2) {
        mode = mp_obj_get_int(args[1]);
    }
    /* Mode 3200: 8 bytes/frame, Mode 2400: 6 bytes/frame */
    int bytes_per_frame = (mode == CODEC2_MODE_3200) ? 8 : 6;
    int n_frames = buf.len / bytes_per_frame;
    int duration_ms = n_frames * 20;

    mp_obj_t tuple[3] = {
        mp_obj_new_int(n_frames),
        mp_obj_new_int(duration_ms),
        mp_obj_new_int(bytes_per_frame),
    };
    return mp_obj_new_tuple(3, tuple);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_info_obj, 1, 2, mod_info);

extern int _lsp_calls, _lsp_fails, _lsp_last_roots;

/* debug_encode(pcm_320bytes) -> (calls, fails, last_roots, ...) */
static mp_obj_t mod_debug_encode(mp_obj_t pcm_obj) {
    (void)pcm_obj;
    /* Return debug counters */
    _lsp_calls = 0; _lsp_fails = 0;

    /* Encode the input to trigger lsp analysis */
    mp_buffer_info_t buf;
    mp_get_buffer_raise(pcm_obj, &buf, MP_BUFFER_READ);
    struct CODEC2 *c2 = codec2_create(CODEC2_MODE_3200);
    if (c2) {
        int samples_per_frame = codec2_samples_per_frame(c2);
        int bits_per_frame = codec2_bits_per_frame(c2);
        int bytes_per_frame = (bits_per_frame + 7) / 8;
        int pcm_bytes_per_frame = samples_per_frame * 2;
        int n_frames = buf.len / pcm_bytes_per_frame;
        uint8_t *out = (uint8_t *)m_malloc(n_frames * bytes_per_frame);
        const short *pcm = (const short *)buf.buf;
        for (int i = 0; i < n_frames; i++) {
            codec2_encode(c2, out + i * bytes_per_frame,
                          (short *)(pcm + i * samples_per_frame));
        }
        m_free(out);
        codec2_destroy(c2);
    }

    mp_obj_t tuple[3];
    tuple[0] = mp_obj_new_int(_lsp_calls);
    tuple[1] = mp_obj_new_int(_lsp_fails);
    tuple[2] = mp_obj_new_int(_lsp_last_roots);
    return mp_obj_new_tuple(3, tuple);
}
#if 0  /* original debug_encode disabled */
static mp_obj_t mod_debug_encode_DISABLED(mp_obj_t pcm_obj) {
    mp_buffer_info_t buf;
    mp_get_buffer_raise(pcm_obj, &buf, MP_BUFFER_READ);

    struct CODEC2 *c2 = codec2_create(CODEC2_MODE_3200);
    if (!c2) mp_raise_ValueError(MP_ERROR_TEXT("create fail"));

    short *speech = (short *)buf.buf;
    MODEL model;
    analyse_one_frame(c2, &model, speech);
    analyse_one_frame(c2, &model, &speech[c2->n_samp]);

    /* Manual LPC analysis (same as speech_to_uq_lsps internals) */
    int m_pitch = c2->m_pitch;
    float *Wn = (float *)m_malloc(m_pitch * sizeof(float));
    float R[LPC_ORD + 1];
    float ak[LPC_ORD + 1];
    float lsps[LPC_ORD];

    float e = 0.0;
    for (int i = 0; i < m_pitch; i++) {
        Wn[i] = c2->Sn[i] * c2->w[i];
        e += Wn[i] * Wn[i];
    }

    autocorrelate(Wn, R, m_pitch, LPC_ORD);
    levinson_durbin(R, ak, LPC_ORD);

    /* BW expansion (same as speech_to_uq_lsps) */
    for (int i = 0; i <= LPC_ORD; i++)
        ak[i] *= powf(0.994f, (float)i);

    int roots = lpc_to_lsp(ak, LPC_ORD, lsps, 5, 0.01f);

    m_free(Wn);
    codec2_destroy(c2);

    /* Return: (e, R0, roots, ak0..ak10) */
    mp_obj_t tuple[14];
    tuple[0] = mp_obj_new_float(e);
    tuple[1] = mp_obj_new_float(R[0]);
    tuple[2] = mp_obj_new_int(roots);
    for (int i = 0; i <= LPC_ORD; i++) {
        tuple[3 + i] = mp_obj_new_float(ak[i]);
    }
    return mp_obj_new_tuple(14, tuple);
}
#endif
static MP_DEFINE_CONST_FUN_OBJ_1(mod_debug_encode_obj, mod_debug_encode);

mp_obj_t mpy_init(mp_obj_fun_bc_t *self, size_t n_args, size_t n_kw, mp_obj_t *args) {
    MP_DYNRUNTIME_INIT_ENTRY
    mp_store_global(MP_QSTR_create, MP_OBJ_FROM_PTR(&mod_create_obj));
    mp_store_global(MP_QSTR_decode_frame, MP_OBJ_FROM_PTR(&mod_decode_frame_obj));
    mp_store_global(MP_QSTR_encode_frame, MP_OBJ_FROM_PTR(&mod_encode_frame_obj));
    mp_store_global(MP_QSTR_destroy, MP_OBJ_FROM_PTR(&mod_destroy_obj));
    mp_store_global(MP_QSTR_decode, MP_OBJ_FROM_PTR(&mod_decode_obj));
    mp_store_global(MP_QSTR_encode, MP_OBJ_FROM_PTR(&mod_encode_obj));
    mp_store_global(MP_QSTR_info, MP_OBJ_FROM_PTR(&mod_info_obj));
    mp_store_global(MP_QSTR_debug_encode, MP_OBJ_FROM_PTR(&mod_debug_encode_obj));
    MP_DYNRUNTIME_INIT_EXIT
}
