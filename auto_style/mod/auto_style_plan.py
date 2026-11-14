# -*- coding: utf-8 -*-
"""What the auto-style mod should do next; no game imports, so it runs in tests (Python 2.7 and 3).

A vehicle here is anything with .intCD, .isLocked, .isInInventory and .getOutfitComponent(season),
which returns a CustomizationOutfit-like object (styleId, camouflages, paints) or None.
"""

# the game's SeasonType.COMMON_SEASONS: summer, winter, desert
COMMON_SEASONS = (1, 2, 4)


def components(vehicle):
    return [vehicle.getOutfitComponent(s) for s in COMMON_SEASONS]


def wears(vehicle, style_ids):
    """The vehicle has one of style_ids on."""
    return any(c is not None and c.styleId in style_ids for c in components(vehicle))


def has_own_skin(vehicle, style_ids):
    """Another style, or camouflage / paint of its own (decals and emblems do not count)."""
    for c in components(vehicle):
        if c is None or c.styleId in style_ids:
            continue
        if c.styleId or c.camouflages or c.paints:
            return True
    return False


def outfit_ids(vehicle):
    """The camouflage and paint ids the vehicle wears, to see if it has the wanted level."""
    ids = set()
    for c in components(vehicle):
        if c is not None:
            ids.update(('camo', x.id) for x in c.camouflages)
            ids.update(('paint', x.id) for x in c.paints)
    return ids


def plan(current, vehicles, style_ids, mapping=None, limit_reached=False, free_copies=1, skip=()):
    """The next step as (action, vehicle), or None.

    restore: a vehicle that is not the selected one takes its marble off (its own look comes back);
    apply: the selected vehicle gets the style, or gets it again when it wears an item that mapping
    ({('camo' | 'paint', id): id of the wanted level}) turns into another one.
    Locked vehicles (in battle, in a queue or platoon) are left until they come back. skip holds
    (action, intCD) pairs that failed: they are not tried again until the selection changes.
    """
    style_ids = set(style_ids)
    current_cd = getattr(current, 'intCD', None)
    for v in vehicles:
        if v.intCD != current_cd and not v.isLocked and wears(v, style_ids) \
                and ('restore', v.intCD) not in skip:
            return 'restore', v
    if current is None or current.isLocked or not current.isInInventory \
            or ('apply', current_cd) in skip:
        return None
    if wears(current, style_ids):
        mapping = mapping or {}
        if any(mapping.get(key, key[1]) != key[1] for key in outfit_ids(current)):
            return 'apply', current
        return None
    if limit_reached and has_own_skin(current, style_ids):
        return None
    if free_copies <= 0:
        # the style is still on a vehicle in battle: it comes off there once that one is back
        return None
    return 'apply', current


def level_mapping(groups, target_level=None):
    """{(type, id): id of the target level} for quest-progression items.

    groups: per progression chain, (all levels, unlocked levels), each level a dict
    {item type: [ids]} (empty levels are skipped). The target is target_level (1-based) when it is
    unlocked, else the highest unlocked one.
    """
    mapping = {}
    for levels, unlocked in groups:
        levels = [lv for lv in levels if lv]
        unlocked = [lv for lv in unlocked if lv]
        if not unlocked:
            continue
        target = unlocked[-1]
        if target_level and 0 < target_level <= len(levels) and levels[target_level - 1] in unlocked:
            target = levels[target_level - 1]
        for item_type, ids in target.items():
            if not ids:
                continue
            for lv in levels:
                for item_id in lv.get(item_type, ()):
                    mapping[(item_type, item_id)] = ids[0]
    return mapping


def _names_style(value, style_ids):
    """A styleId value: one id or, as the game's quest data has it, a list of them."""
    values = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
    for v in values:
        try:
            if int(v) in style_ids:
                return True
        except (TypeError, ValueError):
            pass
    return False


def find_style_ids(data, style_ids):
    """True when quest data mentions one of style_ids as a styleId (its customization condition)."""
    stack = [data]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            if 'styleId' in x and _names_style(x['styleId'], style_ids):
                return True
            stack.extend(x.items())
        elif isinstance(x, (list, tuple)):
            if len(x) == 2 and x[0] == 'styleId' and _names_style(x[1], style_ids):
                return True
            stack.extend(x)
    return False
