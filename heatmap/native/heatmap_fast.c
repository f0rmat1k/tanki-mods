/*
 * heatmap_fast.c - Python 2.7 C extension for heatmap mod.
 *
 * Provides grid_to_raw(): codes array + palette -> raw PNG row data.
 * This replaces the hot Python loop in _png_from_codes.
 *
 * Build (MinGW-w64, Python 2.7 x64):
 *   gcc -O2 -shared -o heatmap_fast.pyd heatmap_fast.c -IC:\Python27\include -LC:\Python27\libs -lpython27
 *
 * Usage in mod:
 *   try:
 *       from heatmap_fast import grid_to_raw
 *   except ImportError:
 *       grid_to_raw = None  # fallback to pure Python
 *
 *   raw = grid_to_raw(codes_bytes, gw, gh, palette_bytes)
 *   # returns a bytes object: (1 + gw*4) * gh bytes of raw PNG row data
 */

#include <Python.h>
#include <stdlib.h>
#include <string.h>

static PyObject *
heatmap_fast_grid_to_raw(PyObject *self, PyObject *args)
{
    const unsigned char *codes;
    int codes_len;
    int gw, gh;
    const unsigned char *palette;
    int pal_len;

    if (!PyArg_ParseTuple(args, "s#iis#",
                          &codes, &codes_len,
                          &gw, &gh,
                          &palette, &pal_len))
        return NULL;

    if (gw <= 0 || gh <= 0) {
        PyErr_SetString(PyExc_ValueError, "gw and gh must be positive");
        return NULL;
    }
    if (codes_len < gw * gh) {
        PyErr_SetString(PyExc_ValueError, "codes buffer too small");
        return NULL;
    }
    if (pal_len < 256 * 4) {
        PyErr_SetString(PyExc_ValueError, "palette must be 256*4 bytes");
        return NULL;
    }

    int row_bytes = 1 + gw * 4;
    int out_size = row_bytes * gh;
    unsigned char *out = (unsigned char *)malloc(out_size);
    if (!out)
        return PyErr_NoMemory();

    unsigned char *p = out;
    for (int j = 0; j < gh; j++) {
        *p++ = 0; /* PNG filter: none */
        const unsigned char *row = codes + j * gw;
        for (int i = 0; i < gw; i++) {
            const unsigned char *c = palette + row[i] * 4;
            *p++ = c[0];
            *p++ = c[1];
            *p++ = c[2];
            *p++ = c[3];
        }
    }

    PyObject *result = PyString_FromStringAndSize((const char *)out, out_size);
    free(out);
    return result;
}

static PyMethodDef HeatmapFastMethods[] = {
    {"grid_to_raw", heatmap_fast_grid_to_raw, METH_VARARGS,
     "grid_to_raw(codes, gw, gh, palette) -> raw PNG row bytes\n\n"
     "codes: bytes of gw*gh uint8 codes\n"
     "gw, gh: grid dimensions\n"
     "palette: bytes of 256*4 RGBA entries\n"
     "Returns raw PNG data (filter byte + RGBA pixels per row)."},
    {NULL, NULL, 0, NULL}
};

PyMODINIT_FUNC
initheatmap_fast(void)
{
    Py_InitModule3("heatmap_fast", HeatmapFastMethods,
                   "Native acceleration for heatmap mod PNG generation.");
}
