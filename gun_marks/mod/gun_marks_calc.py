# -*- coding: utf-8 -*-
"""Marks of excellence arithmetic (Python 2.7 and 3).

The server keeps, per vehicle, an exponential moving average of the combined damage of a battle
(movingAvgDamage) and the share of players whose average is lower (damageRating, the mark %).
After a battle: avg += (combined - avg) * ALPHA. The averages behind 65 / 85 / 95 % are not in the
client, so the slope "% per point of average" is estimated from this player's own history of
(average, %) pairs, one per battle; a single battle moves the average little, so a straight line
through the nearest points is a fair local estimate.
"""

ALPHA = 2.0 / 101       # EMA over 100 battles
MARKS = (65.0, 85.0, 95.0)
HISTORY_LEN = 30
MIN_POINTS = 2
MIN_SPREAD = 1.0        # points of average the history must span for a slope


def next_mark(percent):
    """The next mark threshold above percent, or None when all three are reached."""
    for m in MARKS:
        if percent < m:
            return m
    return None


def add_point(history, avg, percent):
    """history (list of [avg, percent]) with this battle's values appended, no repeats."""
    point = [int(avg), round(float(percent), 2)]
    if history and history[-1] == point:
        return history
    return (history + [point])[-HISTORY_LEN:]


def slope(history, avg, near=8):
    """% per point of average around avg, from the nearest history points; None if unknown."""
    pts = sorted(history, key=lambda p: abs(p[0] - avg))[:near]
    if len(pts) < MIN_POINTS:
        return None
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    if max(xs) - min(xs) < MIN_SPREAD:
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    k = sxy / sxx
    return k if k > 0 else None


def damage_for(avg, percent, target, k):
    """Combined damage in one battle that brings the mark % to target, by the slope k."""
    avg_needed = avg + (target - percent) / k
    return avg + (avg_needed - avg) / ALPHA


def estimate(avg, percent, history):
    """dict for the battle panel: keep (damage that keeps the average), target (next mark) and need,
    step (the next whole percent) and step_need; the needs are None until the history has a slope."""
    step = min(100.0, float(int(percent) + 1))
    out = {'avg': avg, 'percent': percent, 'keep': avg, 'target': next_mark(percent),
           'need': None, 'step': step, 'step_need': None, 'battles_short': 0}
    k = slope(history, avg)
    if k is None:
        out['battles_short'] = max(1, MIN_POINTS - len(history))
        return out
    out['step_need'] = max(0.0, damage_for(avg, percent, step, k))
    if out['target'] is not None:
        out['need'] = max(0.0, damage_for(avg, percent, out['target'], k))
    return out


def projected_percent(avg, percent, history, combined):
    """The mark % after a battle with this combined damage, by the history slope; None if unknown."""
    k = slope(history, avg)
    if k is None:
        return None
    new_avg = avg + (combined - avg) * ALPHA
    return min(100.0, max(0.0, percent + (new_avg - avg) * k))
