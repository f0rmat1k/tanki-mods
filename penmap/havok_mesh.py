"""Extract per-armor-group triangle meshes from the game's collision .havok files."""
import struct

import numpy as np

import hktag


class Mesh:
    def __init__(self, name, tris):
        self.name = name          # e.g. 'armor_3'
        self.tris = tris          # (N, 3, 3) float32, model space


def _u32(d, o):
    return struct.unpack_from('<I', d, o)[0]


def _cstr(tf, item_index):
    if not item_index:
        return None
    it = tf.items[item_index]
    raw = tf.d[tf.data_off + it.offset: tf.data_off + it.offset + it.count]
    return raw.split(b'\0')[0].decode('utf-8', 'replace')


def _array(tf, item_index):
    """Return (absolute byte offset, count) of an array item."""
    if not item_index:
        return 0, 0
    it = tf.items[item_index]
    return tf.data_off + it.offset, it.count


def _decode_compressed_mesh(tf, shape_off):
    d = tf.d
    data_item = _u32(d, shape_off + 28)
    base, _ = _array(tf, data_item)
    tree = base  # meshTree is at offset 0 of hknpCompressedMeshShapeData
    dmin = np.frombuffer(d, '<f4', 3, tree + 16)
    dmax = np.frombuffer(d, '<f4', 3, tree + 32)

    sec_off, sec_n = _array(tf, _u32(d, tree + 64))
    prim_off, prim_n = _array(tf, _u32(d, tree + 68))
    svi_off, svi_n = _array(tf, _u32(d, tree + 72))
    pv_off, pv_n = _array(tf, _u32(d, tree + 76))
    sv_off, sv_n = _array(tf, _u32(d, tree + 80))

    prims = np.frombuffer(d, np.uint8, prim_n * 4, prim_off).reshape(-1, 4).tolist()
    svi = np.frombuffer(d, '<u2', svi_n, svi_off).astype(np.int64)
    pv = np.frombuffer(d, '<u4', pv_n, pv_off).astype(np.int64)
    sv = np.frombuffer(d, '<u8', sv_n, sv_off)

    # shared vertices: 21/21/22 bits over the whole tree domain
    sx = (sv & 0x1FFFFF).astype(np.float64)
    sy = ((sv >> np.uint64(21)) & np.uint64(0x1FFFFF)).astype(np.float64)
    sz = (sv >> np.uint64(42)).astype(np.float64)
    ext = (dmax - dmin).astype(np.float64)
    shared = np.stack([dmin[0] + sx * ext[0] / 0x1FFFFF,
                       dmin[1] + sy * ext[1] / 0x1FFFFF,
                       dmin[2] + sz * ext[2] / 0x3FFFFF], 1)

    tris = []
    for s in range(sec_n):
        so = sec_off + s * 96
        parms = np.frombuffer(d, '<f4', 6, so + 48).astype(np.float64)
        first_pv, first_sv, first_prim = struct.unpack_from('<III', d, so + 72)
        n_pv, n_prim = d[so + 88], d[so + 89]
        p = pv[first_pv:first_pv + n_pv]
        packed = np.stack([parms[0] + (p & 0x7FF) * parms[3],
                           parms[1] + ((p >> 11) & 0x7FF) * parms[4],
                           parms[2] + (p >> 22) * parms[5]], 1)

        def vert(i):
            if i < n_pv:
                return packed[i]
            return shared[svi[first_sv + i - n_pv]]

        n_sv = max(0, min(len(svi) - first_sv, len(shared)))
        for a, b, c, e in prims[first_prim:first_prim + n_prim]:
            if max(a, b, c, e) >= n_pv + n_sv:
                continue  # 0xDEAD filler primitives
            va, vb, vc = vert(a), vert(b), vert(c)
            tris.append((va, vb, vc))
            if c != e:
                tris.append((va, vc, vert(e)))
    return np.array(tris, np.float32).reshape(-1, 3, 3)


def load(data):
    """Return list of Mesh from a .havok collision file."""
    tf = hktag.TagFile(data)
    meshes = []
    for idx, it in enumerate(tf.items):
        if it.type is None or it.type.name != 'hknpPhysicsSystemData::ExtendedBodyCinfo':
            continue
        for k in range(it.count):
            bo = tf.data_off + it.offset + k * it.type.byte_size
            shape_item = tf.items[_u32(tf.d, bo + 0)]
            name = _cstr(tf, _u32(tf.d, bo + 20))
            if shape_item.type is None or shape_item.type.name != 'hknpCompressedMeshShape':
                continue
            pos = np.frombuffer(tf.d, '<f4', 3, bo + 48)
            rot = np.frombuffer(tf.d, '<f4', 4, bo + 64)
            tris = _decode_compressed_mesh(tf, tf.data_off + shape_item.offset)
            tris = _transform(tris, pos, rot)
            meshes.append(Mesh(name, tris))
        break  # first system is "Collision Physics Data"; the rest are physics/trigger proxies
    return meshes


def _transform(tris, pos, q):
    x, y, z, w = q
    if abs(w - 1) < 1e-6 and not np.any(pos):
        return tris
    r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]], np.float32)
    return (tris @ r.T + pos).astype(np.float32)
