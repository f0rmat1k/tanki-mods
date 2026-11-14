"""Render a frontal penetration map.

Usage: python make_map.py ussr R19_IS-3 --shell AP --pen 225 --caliber 122 [--res 0.006]
"""
import argparse
import os

import render
import vehicle


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('nation')
    ap.add_argument('vehicle')
    ap.add_argument('--shell', default='AP', choices=sorted(render.SHELLS))
    ap.add_argument('--pen', type=float, required=True)
    ap.add_argument('--caliber', type=float, required=True)
    ap.add_argument('--res', type=float, default=0.008, help='meters per pixel')
    ap.add_argument('--out', default='out')
    a = ap.parse_args()

    parts, info = vehicle.load(a.nation, a.vehicle)
    img, _ = render.render(parts, a.shell, a.pen, a.caliber, a.res)
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, '%s_%s_%d.png' % (a.vehicle, a.shell, a.pen))
    img.save(path, optimize=True)
    print('%s (%s, %s) -> %s %dx%d, %d bytes' % (a.vehicle, info['turret'], info['gun'], path,
                                                img.width, img.height, os.path.getsize(path)))


if __name__ == '__main__':
    main()
