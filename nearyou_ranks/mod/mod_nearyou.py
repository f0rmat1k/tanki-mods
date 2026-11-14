# -*- coding: utf-8 -*-
"""Near_You tier list in the hangar: rank and difficulty on the tank carousel, '#' search filter.

Data comes from nearyou.team/setups (the Near_You team's site, the same ranks as their tier-list
sheet). It is downloaded once per game start with BigWorld.fetchURL; the last good copy is kept in
mods/configs/nearyou/ratings.json, and the snapshot packed into the mod is used until a download
succeeds.

Without XVM the rank and difficulty go in front of the carousel label
(carousel_data_provider._getVehicleDataVO). XVM draws its own cells: there the mod gives the Python
macro {{py:nearyou.label('{{v.sysname}}')}} for extraFields and writes it into XVM's carousel
config at start (nearyou_xvm), and the label stays as is once that config uses it. The search box goes through
BasicCriteriesGroup._setVehicleNameCriteria, where a text starting with '#' becomes a rank /
difficulty filter (nearyou_data.parse_query).

Tech tree: the nation tree is a Gameface view that gets only vehicle ids from Python and draws the
names itself, so the rank goes into the vehicle tooltip instead (HeaderBlockConstructor, the header
of every vehicle tooltip: tech tree, carousel, shop). The research page of one vehicle is Scaleform:
the names under its nodes come from dumpers.ResearchBaseDumper._getItemData as 'nameString' and go
into a plain-text field (ResearchItem.nameField.text), so they get the rank without colors; its
title (Research._getRootData, htmlText) gets it in color.
"""
import datetime
import json
import os
import sys
import traceback
import weakref

import BigWorld
import ResMgr

from gui.mods import nearyou_data
from gui.mods import nearyou_xvm

__version__ = '0.4.0'

CONFIG_DIR = os.path.join('mods', 'configs', 'nearyou')
DEFAULTS = {
    'enabled': True,
    'show_rank': True,
    'show_difficulty': True,
    'download': True,
    # rank and difficulty in the carousel label: 'auto' = unless an XVM carousel config uses
    # the nearyou macros, true / false = always / never
    'label': 'auto',
    # add the rank to XVM's carousel config (res_mods/configs/xvm) when it is not there yet
    'xvm_config': True,
    'url': nearyou_data.SOURCE_URL,
    # a "Near_You: rank A, difficulty medium" line in vehicle tooltips (the tech tree among them)
    'tooltip': True,
    # rank on the research page of a vehicle: its title and the names of the vehicles around it
    'research': True,
}
SNAPSHOT = 'mods/nearyou/ratings.json'
FETCH_TIMEOUT = 20.0

RANK_TEXT = {'s+': 'SSS', 's': 'S', 'a': 'A', 'b': 'B', 'c': 'C', 'd': 'D', 'f': 'F'}
RANK_COLOR = {'s+': '#FF9A1F', 's': '#FFD24A', 'a': '#A6E05A', 'b': '#64C8FF', 'c': '#C8C8C8',
              'd': '#A89A86', 'f': '#E85A4F'}
DIFFICULTY_TEXT = {'easy': u'лег.', 'medium': u'сред.', 'hard': u'слож.', 'hardcore': u'хард'}
DIFFICULTY_COLOR = {'easy': '#7CD67C', 'medium': '#E8D060', 'hard': '#F09040', 'hardcore': '#E04848'}
DIFFICULTY_WORD = {'easy': u'низкая', 'medium': u'средняя', 'hard': u'высокая', 'hardcore': u'хардкор'}
# the game's text_styles.main, the style of the role line in the tooltip header
TOOLTIP_STYLE = u"<font face='$FieldFont' size='14' color='#A39D8C'>%s</font>"
TOOLTIP_TEXT_OFFSET = 101   # HeaderBlockConstructor's leftOffset: text right of the class icon


def log(*args):
    print '[nearyou]', ' '.join(str(a) for a in args)


def _game_root():
    cwd = os.getcwd()
    for d in (cwd, os.path.dirname(cwd)):
        if os.path.isdir(os.path.join(d, 'mods')):
            return d
    return cwd


def xvm_carousel_loaded():
    """XVM's carousel package is imported by its loader only when XVM starts without errors."""
    return 'xvm_tankcarousel' in sys.modules


def _searchable(word):
    from gui.shared.utils import makeSearchableString
    return makeSearchableString(word)


def _font(text, color):
    return u"<font color='%s'>%s</font>" % (color, text)


def _decorate(label, prefix, suffix):
    """Put prefix / suffix inside the label's outer <font> so they keep its face and size."""
    if not prefix and not suffix:
        return label
    as_bytes = isinstance(label, bytes)
    text = label.decode('utf-8', 'replace') if as_bytes else label
    start = text.find(u'>') + 1 if text.startswith(u'<font') else 0
    end = text.rfind(u'</font>') if start else -1
    if end < start:
        end = len(text)
    out = text[:start] + prefix + text[start:end] + suffix + text[end:]
    return out.encode('utf-8') if as_bytes else out


class NearYou(object):
    def __init__(self):
        self.root = _game_root()
        self.dir = os.path.join(self.root, CONFIG_DIR)
        self.cfg = self._load_config()
        self.ratings, self.fetched = {}, ''
        self.providers = weakref.WeakSet() if hasattr(weakref, 'WeakSet') else set()
        self.by_name = None      # vehicle type name ('ussr:R19_IS-3') -> intCD, for XVM macros
        # decided before the XVM config is touched: XVM may have read its config already, and
        # then its new field shows only from the next start, so the label covers this session
        self.use_label = self._label_wanted()
        if self.cfg['xvm_config']:
            self._patch_xvm()
        self._load_ratings()
        log('v%s loaded: %d vehicles from %s' % (__version__, len(self.ratings), self.fetched))

    # ---- data ----

    def _load_config(self):
        cfg = dict(DEFAULTS)
        path = os.path.join(self.dir, 'nearyou.json')
        try:
            if os.path.isfile(path):
                with open(path) as f:
                    cfg.update(json.load(f))
            else:
                if not os.path.isdir(self.dir):
                    os.makedirs(self.dir)
                with open(path, 'w') as f:
                    json.dump(DEFAULTS, f, indent=2, sort_keys=True)
        except Exception:
            log('config error', traceback.format_exc())
        return cfg

    def _load_ratings(self):
        """The last download if there is one, else the snapshot packed into the mod."""
        cache = os.path.join(self.dir, 'ratings.json')
        try:
            if os.path.isfile(cache):
                with open(cache, 'rb') as f:
                    self.ratings, self.fetched = nearyou_data.loads(f.read())
                return
        except Exception:
            log('bad cache, using the packed snapshot', traceback.format_exc())
        sec = ResMgr.openSection(SNAPSHOT)
        if sec is not None:
            try:
                self.ratings, self.fetched = nearyou_data.loads(sec.asBinary)
                self.fetched += ' (snapshot)'
            except Exception:
                log('bad snapshot', traceback.format_exc())

    def download(self):
        if not self.cfg['download']:
            return
        try:
            BigWorld.fetchURL(str(self.cfg['url']), self._on_downloaded, None, FETCH_TIMEOUT, 'GET', '')
        except Exception:
            log('download failed to start', traceback.format_exc())

    def _on_downloaded(self, response):
        try:
            code = getattr(response, 'responseCode', None)
            if code != 200:
                log('download failed: HTTP %s' % code)
                return
            ratings = nearyou_data.parse_setups(response.body)
        except Exception:
            log('download not parsed, keeping %s' % self.fetched, traceback.format_exc())
            return
        changed = ratings != self.ratings
        self.ratings, self.fetched = ratings, datetime.date.today().isoformat()
        try:
            if not os.path.isdir(self.dir):
                os.makedirs(self.dir)
            tmp = os.path.join(self.dir, 'ratings.json.tmp')
            with open(tmp, 'wb') as f:
                f.write(nearyou_data.dumps(ratings, self.fetched, str(self.cfg['url'])))
            path = os.path.join(self.dir, 'ratings.json')
            if os.path.exists(path):
                os.remove(path)
            os.rename(tmp, path)
        except Exception:
            log('cache not saved', traceback.format_exc())
        log('downloaded %d vehicles%s' % (len(ratings), '' if changed else ', no changes'))
        if changed:
            self.refresh_carousels()

    # ---- carousel ----

    def refresh_carousels(self):
        for dp in list(self.providers):
            try:
                if getattr(dp, 'flashObject', None) is not None:
                    dp.updateVehicles()
            except Exception:
                log('carousel refresh failed', traceback.format_exc())

    def _patch_xvm(self):
        try:
            changed = nearyou_xvm.patch_config(os.path.join(self.root, 'res_mods', 'configs', 'xvm'),
                                               log)
        except Exception:
            log('XVM config not changed', traceback.format_exc())
            return
        for path in changed:
            log('rank added to the XVM config %s (original: %s%s)' % (
                path, os.path.basename(path), nearyou_xvm.BACKUP_SUFFIX))

    def _label_wanted(self):
        mode = self.cfg['label']
        if mode in (True, False):
            return mode
        xvm_dir = os.path.join(self.root, 'res_mods', 'configs', 'xvm')
        try:
            for dirpath, _, files in os.walk(xvm_dir):
                for f in files:
                    if f.startswith('carousel') and f.endswith('.xc'):
                        with open(os.path.join(dirpath, f)) as fh:
                            if 'nearyou.' in fh.read():
                                log('XVM carousel config uses the nearyou macros: label left as is')
                                return False
        except Exception:
            log('XVM config check failed', traceback.format_exc())
        return True

    def badge(self, intCD, sep=u' '):
        """'SSS лег.' in their colors for intCD, or u'' when it has no rating."""
        if not self.cfg['enabled']:
            return u''
        rating, difficulty = self.ratings.get(intCD, ('', ''))
        parts = []
        if self.cfg['show_rank'] and rating:
            parts.append(_font(RANK_TEXT[rating], RANK_COLOR[rating]))
        if self.cfg['show_difficulty'] and difficulty:
            parts.append(_font(DIFFICULTY_TEXT[difficulty], DIFFICULTY_COLOR[difficulty]))
        return sep.join(parts)

    def plain_badge(self, intCD):
        """'A сред.' without colors, for plain-text fields."""
        if not self.cfg['enabled']:
            return u''
        rating, difficulty = self.ratings.get(intCD, ('', ''))
        parts = []
        if self.cfg['show_rank'] and rating:
            parts.append(RANK_TEXT[rating])
        if self.cfg['show_difficulty'] and difficulty:
            parts.append(DIFFICULTY_TEXT[difficulty])
        return u' '.join(parts)

    def tooltip_line(self, intCD):
        """'Near_You: ранг A, сложность средняя' (html) for the vehicle tooltip, or u''."""
        if not (self.cfg['enabled'] and self.cfg['tooltip']):
            return u''
        rating, difficulty = self.ratings.get(intCD, ('', ''))
        parts = []
        if self.cfg['show_rank'] and rating:
            parts.append(u'ранг ' + _font(RANK_TEXT[rating], RANK_COLOR[rating]))
        if self.cfg['show_difficulty'] and difficulty:
            parts.append(u'сложность ' + _font(DIFFICULTY_WORD[difficulty], DIFFICULTY_COLOR[difficulty]))
        if not parts:
            return u''
        return TOOLTIP_STYLE % (u'Near_You: ' + u', '.join(parts))

    def decorate_tooltip_header(self, vehicle, block):
        """Add the tooltip line under the header's name / role lines (HeaderBlockConstructor)."""
        line = self.tooltip_line(getattr(vehicle, 'intCD', None))
        if not line or not block:
            return block
        from gui.shared.tooltips import formatters
        lines = block[0]['data']['blocksData']
        # the same paddings as the game's role and "preferential" lines
        top = -9 if len(lines) == 1 else -11
        lines.append(formatters.packTextBlockData(line.encode('utf-8'), padding=formatters.packPadding(
            top=top, left=TOOLTIP_TEXT_OFFSET, bottom=9)))
        return block

    def decorate_research_node(self, node, data):
        """Plain 'A сред.' in front of a vehicle's name on the research page."""
        if not self.cfg['research'] or not node.isVehicle() or 'nameString' not in data:
            return data
        badge = self.plain_badge(data.get('id'))
        if badge:
            data['nameString'] = _decorate(data['nameString'], badge + u' ', u'')
        return data

    def decorate_research_root(self, result):
        """Colored rank after the title of the research page."""
        title = result.get('vehicleTitle') if self.cfg['research'] else None
        if not title:
            return result
        badge = self.badge(title.get('intCD'))
        if badge:
            for key in ('tankNameStr', 'tankNameStrSmall'):
                if key in title:
                    title[key] = _decorate(title[key], u'', u'  ' + badge)
        return result

    def decorate(self, vo):
        """Rank and difficulty in front of the name: after it a long name would cut them off.

        With the macros in XVM's config the label is left to XVM, but only while XVM's carousel
        really runs: XVM can fail to load (a missing native DLL takes all of it down)."""
        if not self.use_label and xvm_carousel_loaded():
            return vo
        badge = self.badge(vo.get('intCD'))
        if badge and 'label' in vo:
            vo['label'] = _decorate(vo['label'], badge + u' ', u'')
        return vo

    def intCD_by_name(self, name):
        """intCD for 'ussr:R19_IS-3', 'ussr-R19_IS-3' or 'R19_IS-3' (XVM's v.sysname)."""
        if self.by_name is None:
            from items import vehicles
            import nations
            self.by_name = {}
            for nation_id in range(len(nations.NAMES)):
                for inner_id, item in vehicles.g_list.getList(nation_id).iteritems():
                    cd = vehicles.makeIntCompactDescrByID('vehicle', nation_id, inner_id)
                    self.by_name[item.name] = cd
                    self.by_name.setdefault(item.name.split(':', 1)[-1], cd)
        if isinstance(name, bytes):
            name = name.decode('utf-8', 'replace')
        name = (name or u'').strip()
        return self.by_name.get(name) or self.by_name.get(name.replace(u'-', u':', 1))

    def search_predicate(self, text):
        """Criteria predicate for a '#' search text, or None to leave the search to the game."""
        if not self.cfg['enabled']:
            return None
        query = nearyou_data.parse_query(text)
        if query is None:
            return None
        ranks, difficulties, words = query
        # name words are compared the game's way, with its searchableUserName
        words = [_searchable(w) for w in words]
        query = ranks, difficulties, words
        ratings = self.ratings

        def predicate(vehicle):
            rating, difficulty = ratings.get(vehicle.intCD, ('', ''))
            name = getattr(vehicle, 'searchableUserName', u'') or u''
            return nearyou_data.matches(query, rating, difficulty, name)

        return predicate


g_nearyou = None


# ---- XVM python macros: {{py:nearyou.label('{{v.sysname}}')}} and friends ----

_unknown_names = set()


def xvm_label(sysname, sep=u' '):
    if g_nearyou is None:
        return u''
    intCD = g_nearyou.intCD_by_name(sysname)
    if intCD is None and len(_unknown_names) < 5 and sysname not in _unknown_names:
        # a few lines in python.log show what XVM passes, should v.sysname change
        _unknown_names.add(sysname)
        log('XVM macro: unknown vehicle %r' % (sysname,))
    return g_nearyou.badge(intCD, sep)


def xvm_rank(sysname):
    if g_nearyou is None:
        return u''
    rating, _ = g_nearyou.ratings.get(g_nearyou.intCD_by_name(sysname), ('', ''))
    return RANK_TEXT.get(rating, u'')


def xvm_difficulty(sysname):
    if g_nearyou is None:
        return u''
    _, difficulty = g_nearyou.ratings.get(g_nearyou.intCD_by_name(sysname), ('', ''))
    return DIFFICULTY_TEXT.get(difficulty, u'')


XVM_MACROS = (('nearyou.label', xvm_label), ('nearyou.rank', xvm_rank),
              ('nearyou.difficulty', xvm_difficulty))


def _register_xvm_macros():
    """Into XVM's py_macro registry; again after each XVM reload, which empties it."""
    try:
        import xvm_pymacro
    except ImportError:
        return False

    def register():
        for name, func in XVM_MACROS:
            # not deterministic: the ratings can change after the download
            xvm_pymacro._container[name] = (func, False)
            xvm_pymacro._ignored.discard(name)

    register()
    reload_name = '__reload'
    orig = getattr(xvm_pymacro, reload_name, None)
    if orig is not None:
        def reload_and_register(*args, **kwargs):
            orig(*args, **kwargs)
            register()
        setattr(xvm_pymacro, reload_name, reload_and_register)
    process = getattr(xvm_pymacro, 'process', None)
    if process is not None:
        def process_registered(codestring, *args, **kwargs):
            if codestring.startswith('nearyou.') and 'nearyou.label' not in xvm_pymacro._container:
                register()
            return process(codestring, *args, **kwargs)
        xvm_pymacro.process = process_registered
    log('XVM macros registered')
    return True


def _override(owner, name, wrapper):
    orig = getattr(owner, name)

    def call(*args, **kwargs):
        return wrapper(orig, *args, **kwargs)

    setattr(owner, name, call)


def _vehicle_vo(orig, *args, **kwargs):
    vo = orig(*args, **kwargs)
    try:
        g_nearyou.decorate(vo)
    except Exception:
        log('label failed', traceback.format_exc())
    return vo


def _name_criteria(orig, self, filters):
    from gui.Scaleform.daapi.view.common.vehicle_carousel.carousel_filter import FILTER_KEYS
    from gui.shared.utils.requesters import REQ_CRITERIA
    try:
        predicate = g_nearyou.search_predicate(filters.get(FILTER_KEYS.SEARCH_NAME_VEHICLE))
    except Exception:
        log('search failed', traceback.format_exc())
        predicate = None
    if predicate is None:
        return orig(self, filters)
    self._criteria |= REQ_CRITERIA.CUSTOM(predicate)


def _tooltip_header(orig, self, *args, **kwargs):
    block = orig(self, *args, **kwargs)
    try:
        g_nearyou.decorate_tooltip_header(self.vehicle, block)
    except Exception:
        log('tooltip failed', traceback.format_exc())
    return block


def _research_node(orig, self, node, rootItem):
    data = orig(self, node, rootItem)
    try:
        g_nearyou.decorate_research_node(node, data)
    except Exception:
        log('research node failed', traceback.format_exc())
    return data


def _research_root(orig, self, *args, **kwargs):
    result = orig(self, *args, **kwargs)
    try:
        g_nearyou.decorate_research_root(result)
    except Exception:
        log('research title failed', traceback.format_exc())
    return result


def _patch_tech_tree():
    """Each part on its own: a game update that moves one of them leaves the others working."""
    try:
        from gui.shared.tooltips import vehicle as vehicle_tooltips
        _override(vehicle_tooltips.HeaderBlockConstructor, 'construct', _tooltip_header)
    except Exception:
        log('vehicle tooltip not patched', traceback.format_exc())
    try:
        from gui.techtree import dumpers
        # ResearchItemsObjDumper reaches it through super(), looked up at call time
        _override(dumpers.ResearchBaseDumper, '_getItemData', _research_node)
    except Exception:
        log('research nodes not patched', traceback.format_exc())
    try:
        from gui.Scaleform.daapi.view.lobby.techtree import research_page
        _override(research_page.Research, '_getRootData', _research_root)
    except Exception:
        log('research title not patched', traceback.format_exc())


def _provider_init(orig, self, *args, **kwargs):
    orig(self, *args, **kwargs)
    try:
        g_nearyou.providers.add(self)
    except Exception:
        log('carousel not tracked', traceback.format_exc())


def init():
    global g_nearyou
    try:
        g_nearyou = NearYou()
        from gui.Scaleform.daapi.view.common.vehicle_carousel import carousel_data_provider as cdp
        from gui.Scaleform.daapi.view.common.vehicle_carousel import carousel_filter
        # getVehicleDataVO (imported by other carousels) looks _getVehicleDataVO up at call time
        _override(cdp, '_getVehicleDataVO', _vehicle_vo)
        _override(cdp.CarouselDataProvider, '__init__', _provider_init)
        _override(carousel_filter.BasicCriteriesGroup, '_setVehicleNameCriteria', _name_criteria)
    except Exception:
        log('init failed', traceback.format_exc())
        return
    _patch_tech_tree()
    try:
        _register_xvm_macros()
    except Exception:
        log('XVM macros not registered', traceback.format_exc())
    g_nearyou.download()


def fini():
    pass
