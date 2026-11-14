"""Download nearyou.team/setups and write the ratings snapshot packed into the mod.

Usage: python make_snapshot.py [saved_page.html]
Writes res/mods/nearyou/ratings.json; the mod uses it until its own download at game start succeeds.
"""
import datetime
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'mod'))
import nearyou_data  # noqa: E402

OUT = os.path.join(HERE, 'res', 'mods', 'nearyou', 'ratings.json')


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1], 'rb') as f:
            html = f.read()
    else:
        req = urllib.request.Request(nearyou_data.SOURCE_URL, headers={'User-Agent': 'Mozilla/5.0'})
        html = urllib.request.urlopen(req, timeout=30).read()
    ratings = nearyou_data.parse_setups(html)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(nearyou_data.dumps(ratings, datetime.date.today().isoformat()))
    ranked = sum(1 for r, _ in ratings.values() if r)
    print('%s: %d vehicles, %d ranked, %d bytes' % (OUT, len(ratings), ranked, os.path.getsize(OUT)))


if __name__ == '__main__':
    main()
