/*
 * test_decode.c — Host-side test for codec2 mode 3200 decode.
 *
 * Provides codec2_malloc/codec2_calloc/codec2_free using libc,
 * then creates a codec2 instance, feeds dummy frames, and reports
 * whether it survives.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "c2_codec2.h"

/* ---- Allocator implementations required by c2_alloc.h ---- */

void *codec2_malloc(size_t size)
{
    void *p = malloc(size);
    if (!p) {
        fprintf(stderr, "codec2_malloc(%zu) FAILED\n", size);
    }
    return p;
}

void *codec2_calloc(size_t nmemb, size_t size)
{
    void *p = calloc(nmemb, size);
    if (!p) {
        fprintf(stderr, "codec2_calloc(%zu, %zu) FAILED\n", nmemb, size);
    }
    return p;
}

void codec2_free(void *ptr)
{
    free(ptr);
}

/* ---- Main ---- */

int main(void)
{
    struct CODEC2 *c2;
    int samples_per_frame, bytes_per_frame;
    short *speech;
    unsigned char *bits;
    int i;

    printf("Creating codec2 mode 3200...\n");
    c2 = codec2_create(CODEC2_MODE_3200);
    if (c2 == NULL) {
        fprintf(stderr, "codec2_create returned NULL\n");
        return 1;
    }
    printf("codec2_create OK\n");

    samples_per_frame = codec2_samples_per_frame(c2);
    bytes_per_frame = codec2_bytes_per_frame(c2);
    printf("samples_per_frame = %d, bytes_per_frame = %d\n",
           samples_per_frame, bytes_per_frame);

    speech = (short *)malloc(samples_per_frame * sizeof(short));
    bits = (unsigned char *)malloc(bytes_per_frame);
    if (!speech || !bits) {
        fprintf(stderr, "malloc for buffers failed\n");
        return 1;
    }

    for (i = 0; i < 5; i++) {
        memset(bits, 0, bytes_per_frame);
        memset(speech, 0, samples_per_frame * sizeof(short));

        printf("decode frame %d ...\n", i);
        codec2_decode(c2, speech, bits);
        printf("  decode ok\n");
    }

    printf("All 5 frames decoded successfully.\n");

    free(speech);
    free(bits);
    codec2_destroy(c2);
    printf("destroy ok\n");

    return 0;
}
