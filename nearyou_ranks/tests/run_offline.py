# -*- coding: utf-8 -*-
"""Python 2.7 smoke test of mod_nearyou with stubbed game modules.

Run: C:\\Python27\\python.exe tests\\run_offline.py [saved nearyou.team/setups page]
"""
import io
import json
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.dirname(HERE)
PAGE = sys.argv[1] if len(sys.argv) > 1 else None
# the game folder may have a Cyrillic name: os.getcwd() is then cp1251 bytes
TEMP = tempfile.mkdtemp(prefix='nearyou_test_')
ROOT = os.path.join(TEMP, '\xd2\xe0\xed\xea\xe8')
os.makedirs(os.path.join(ROOT, 'mods'))


def module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    parent, _, child = name.rpartition('.')
    if parent in sys.modules:
        setattr(sys.modules[parent], child, m)
    return m


class Obj(object):
    def __init__(self, **kw):
        self.__dict__.update(kw)


FETCHES = []
module('BigWorld', fetchURL=lambda url, cb, headers, timeout, method, data: FETCHES.append((url, cb)))
with io.open(os.path.join(TOOL, 'res', 'mods', 'nearyou', 'ratings.json'), 'rb') as f:
    SNAPSHOT = f.read()
module('ResMgr', openSection=lambda p: Obj(asBinary=SNAPSHOT) if p == 'mods/nearyou/ratings.json'
       else None)


def vehicle_name(text):
    # the game's text_styles.vehicleName: a <font> with face, size and color
    return "<font face='$FieldFont' size='14' color='#E9E2BF'>%s</font>" % text


def _getVehicleDataVO(vehicle, *args, **kwargs):
    return {'intCD': vehicle.intCD, 'label': vehicle_name(vehicle.userName)}


def getVehicleDataVO(vehicle):
    return cdp._getVehicleDataVO(vehicle)


class CarouselDataProvider(object):
    def __init__(self, carouselFilter, itemsCache):
        self.flashObject = object()
        self.updates = 0

    def updateVehicles(self, vehiclesCDs=None, filterCriteria=None, forceUpdate=False):
        self.updates += 1


class Criteria(object):
    def __init__(self, predicate):
        self.predicates = [predicate]

    def __or__(self, other):
        c = Criteria(None)
        c.predicates = self.predicates + other.predicates
        return c

    def __call__(self, item):
        return all(p(item) for p in self.predicates if p is not None)


def makeSearchableString(s):
    if isinstance(s, bytes):
        s = s.decode('utf-8')
    return s.lower().replace(u' ', u'')


REQ_CRITERIA = Obj(EMPTY=Criteria(None), CUSTOM=Criteria, VEHICLE=Obj(
    NAME_VEHICLE=lambda name: Criteria(lambda v: name in v.searchableUserName)))


class BasicCriteriesGroup(object):
    def __init__(self):
        self._criteria = REQ_CRITERIA.EMPTY

    def _setVehicleNameCriteria(self, filters):
        if filters['searchNameVehicle']:
            self._criteria |= REQ_CRITERIA.VEHICLE.NAME_VEHICLE(
                makeSearchableString(filters['searchNameVehicle']))


for name in ('gui', 'gui.Scaleform', 'gui.Scaleform.daapi', 'gui.Scaleform.daapi.view',
             'gui.Scaleform.daapi.view.common', 'gui.Scaleform.daapi.view.common.vehicle_carousel',
             'gui.shared'):
    module(name)
cdp = module('gui.Scaleform.daapi.view.common.vehicle_carousel.carousel_data_provider',
             _getVehicleDataVO=_getVehicleDataVO, getVehicleDataVO=getVehicleDataVO,
             CarouselDataProvider=CarouselDataProvider)
module('gui.Scaleform.daapi.view.common.vehicle_carousel.carousel_filter',
       BasicCriteriesGroup=BasicCriteriesGroup,
       FILTER_KEYS=Obj(SEARCH_NAME_VEHICLE='searchNameVehicle'))
module('gui.shared.utils', makeSearchableString=makeSearchableString)
module('gui.shared.utils.requesters', REQ_CRITERIA=REQ_CRITERIA)
sys.path.insert(0, os.path.join(TOOL, 'mod'))
import nearyou_data  # noqa: E402
import nearyou_xvm  # noqa: E402
module('gui.mods', nearyou_data=nearyou_data, nearyou_xvm=nearyou_xvm)

# XVM's py_macro registry; its reload empties it
def _xvm_reload():
    pymacro._container.clear()


def _xvm_process(codestring):
    name, args = codestring.split('(', 1)
    return pymacro._container[name][0](*eval('(' + args[:-1] + ',)'))


pymacro = module('xvm_pymacro', _container={}, _ignored=set(), process=_xvm_process)
setattr(pymacro, '__reload', _xvm_reload)
VEHICLE_NAMES = {}   # (nation id, inner id) -> name, filled once the ratings are known
module('nations', NAMES=('ussr', 'germany'))
module('items')
module('items.vehicles',
       g_list=Obj(getList=lambda nid: dict((i, Obj(name=n)) for (k, i), n in VEHICLE_NAMES.items()
                                            if k == nid)),
       makeIntCompactDescrByID=lambda kind, nid, iid: VEHICLE_CDS[(nid, iid)])
VEHICLE_CDS = {}

os.chdir(ROOT)
import mod_nearyou  # noqa: E402
mod_nearyou.init()
ny = mod_nearyou.g_nearyou
assert len(ny.ratings) > 300 and 'snapshot' in ny.fetched, (len(ny.ratings), ny.fetched)
assert len(FETCHES) == 1 and FETCHES[0][0] == 'https://nearyou.team/setups'
assert os.path.isfile(os.path.join(ROOT, 'mods', 'configs', 'nearyou', 'nearyou.json'))


def veh(intCD, name):
    return Obj(intCD=intCD, userName=name, searchableUserName=makeSearchableString(name))


IS7 = [k for k, v in ny.ratings.items() if v == ('s+', 'easy')][0]
MAUS = [k for k, v in ny.ratings.items() if v[0] == 'd' and v[1] == 'hard'][0]
is7, maus, lt = veh(IS7, u'ИС-7'.encode('utf-8')), veh(MAUS, 'Maus'), veh(1, 'MS-1')
VEHICLE_NAMES.update({(0, 1): 'ussr:R45_IS-7', (1, 1): 'germany:G42_Maus'})
VEHICLE_CDS.update({(0, 1): IS7, (1, 1): MAUS})

# labels: rank and difficulty in front of the name (a long name cuts off what follows it)
label = cdp.getVehicleDataVO(is7)['label']
print(label)
assert label == (u"<font face='$FieldFont' size='14' color='#E9E2BF'><font color='#FF9A1F'>SSS</font> "
                 u"<font color='#7CD67C'>лег.</font> ИС-7</font>").encode('utf-8'), label
assert cdp.getVehicleDataVO(lt)['label'] == vehicle_name('MS-1'), 'unrated vehicle changed'
ny.cfg['show_difficulty'] = False
assert u'лег.'.encode('utf-8') not in cdp.getVehicleDataVO(is7)['label']
ny.cfg['show_difficulty'] = True
print('labels: ok')

# XVM: macros by the vehicle's system name; registered again after XVM reloads its macros
macro = pymacro._container['nearyou.label'][0]
assert macro('ussr:R45_IS-7') == u"<font color='#FF9A1F'>SSS</font> <font color='#7CD67C'>лег.</font>"
assert macro('ussr-R45_IS-7') == macro('R45_IS-7') == macro('ussr:R45_IS-7')
assert macro('ussr:R45_IS-7', u'<br>').count(u'<br>') == 1
assert macro('') == u'' and macro('germany:Unknown') == u''
assert pymacro._container['nearyou.rank'][0]('R45_IS-7') == 'SSS'
assert pymacro._container['nearyou.difficulty'][0]('R45_IS-7') == u'лег.'
getattr(pymacro, '__reload')()
assert 'nearyou.label' in pymacro._container, 'lost after an XVM reload'
pymacro._container.clear()
assert pymacro.process("nearyou.label('R45_IS-7')").startswith(u"<font color='#FF9A1F'>SSS")
# XVM's carousel config (the real files as XVM ships them) gets the rank at start; the label
# keeps the rank in that session, the next start leaves both the config and the label alone
XVM_SRC = os.path.join(HERE, 'fixtures', 'xvm')   # XVM's own files, as it ships them
xvm_root = os.path.join(ROOT, 'res_mods', 'configs', 'xvm')
os.makedirs(os.path.join(xvm_root, 'default'))
shutil.copyfile(os.path.join(XVM_SRC, 'xvm.xc'), os.path.join(xvm_root, 'xvm.xc'))
for name in ('carousel.xc', 'carouselNormal.xc', 'carouselSmall.xc'):
    shutil.copyfile(os.path.join(XVM_SRC, 'default', name), os.path.join(xvm_root, 'default', name))
first = mod_nearyou.NearYou()
cells = [os.path.join(xvm_root, 'default', n) for n in ('carouselNormal.xc', 'carouselSmall.xc')]
for path in cells:
    with open(path, 'rb') as f:
        assert f.read().count("nearyou.label('{{v.sysname}}')") == 1, path
    assert os.path.isfile(path + '.nearyou_bak')
assert first.use_label, 'the first session must keep the rank in the label'
stamp = [os.path.getmtime(p) for p in cells]
second = mod_nearyou.NearYou()
assert not second.use_label and [os.path.getmtime(p) for p in cells] == stamp
ny.use_label = False
assert cdp.getVehicleDataVO(is7)['label'] == vehicle_name(is7.userName)
ny.use_label = True
print('XVM macros and config: ok')


# '#' search: ranks, ranges, difficulty, words; plain text stays the game's name search
def visible(text):
    g = BasicCriteriesGroup()
    g._setVehicleNameCriteria({'searchNameVehicle': text})
    return [v.userName for v in (is7, maus, lt) if g._criteria(v)]


assert visible(u'#SSS') == [is7.userName]
assert visible(u'#s+') == [is7.userName]
assert visible(u'#D') == ['Maus']
assert visible(u'#d+') == [is7.userName, 'Maus']
assert visible(u'#хард') == []
assert visible(u'#слож') == ['Maus']
assert visible(u'#лёгкий') == [is7.userName]
assert visible(u'#a+ слож') == []
assert visible(u'№ D maus') == ['Maus']
assert visible(u'#S ис') == [] and visible(u'#SSS ис') == [is7.userName]
assert visible(u'maus') == ['Maus'], 'plain search must stay the game one'
assert visible(u'#') == [is7.userName, 'Maus', 'MS-1']
print('search: ok')

# download: parsed, cached, carousels refreshed; a broken page keeps the data
dp = CarouselDataProvider(None, None)
assert dp in ny.providers
if PAGE:
    with open(PAGE, 'rb') as f:
        body = f.read()
    ny.ratings = {}
    FETCHES[0][1](Obj(responseCode=200, body=body))
    assert len(ny.ratings) > 300 and 'snapshot' not in ny.fetched and dp.updates == 1
    cache = os.path.join(ROOT, 'mods', 'configs', 'nearyou', 'ratings.json')
    assert os.path.isfile(cache)
    before = dict(ny.ratings)
    FETCHES[0][1](Obj(responseCode=200, body='<html>maintenance</html>'))
    FETCHES[0][1](Obj(responseCode=503, body=''))
    assert ny.ratings == before and dp.updates == 1
    # the next start reads the cache, not the snapshot
    again = mod_nearyou.NearYou()
    assert again.ratings == before and 'snapshot' not in again.fetched
    print('download and cache: ok')
os.chdir(HERE)
shutil.rmtree(TEMP)
print('ok')
