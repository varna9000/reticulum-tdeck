#include <stdio.h>
#include <stdlib.h>
#include "c2_codec2.h"
void *codec2_malloc(size_t s) { return malloc(s); }
void *codec2_calloc(size_t n, size_t s) { return calloc(n, s); }
void codec2_free(void *p) { free(p); }
int main() {
    FILE *fin = fopen("/tmp/mic_gained.raw", "rb");
    fseek(fin, 0, SEEK_END); long sz = ftell(fin); fseek(fin, 0, SEEK_SET);
    short *pcm = malloc(sz);
    fread(pcm, 1, sz, fin); fclose(fin);
    int n_samples = sz / 2;
    struct CODEC2 *enc = codec2_create(CODEC2_MODE_3200);
    struct CODEC2 *dec = codec2_create(CODEC2_MODE_3200);
    int spf = codec2_samples_per_frame(enc);
    int bpf = (codec2_bits_per_frame(enc) + 7) / 8;
    int n_frames = n_samples / spf;
    unsigned char *bits = malloc(bpf);
    short *out = malloc(n_samples * 2);
    for (int i = 0; i < n_frames; i++) {
        codec2_encode(enc, bits, pcm + i * spf);
        codec2_decode(dec, out + i * spf, bits);
    }
    FILE *fout = fopen("/tmp/mic_roundtrip.raw", "wb");
    fwrite(out, 2, n_frames * spf, fout); fclose(fout);
    printf("Encoded+decoded %d frames\n", n_frames);
    codec2_destroy(enc); codec2_destroy(dec);
    free(pcm); free(bits); free(out);
    return 0;
}
