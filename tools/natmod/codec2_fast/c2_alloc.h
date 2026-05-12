/* debug_alloc.h
 *
 * Some macros which can report on malloc results.
 *
 * Enable with "-D DEBUG_ALLOC"
 */

#ifndef DEBUG_ALLOC_H
#define DEBUG_ALLOC_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif
extern void *codec2_malloc(size_t size);
extern void *codec2_calloc(size_t nmemb, size_t size);
extern void codec2_free(void *ptr);
#ifdef __cplusplus
}
#endif

#define MALLOC(size) codec2_malloc(size)
#define CALLOC(nmemb, size) codec2_calloc(nmemb, size)
#define FREE(ptr) codec2_free(ptr)

#endif  // DEBUG_ALLOC_H
