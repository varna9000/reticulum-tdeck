#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "c2_codec2.h"

void *codec2_malloc(size_t s) { return malloc(s); }
void *codec2_calloc(size_t n, size_t s) { return calloc(n, s); }
void codec2_free(void *p) { free(p); }

int main(int argc, char *argv[]) {
    if (argc < 3) { fprintf(stderr, "Usage: %s input.c2 output.raw\n", argv[0]); return 1; }
    FILE *fin = fopen(argv[1], "rb");
    FILE *fout = fopen(argv[2], "wb");
    if (!fin || !fout) { perror("fopen"); return 1; }

    struct CODEC2 *c2 = codec2_create(CODEC2_MODE_3200);
    int spf = codec2_samples_per_frame(c2);
    int bpf = (codec2_bits_per_frame(c2) + 7) / 8;
    short *speech = malloc(spf * sizeof(short));
    unsigned char *bits = malloc(bpf);

    int frames = 0;
    while (fread(bits, 1, bpf, fin) == bpf) {
        codec2_decode(c2, speech, bits);
        fwrite(speech, sizeof(short), spf, fout);
        frames++;
    }
    printf("Decoded %d frames (%d samples)\n", frames, frames * spf);
    codec2_destroy(c2);
    fclose(fin); fclose(fout);
    return 0;
}
