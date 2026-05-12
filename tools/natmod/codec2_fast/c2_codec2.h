/*---------------------------------------------------------------------------*\

  FILE........: codec2.h
  AUTHOR......: David Rowe
  DATE CREATED: 21 August 2010

  Codec 2 fully quantised encoder and decoder functions.  If you want use
  Codec 2, these are the functions you need to call.

\*---------------------------------------------------------------------------*/

/*
  Copyright (C) 2010 David Rowe

  All rights reserved.

  This program is free software; you can redistribute it and/or modify
  it under the terms of the GNU Lesser General Public License version 2.1, as
  published by the Free Software Foundation.  This program is
  distributed in the hope that it will be useful, but WITHOUT ANY
  WARRANTY; without even the implied warranty of MERCHANTABILITY or
  FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public
  License for more details.

  You should have received a copy of the GNU Lesser General Public License
  along with this program; if not, see <http://www.gnu.org/licenses/>.
*/

#ifndef __CODEC2__
#define __CODEC2__
#include "c2_version.h"
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

#define CODEC2_MODE_3200 0
#define CODEC2_MODE_3200_EN 1
#define CODEC2_MODE_2400 1
#define CODEC2_MODE_2400_EN 1

// header
#define c2_magic {0xc0, 0xde, 0xc2}
struct c2_header {
    char magic[3];
    char version_major;
    char version_minor;
    char mode;
    char flags;
};

#define CODEC2_MODE_ACTIVE(mode_name, var) \
  ((mode_name##_EN) == 0 ? 0 : (var) == mode_name)

struct CODEC2;

struct CODEC2 *codec2_create(int mode);
void codec2_destroy(struct CODEC2 *codec2_state);
void codec2_encode(struct CODEC2 *codec2_state, unsigned char bytes[],
                   short speech_in[]);
void codec2_decode(struct CODEC2 *codec2_state, short speech_out[],
                   const unsigned char bytes[]);
void codec2_decode_ber(struct CODEC2 *codec2_state, short speech_out[],
                       const unsigned char *bytes, float ber_est);
int codec2_samples_per_frame(struct CODEC2 *codec2_state);
int codec2_bits_per_frame(struct CODEC2 *codec2_state);
int codec2_bytes_per_frame(struct CODEC2 *codec2_state);

void codec2_set_lpc_post_filter(struct CODEC2 *codec2_state, int enable,
                                int bass_boost, float beta, float gamma);
int codec2_get_spare_bit_index(struct CODEC2 *codec2_state);
void codec2_set_natural_or_gray(struct CODEC2 *codec2_state, int gray);
void codec2_set_softdec(struct CODEC2 *c2, float *softdec);
float codec2_get_energy(struct CODEC2 *codec2_state, const unsigned char *bits);

// support for ML and VQ experiments
void codec2_open_mlfeat(struct CODEC2 *codec2_state, char *feat_filename,
                        char *model_filename);
void codec2_load_codebook(struct CODEC2 *codec2_state, int num, char *filename);
float codec2_get_var(struct CODEC2 *codec2_state);
float *codec2_enable_user_ratek(struct CODEC2 *codec2_state, int *K);

#ifdef __cplusplus
}
#endif

#endif
