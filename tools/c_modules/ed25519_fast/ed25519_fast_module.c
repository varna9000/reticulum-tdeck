/*
 * ed25519_fast_xtensawin — built-in user C module (Ed25519 + X25519)
 *
 * Static conversion of vendor/uP-reticulum/tools/natmod/ed25519_fast/main.c
 * (the dynruntime natmod). Same API, same import name as the .mpy it
 * replaces, so urns/crypto/ed25519.py needs no changes. Compiled into the
 * firmware the Monocypher code executes from flash (XIP) instead of being
 * committed to internal IRAM at import (~45KB recovered).
 *
 * Algorithm sources: vendored Monocypher 4.x, referenced unmodified from
 * the uP-reticulum submodule (zero libc deps by design).
 *
 * API:
 *   import ed25519_fast_xtensawin as ed
 *   sig = ed.sign(message, seed_32)
 *   ok  = ed.verify(sig_64, message, pk_32)
 *   pub = ed.publickey(seed_32)
 *   shared = ed.x25519(private_32, public_32)
 *   pub = ed.x25519_publickey(private_32)
 */

#include "py/runtime.h"
#include "py/obj.h"

#include "monocypher.h"
#include "monocypher-ed25519.h"

/* Ed25519 sign: sign(message, seed_32) -> bytes(64) */
static mp_obj_t mod_sign(mp_obj_t msg_obj, mp_obj_t sk_seed_obj) {
    mp_buffer_info_t msg_buf, sk_buf;
    mp_get_buffer_raise(msg_obj, &msg_buf, MP_BUFFER_READ);
    mp_get_buffer_raise(sk_seed_obj, &sk_buf, MP_BUFFER_READ);
    if (sk_buf.len != 32) {
        mp_raise_ValueError(MP_ERROR_TEXT("seed must be 32 bytes"));
    }

    uint8_t sk[64], pk[32], sig[64];
    uint8_t seed[32];
    for (int i = 0; i < 32; i++) seed[i] = ((uint8_t *)sk_buf.buf)[i];
    crypto_ed25519_key_pair(sk, pk, seed);
    crypto_wipe(seed, 32);
    crypto_ed25519_sign(sig, sk, msg_buf.buf, msg_buf.len);
    crypto_wipe(sk, 64);

    return mp_obj_new_bytes(sig, 64);
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_sign_obj, mod_sign);

/* Ed25519 verify: verify(sig_64, message, pk_32) -> bool */
static mp_obj_t mod_verify(mp_obj_t sig_obj, mp_obj_t msg_obj, mp_obj_t pk_obj) {
    mp_buffer_info_t sig_buf, msg_buf, pk_buf;
    mp_get_buffer_raise(sig_obj, &sig_buf, MP_BUFFER_READ);
    mp_get_buffer_raise(msg_obj, &msg_buf, MP_BUFFER_READ);
    mp_get_buffer_raise(pk_obj, &pk_buf, MP_BUFFER_READ);
    if (sig_buf.len != 64) {
        mp_raise_ValueError(MP_ERROR_TEXT("sig must be 64 bytes"));
    }
    if (pk_buf.len != 32) {
        mp_raise_ValueError(MP_ERROR_TEXT("pk must be 32 bytes"));
    }

    int r = crypto_ed25519_check(sig_buf.buf, pk_buf.buf, msg_buf.buf, msg_buf.len);
    return mp_obj_new_bool(r == 0);
}
static MP_DEFINE_CONST_FUN_OBJ_3(mod_verify_obj, mod_verify);

/* Ed25519 public key: publickey(seed_32) -> bytes(32) */
static mp_obj_t mod_publickey(mp_obj_t sk_seed_obj) {
    mp_buffer_info_t sk_buf;
    mp_get_buffer_raise(sk_seed_obj, &sk_buf, MP_BUFFER_READ);
    if (sk_buf.len != 32) {
        mp_raise_ValueError(MP_ERROR_TEXT("seed must be 32 bytes"));
    }

    uint8_t sk[64], pk[32];
    uint8_t seed[32];
    for (int i = 0; i < 32; i++) seed[i] = ((uint8_t *)sk_buf.buf)[i];
    crypto_ed25519_key_pair(sk, pk, seed);
    crypto_wipe(seed, 32);
    crypto_wipe(sk, 64);

    return mp_obj_new_bytes(pk, 32);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_publickey_obj, mod_publickey);

/* X25519 key exchange: x25519(private_32, public_32) -> bytes(32) */
static mp_obj_t mod_x25519(mp_obj_t sk_obj, mp_obj_t pk_obj) {
    mp_buffer_info_t sk_buf, pk_buf;
    mp_get_buffer_raise(sk_obj, &sk_buf, MP_BUFFER_READ);
    mp_get_buffer_raise(pk_obj, &pk_buf, MP_BUFFER_READ);
    if (sk_buf.len != 32 || pk_buf.len != 32) {
        mp_raise_ValueError(MP_ERROR_TEXT("keys must be 32 bytes"));
    }

    uint8_t shared[32];
    crypto_x25519(shared, sk_buf.buf, pk_buf.buf);

    return mp_obj_new_bytes(shared, 32);
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_x25519_obj, mod_x25519);

/* X25519 public key: x25519_publickey(private_32) -> bytes(32) */
static mp_obj_t mod_x25519_publickey(mp_obj_t sk_obj) {
    mp_buffer_info_t sk_buf;
    mp_get_buffer_raise(sk_obj, &sk_buf, MP_BUFFER_READ);
    if (sk_buf.len != 32) {
        mp_raise_ValueError(MP_ERROR_TEXT("key must be 32 bytes"));
    }

    uint8_t pk[32];
    crypto_x25519_public_key(pk, sk_buf.buf);

    return mp_obj_new_bytes(pk, 32);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_x25519_publickey_obj, mod_x25519_publickey);

static const mp_rom_map_elem_t ed25519_fast_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_ed25519_fast_xtensawin) },
    { MP_ROM_QSTR(MP_QSTR_sign), MP_ROM_PTR(&mod_sign_obj) },
    { MP_ROM_QSTR(MP_QSTR_verify), MP_ROM_PTR(&mod_verify_obj) },
    { MP_ROM_QSTR(MP_QSTR_publickey), MP_ROM_PTR(&mod_publickey_obj) },
    { MP_ROM_QSTR(MP_QSTR_x25519), MP_ROM_PTR(&mod_x25519_obj) },
    { MP_ROM_QSTR(MP_QSTR_x25519_publickey), MP_ROM_PTR(&mod_x25519_publickey_obj) },
};
static MP_DEFINE_CONST_DICT(ed25519_fast_globals, ed25519_fast_globals_table);

const mp_obj_module_t ed25519_fast_user_cmodule = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&ed25519_fast_globals,
};

MP_REGISTER_MODULE(MP_QSTR_ed25519_fast_xtensawin, ed25519_fast_user_cmodule);
