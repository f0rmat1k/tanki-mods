/*
 * heatmap_fast.c — Native helpers for the heatmap mod.
 *
 * Compiled to heatmap_fast.dll, loaded via ctypes from Python 2.7.
 * Provides:
 *   - grid_to_raw: codes array + palette -> raw RGBA pixel data (filter-byte + row)
 *     Python then compresses with zlib and wraps in PNG.
 *
 * Build (MinGW-w64):
 *   gcc -O2 -shared -o heatmap_fast.dll heatmap_fast.c
 */

#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif

/*
 * grid_to_raw: convert a codes grid + palette to raw PNG image data
 * (filter byte 0 + RGBA pixels per row, ready for zlib.compress).
 *
 * Parameters:
 *   codes    - array of uint8_t[gw * gh], one code per cell
 *   gw, gh   - grid dimensions
 *   palette  - array of uint8_t[256 * 4], RGBA per code
 *   out      - output buffer, caller allocates (1 + gw*4) * gh bytes
 *
 * Returns the number of bytes written.
 */
EXPORT int grid_to_raw(const uint8_t *codes, int gw, int gh,
                       const uint8_t *palette, uint8_t *out) {
    if (gw <= 0 || gh <= 0 || !codes || !palette || !out) return 0;

    uint8_t *p = out;
    for (int j = 0; j < gh; j++) {
        *p++ = 0; /* PNG filter: none */
        const uint8_t *row = codes + j * gw;
        for (int i = 0; i < gw; i++) {
            const uint8_t *c = palette + row[i] * 4;
            /* RGBA */
            *p++ = c[0];
            *p++ = c[1];
            *p++ = c[2];
            *p++ = c[3];
        }
    }
    return (int)(p - out);
}
