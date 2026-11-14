# -*- coding: utf-8 -*-
"""Near_You ratings: parse nearyou.team/setups, store and query them (Python 2.7 and 3).

The page is rendered on the server by Nuxt: <script id="__NUXT_DATA__"> holds a devalue payload, a
flat JSON array where objects and lists point to other entries by index. data.vehicles lists every
vehicle with intCD (the game's compact descriptor), rating ('s+', 's', 'a', ... or '') and
difficulty ('easy', 'medium', 'hard', 'hardcore' or '').
"""
import json
import re

SOURCE_URL = 'https://nearyou.team/setups'
MIN_VEHICLES = 300      # fewer parsed vehicles means the page changed: keep the old data

RANKS = ('s+', 's', 'a', 'b', 'c', 'd', 'f')    # best first
DIFFICULTIES = ('easy', 'medium', 'hard', 'hardcore')

_NUXT_RE = re.compile(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S)
_WRAPPERS = ('ShallowReactive', 'Reactive', 'Ref', 'ShallowRef', 'EmptyRef')


def _resolve(payload, index, memo):
    """Expand payload[index]; devalue wrappers like ['ShallowReactive', i] are unwrapped."""
    if not isinstance(index, int) or index < 0:
        return None
    if index in memo:
        return memo[index]
    v = payload[index]
    if isinstance(v, list):
        if v and isinstance(v[0], (str, type(u''))):
            out = _resolve(payload, v[1], memo) if v[0] in _WRAPPERS and len(v) > 1 else None
        else:
            out = [_resolve(payload, i, memo) for i in v]
    elif isinstance(v, dict):
        out = dict((k, _resolve(payload, i, memo)) for k, i in v.items())
    else:
        out = v
    memo[index] = out
    return out


def parse_setups(html):
    """{intCD: (rating, difficulty)} from the /setups page; raises ValueError if it is not there."""
    if isinstance(html, bytes):
        html = html.decode('utf-8', 'replace')
    m = _NUXT_RE.search(html)
    if m is None:
        raise ValueError('no __NUXT_DATA__ on the page')
    payload = json.loads(m.group(1))
    root = _resolve(payload, 0, {})
    try:
        vehicles = root['data']['vehicles']
    except (TypeError, KeyError):
        raise ValueError('no data.vehicles in the payload')
    out = {}
    for v in vehicles or ():
        if not isinstance(v, dict) or not isinstance(v.get('intCD'), int):
            continue
        rating = (v.get('rating') or '').lower()
        difficulty = (v.get('difficulty') or '').lower()
        if rating in RANKS or difficulty in DIFFICULTIES:
            out[v['intCD']] = (rating if rating in RANKS else '',
                               difficulty if difficulty in DIFFICULTIES else '')
    if len(out) < MIN_VEHICLES:
        raise ValueError('only %d rated vehicles on the page' % len(out))
    return out


def dumps(ratings, fetched, source=SOURCE_URL):
    data = {'fetched': fetched, 'source': source,
            'vehicles': dict((str(k), list(v)) for k, v in ratings.items())}
    return json.dumps(data, sort_keys=True, separators=(',', ':'))


def loads(text):
    """(ratings, fetched) from dumps() output."""
    if isinstance(text, bytes):
        text = text.decode('utf-8')
    data = json.loads(text)
    ratings = dict((int(k), (v[0], v[1])) for k, v in data['vehicles'].items())
    return ratings, data.get('fetched', '')


# ---- '#' queries in the carousel search box ----

MARKERS = (u'#', u'№', u'*')
_CYRILLIC_RANKS = {u'а': u'a', u'в': u'b', u'с': u'c'}
_DIFFICULTY_WORDS = (
    # (prefix, difficulty): the first matching prefix wins, so longer ones go first
    (u'хардкор', 'hardcore'), (u'hardcore', 'hardcore'), (u'хард', 'hardcore'),
    (u'лёг', 'easy'), (u'лег', 'easy'), (u'easy', 'easy'),
    (u'сред', 'medium'), (u'med', 'medium'),
    (u'слож', 'hard'), (u'hard', 'hard'),
)


def _rank_token(token):
    """Set of ranks a token asks for: 's', 'sss' / 's+' (SSS), 'a+' (A and better), or None."""
    better = token.endswith(u'+') and token not in (u's+',)
    core = token[:-1] if better else token
    if core in (u'sss', u's+'):
        return set(['s+'])
    core = _CYRILLIC_RANKS.get(core, core)
    if core in RANKS:
        if better:
            return set(RANKS[:RANKS.index(core) + 1])
        return set([core])
    return None


def _difficulty_token(token):
    for prefix, difficulty in _DIFFICULTY_WORDS:
        if token.startswith(prefix):
            return difficulty
    return None


def parse_query(text):
    """(ranks, difficulties, words) for a search text starting with a marker, else None.

    Ranks and difficulties are sets (empty = any); words must all be in the vehicle's name.
    """
    if not text:
        return None
    if isinstance(text, bytes):
        text = text.decode('utf-8', 'replace')
    text = text.strip().lower()
    if not text or text[0] not in MARKERS:
        return None
    ranks, difficulties, words = set(), set(), []
    for token in re.split(u'[\\s,;#№*]+', text):
        if not token:
            continue
        rank = _rank_token(token)
        if rank is not None:
            ranks |= rank
            continue
        difficulty = _difficulty_token(token)
        if difficulty is not None:
            difficulties.add(difficulty)
        else:
            words.append(token)
    return ranks, difficulties, words


def matches(query, rating, difficulty, name):
    ranks, difficulties, words = query
    if ranks and rating not in ranks:
        return False
    if difficulties and difficulty not in difficulties:
        return False
    return all(w in name for w in words)
