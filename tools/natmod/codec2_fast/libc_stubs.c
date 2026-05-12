/* libc stubs for Codec2 natmod — no standard library available.
   Must NOT include compat.h to avoid macro conflicts. */

typedef unsigned char uint8_t;
typedef unsigned int size_t;

#undef memcpy
#undef memset
#undef memcmp

void *memcpy(void *dst, const void *src, size_t n) {
    uint8_t *d = (uint8_t *)dst;
    const uint8_t *s = (const uint8_t *)src;
    while (n--) *d++ = *s++;
    return dst;
}

void *memset(void *s, int c, size_t n) {
    uint8_t *p = (uint8_t *)s;
    while (n--) *p++ = (uint8_t)c;
    return s;
}

int memcmp(const void *a, const void *b, size_t n) {
    const uint8_t *pa = (const uint8_t *)a;
    const uint8_t *pb = (const uint8_t *)b;
    while (n--) {
        if (*pa != *pb) return *pa - *pb;
        pa++; pb++;
    }
    return 0;
}

void *memmove(void *dst, const void *src, size_t n) {
    uint8_t *d = (uint8_t *)dst;
    const uint8_t *s = (const uint8_t *)src;
    if (d < s) {
        while (n--) *d++ = *s++;
    } else {
        d += n; s += n;
        while (n--) *--d = *--s;
    }
    return dst;
}

int abs(int x) { return x < 0 ? -x : x; }

/* libm fdlib_version stub — avoids pulling in s_lib_ver.o which has .data */
int __fdlib_version;

/* stdio/stdlib stubs — codec2 debug paths and libm error handling */
typedef void FILE;
int fprintf(FILE *f, const char *fmt, ...) { (void)f; (void)fmt; return 0; }
FILE *fopen(const char *path, const char *mode) { (void)path; (void)mode; return (void *)0; }
void exit(int status) { (void)status; for(;;); }

/* newlib internals needed by libm */
static int _errno_val;
int *__errno(void) { return &_errno_val; }

/* _reent struct must be large enough — libm accesses multiple fields */
static char _reent_buf[1024];  /* oversized to cover all possible fields */
void *__getreent(void) { return _reent_buf; }
