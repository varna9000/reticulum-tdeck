#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include "c2_codec2.h"

void *codec2_malloc(size_t s) { return malloc(s); }
void *codec2_calloc(size_t n, size_t s) { return calloc(n, s); }
void codec2_free(void *p) { free(p); }

int main() {
    // Generate 1 second of 440Hz sine wave at 8kHz 16-bit
    int n_samples = 8000;
    short *pcm = malloc(n_samples * sizeof(short));
    for (int i = 0; i < n_samples; i++) {
        pcm[i] = (short)(16000 * sin(2.0 * 3.14159265 * 440.0 * i / 8000.0));
    }

    // Encode with mode 3200
    struct CODEC2 *c2 = codec2_create(CODEC2_MODE_3200);
    int spf = codec2_samples_per_frame(c2);  // 160
    int bpf = (codec2_bits_per_frame(c2) + 7) / 8;  // 8
    int n_frames = n_samples / spf;  // 50
    
    printf("Encoding: %d samples -> %d frames, %d bytes/frame\n", n_samples, n_frames, bpf);
    
    unsigned char *encoded = malloc(n_frames * bpf);
    for (int i = 0; i < n_frames; i++) {
        codec2_encode(c2, encoded + i * bpf, pcm + i * spf);
    }
    codec2_destroy(c2);
    printf("Encoded: %d bytes\n", n_frames * bpf);

    // Decode back
    c2 = codec2_create(CODEC2_MODE_3200);
    short *decoded = malloc(n_samples * sizeof(short));
    for (int i = 0; i < n_frames; i++) {
        codec2_decode(c2, decoded + i * spf, encoded + i * bpf);
    }
    codec2_destroy(c2);

    // Compare
    double err = 0;
    for (int i = 0; i < n_samples; i++) {
        double d = (double)(pcm[i] - decoded[i]);
        err += d * d;
    }
    double snr_db = 10.0 * log10((double)(n_samples * 16000.0 * 16000.0) / err);
    printf("Encode->Decode SNR: %.1f dB\n", snr_db);
    printf("PASS: encode/decode roundtrip works\n");

    free(pcm); free(encoded); free(decoded);
    return 0;
}
