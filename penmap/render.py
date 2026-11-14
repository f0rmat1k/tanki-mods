"""Render a frontal penetration map of a vehicle for a given shell."""
import math

import numpy as np
from PIL import Image

SHELLS = {
    # normalization deg, ricochet deg
    'AP': (5.0, 70.0),
    'APCR': (2.0, 70.0),
    'HEAT': (0.0, 85.0),
}
PEN_SPREAD = 0.25          # +-25% penetration roll
HEAT_LOSS_PER_M = 0.5      # HEAT loses 5% per 10 cm after spaced armor

K_ARMOR, K_SPACED, K_GUN = 0, 1, 2


def collect_triangles(parts):
    """Flatten parts into world-space triangles with armor attributes."""
    tris, thick, kind, group = [], [], [], []
    labels = []
    for p in parts:
        for m in p.meshes:
            g = m.name[2:] if m.name.startswith('s_') else m.name
            if g == 'gun':
                t, k = p.armor.get('gun', (0, False))[0], K_GUN
            elif g in p.armor:
                t, spaced = p.armor[g]
                if t <= 0 and spaced:
                    continue
                # 0 mm main armor is a virtual plate closing openings (behind mantlets):
                # reaching it means damage
                k = K_SPACED if spaced else K_ARMOR
            else:
                continue  # modules (surveying device etc.) do not stop shells
            labels.append('%s.%s' % (p.kind, g))
            tris.append(m.tris + p.offset)
            n = len(m.tris)
            thick.append(np.full(n, t, np.float32))
            kind.append(np.full(n, k, np.int8))
            group.append(np.full(n, len(labels) - 1, np.int16))
    return (np.concatenate(tris), np.concatenate(thick), np.concatenate(kind),
            np.concatenate(group), labels)


def frame_for(tris, res):
    """Image frame (umin, vmin, w, h) covering the triangles in the front view."""
    u, v = -tris[..., 0], -tris[..., 1]
    umin, vmin = u.min() - 2 * res, v.min() - 2 * res
    w = int(math.ceil((u.max() - umin) / res)) + 3
    h = int(math.ceil((v.max() - vmin) / res)) + 3
    return float(umin), float(vmin), w, h


def rasterize(tris, res, frame=None):
    """Orthographic front view (looking along -z). Returns per-hit arrays and image size."""
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
    front = n[:, 2] > 1e-4
    # image coords: u grows with -x (viewer's right), v grows downward
    u = -tris[..., 0]
    v = -tris[..., 1]
    umin, vmin, w, h = frame or frame_for(tris, res)
    px = (u - umin) / res
    py = (v - vmin) / res

    hit_pix, hit_z, hit_tri = [], [], []
    for i in np.nonzero(front)[0]:
        x, y, z = px[i], py[i], tris[i, :, 2]
        x0, x1 = int(max(0, math.floor(x.min()))), int(min(w - 1, math.ceil(x.max())))
        y0, y1 = int(max(0, math.floor(y.min()))), int(min(h - 1, math.ceil(y.max())))
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        d = (y[1] - y[2]) * (x[0] - x[2]) + (x[2] - x[1]) * (y[0] - y[2])
        if abs(d) < 1e-12:
            continue
        a = ((y[1] - y[2]) * (gx - x[2]) + (x[2] - x[1]) * (gy - y[2])) / d
        b = ((y[2] - y[0]) * (gx - x[2]) + (x[0] - x[2]) * (gy - y[2])) / d
        c = 1 - a - b
        inside = (a >= 0) & (b >= 0) & (c >= 0)
        if not inside.any():
            continue
        zz = a * z[0] + b * z[1] + c * z[2]
        pix = (gy[inside] - 0.5).astype(np.int64) * w + (gx[inside] - 0.5).astype(np.int64)
        hit_pix.append(pix)
        hit_z.append(zz[inside])
        hit_tri.append(np.full(pix.size, i, np.int64))
    return (np.concatenate(hit_pix), np.concatenate(hit_z), np.concatenate(hit_tri),
            n, (w, h))


def simulate(pix, z, tri, normals, thick, kind, shell, pen, caliber, npix):
    """Per pixel: probability to penetrate (NaN = no armor hit), plus id of first hit triangle."""
    norm_deg, ric_deg = SHELLS[shell]
    order = np.lexsort((-z, pix))
    pix, z, tri = pix[order], z[order], tri[order]
    starts = np.r_[0, np.nonzero(np.diff(pix))[0] + 1]
    counts = np.diff(np.r_[starts, pix.size])
    upix = pix[starts]

    m = upix.size
    required = np.zeros(m)
    loss = np.zeros(m)
    spaced_z = np.full(m, np.nan)
    state = np.zeros(m, np.int8)   # 0 flying, 1 hit main armor, 2 ricochet, 3 gun barrel
    for k in range(counts.max()):
        act = (state == 0) & (counts > k)
        if not act.any():
            break
        idx = starts[act] + k
        ti = tri[idx]
        t = thick[ti].astype(np.float64)
        cos = np.clip(normals[ti, 2], 1e-6, 1.0)
        ang = np.degrees(np.arccos(cos))
        overmatch3 = caliber > 3 * t
        ric = (ang > ric_deg) & ~overmatch3
        nd = np.full(t.shape, norm_deg)
        if shell != 'HEAT':
            boost = (caliber > 2 * t) & (t > 0)
            nd[boost] *= 1.4 * caliber / (2 * t[boost])
        teff = t / np.cos(np.radians(np.clip(ang - nd, 0, 89.9)))
        kd = kind[ti]
        sel = np.nonzero(act)[0]
        if shell == 'HEAT':
            started = ~np.isnan(spaced_z[sel])
            loss[sel[started]] = np.minimum(
                1.0, HEAT_LOSS_PER_M * (spaced_z[sel[started]] - z[idx][started]))
        required[sel] += teff
        st = np.where(kd == K_GUN, 3, np.where(ric, 2, np.where(kd == K_ARMOR, 1, 0)))
        if shell == 'HEAT':
            first = (kd == K_SPACED) & np.isnan(spaced_z[sel])
            spaced_z[sel[first]] = z[idx][first]
        state[sel] = st

    p_eff = pen * (1 - loss)
    lo, hi = p_eff * (1 - PEN_SPREAD), p_eff * (1 + PEN_SPREAD)
    prob = np.clip((hi - required) / (hi - lo), 0, 1)
    prob[state == 2] = 0
    prob[(state == 0) | (state == 3)] = np.nan   # spaced armor only / gun barrel: no damage

    out_prob = np.full(npix, np.nan)
    out_prob[upix] = prob
    first_tri = np.full(npix, -1, np.int64)
    first_tri[upix] = tri[starts]
    return out_prob, first_tri


def colorize(prob, first_tri, group, normals, size):
    w, h = size
    img = np.zeros((h * w, 4), np.float64)
    hit = first_tri >= 0
    red, yellow, green = np.array([214, 48, 39]), np.array([250, 205, 40]), np.array([60, 190, 70])
    gray = np.array([95, 95, 100])
    p = prob[hit]
    col = np.where(np.isnan(p)[:, None], gray,
                   np.where((p < 0.5)[:, None],
                            red + (yellow - red) * (np.nan_to_num(p)[:, None] * 2),
                            yellow + (green - yellow) * ((np.nan_to_num(p)[:, None] - 0.5) * 2)))
    # light shading so plate shapes stay readable
    nz = normals[first_tri[hit]]
    light = 0.72 + 0.28 * np.clip(nz @ np.array([0.3, 0.5, 0.81]), 0, 1)
    img[hit, :3] = col * light[:, None]
    img[hit, 3] = 255
    img = img.reshape(h, w, 4)
    # outlines where the first-hit armor group changes
    g = np.where(hit, group[np.maximum(first_tri, 0)], -1).reshape(h, w)
    edge = np.zeros((h, w), bool)
    edge[:, 1:] |= g[:, 1:] != g[:, :-1]
    edge[1:, :] |= g[1:, :] != g[:-1, :]
    edge &= (g >= 0)
    img[edge, :3] *= 0.35
    img[edge, 3] = 255
    return Image.fromarray(img.astype(np.uint8), 'RGBA')


def render(parts, shell='AP', pen=200, caliber=100, res=0.008):
    tris, thick, kind, group, labels = collect_triangles(parts)
    pix, z, tri, normals, size = rasterize(tris, res)
    prob, first_tri = simulate(pix, z, tri, normals, thick, kind, shell, pen, caliber,
                                  size[0] * size[1])
    return colorize(prob, first_tri, group, normals, size), (tris, thick, kind, group, labels)
