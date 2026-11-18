"""
Grouping, dry/wet decision, depth-interval chaining, filenames, validation.

Photos are grouped by the tray number the user assigns, NOT by adjacency.
Adjacency looks tempting - the two shots of a tray are always next to each
other - but one reshoot (three photos of a tray) shifts every later pair and
silently puts the wrong depths on the rest of the hole. Grouping on an explicit
tray number cannot drift.
"""
import os
import csv


def suggest_trays(sources, start=1):
    """Default tray number per photo, assuming the usual two shots per tray.

    Only a starting guess for the UI - the user can correct any row, which is
    what makes reshoots and gaps survivable.
    """
    return {s: start + i // 2 for i, s in enumerate(sorted(sources))}


def group_by_tray(tray_of):
    """{source: tray} -> {tray: [sources]}, each list in filename order."""
    out = {}
    for src, tray in tray_of.items():
        if tray is None:
            continue
        out.setdefault(tray, []).append(src)
    for k in out:
        out[k].sort()
    return out


def decide_conditions(sources, core_L, overrides=None):
    """Assign Dry/Wet within one tray by whole-photo brightness: darker is wet.

    Used by `decide_hole` for trays that do not have exactly two photos. With
    more than two, the darkest is wet and the rest are dry; with one, it is
    dry unless overridden.

    overrides: {source: "Dry"|"Wet"} set by the user, always wins.
    Returns ({source: condition}, margin_L).
    """
    overrides = overrides or {}
    srcs = sorted(sources)
    if not srcs:
        return {}, 0.0
    lums = [core_L.get(s, 0.0) for s in srcs]
    margin = (max(lums) - min(lums)) if len(srcs) > 1 else 0.0
    wet = min(srcs, key=lambda s: core_L.get(s, 0.0)) if len(srcs) > 1 else None
    out = {}
    for s in srcs:
        out[s] = overrides.get(s) or ("Wet" if s == wet else "Dry")
    return out, margin


STRONG_PAIR = 8.0   # a pair_score this far from zero decides a tray on its own
DRY_WET_MARGIN = 6.0


def shooting_order(scores):
    """+1 if this hole was shot dry-first, -1 if wet-first, 0 if no clear habit.

    Measured only on trays whose two shots differ clearly, and only called a
    habit when three in four of those agree. Every one of 114 trays across
    DD_ZOP_014/015 and DD26ZOP-021/026/027 was shot dry first.
    """
    strong = [s for s in scores.values() if s is not None and abs(s) >= STRONG_PAIR]
    if len(strong) < 3:
        return 0
    pos = sum(1 for s in strong if s > 0)
    if pos >= 0.75 * len(strong):
        return 1
    if len(strong) - pos >= 0.75 * len(strong):
        return -1
    return 0


def decide_hole(groups, pair_scores, core_L, overrides=None):
    """Dry/Wet for every photo of one hole.

    groups:      {tray: [sources]}
    pair_scores: {tray: score} for two-photo trays; > 0 means the first source
                 in filename order is the brighter, i.e. dry, shot
    core_L:      {source: median L} for trays without exactly two photos
    overrides:   {source: "Dry"|"Wet"} set by the user; always wins

    Returns ({source: condition}, {tray: dict(confidence, basis, score)}).

    A clear brightness difference decides a tray. A near-tie does not - it
    follows the hole's own shooting order (see `shooting_order`) and is marked
    "check", so it is never presented as certain. A clear decision that goes
    AGAINST the hole's order is kept, because crews do sometimes shoot wet
    first (DD26ZOP-006 tray 25), but it is marked "check" too. On 114 verified
    trays this gets 113 right and flags the one it gets wrong.
    """
    overrides = overrides or {}
    order = shooting_order(pair_scores)
    conds, info = {}, {}
    for tray, srcs in groups.items():
        srcs = sorted(srcs)
        given = {s: overrides[s] for s in srcs if s in overrides}
        score = pair_scores.get(tray) if len(srcs) == 2 else None
        if len(srcs) == 2 and len(given) == 1:
            # one shot set by hand: the other is the other condition
            s, c = next(iter(given.items()))
            other = srcs[1] if s == srcs[0] else srcs[0]
            given[other] = "Wet" if c == "Dry" else "Dry"
        if len(given) == len(srcs):
            for s in srcs:
                conds[s] = given[s]
            info[tray] = dict(confidence="high", basis="set by hand", score=score)
            continue
        if score is None:
            c, margin = decide_conditions(srcs, core_L, given)
            conds.update(c)
            info[tray] = dict(confidence="high" if margin >= DRY_WET_MARGIN else "check",
                              basis="brightness" if len(srcs) > 1 else "single photo",
                              score=margin)
            continue
        if abs(score) >= STRONG_PAIR:
            first_dry = score > 0
            agrees = order == 0 or (order > 0) == first_dry
            conf = "high" if agrees else "check"
            basis = "brightness" if agrees else "brightness, against the hole's shooting order"
        elif order != 0:
            first_dry, conf, basis = order > 0, "check", "shooting order (too close to call)"
        else:
            first_dry, conf, basis = score >= 0, "check", "brightness (too close to call)"
        conds[srcs[0]] = "Dry" if first_dry else "Wet"
        conds[srcs[1]] = "Wet" if first_dry else "Dry"
        info[tray] = dict(confidence=conf, basis=basis, score=score)
    return conds, info


def chain_depths(trays, ends, first_start):
    """trays: sorted tray numbers. ends: {tray: end_depth}.
    Returns {tray: (start, end)}, each tray starting where the last one ended.

    A tray with no end depth POISONS every tray below it: its own end is
    unknown, so the next tray's start is unknown too, and `start` comes back
    None all the way down. The previous version quietly carried the last known
    end forward instead, so leaving Tray 2 blank produced a file called
    `..._Tray3_4.95-10.20m.jpg` when Tray 3 really began at 7.65 - a wrong
    start depth, baked into a filename, with nothing on screen to say so. That
    is the one error a geologist cannot catch by looking at the output.
    """
    out, prev = {}, first_start
    for t in sorted(trays):
        end = ends.get(t)
        out[t] = (prev, end)
        prev = end if end is not None else None
    return out


def make_name(hole, condition, tray, start_m, end_m, suffix="", ext=".jpg"):
    return (f"{hole}_{condition}_Tray{tray}_{start_m:.2f}-{end_m:.2f}m"
            f"{suffix}{ext}")


def disambiguate(names):
    """Two photos of a tray with the same condition would collide. Suffix them
    _2, _3 ... rather than silently overwriting."""
    seen, out = {}, []
    for n in names:
        if n in seen:
            seen[n] += 1
            stem, ext = os.path.splitext(n)
            out.append(f"{stem}_{seen[n]}{ext}")
        else:
            seen[n] = 1
            out.append(n)
    return out


def blocking_problems(hole, groups, ends, first_start):
    """The subset of problems that must NOT be overridable.

    Everything else in `validate` is advice - a gap in tray numbers may be
    genuine, an odd photo count may be a deliberate reshoot - and the user is
    right to be able to press on. These three are different: they put a wrong
    number into a filename, or write a file with no hole name at all, and
    neither is visible in the output afterwards.
    """
    out = []
    if not hole or any(c in hole for c in '\\/:*?"<>|'):
        out.append("Hole ID is empty or contains characters that cannot be used "
                   "in a filename.")
    if first_start is None:
        out.append("Start depth of the first tray is missing, so no tray has a "
                   "known start.")
    trays = sorted(groups)
    chained = chain_depths(trays, ends, first_start)
    missing = [t for t in trays if ends.get(t) is None]
    if missing:
        after = [t for t in trays if chained[t][0] is None and t not in missing]
        msg = ("End depth missing for tray(s) " +
               ", ".join(str(t) for t in missing) + ".")
        if after:
            msg += (" That also leaves tray(s) " + ", ".join(str(t) for t in after) +
                    " with an unknown start depth.")
        out.append(msg + " Fill them in - these cannot be skipped.")
    for t in trays:
        start, end = chained[t]
        if start is not None and end is not None and end <= start:
            out.append(f"Tray {t}: end depth {end:.2f} m is not deeper than its "
                       f"start depth {start:.2f} m.")
    return out


def validate(hole, groups, ends, first_start):
    """Problems in plain language, empty list means good to go."""
    problems = []
    if not hole or any(c in hole for c in '\\/:*?"<>|'):
        problems.append("Hole ID is empty or contains characters that cannot be "
                        "used in a filename.")
    if not groups:
        problems.append("No trays to process.")
        return problems

    trays = sorted(groups)
    gaps = [b for a, b in zip(trays, trays[1:]) if b != a + 1]
    if gaps:
        problems.append(f"Tray numbers skip before {gaps}. Fine if those trays are "
                        f"genuinely missing, but worth a look.")
    if first_start is None:
        problems.append("Start depth of the first tray is missing.")

    chained = chain_depths(trays, ends, first_start if first_start is not None else 0.0)
    for t in trays:
        start, end = chained[t]
        n = len(groups[t])
        if n != 2:
            problems.append(f"Tray {t}: {n} photo(s). Expected 2 (one dry, one wet) - "
                            f"check the tray numbers on those rows.")
        if end is None:
            problems.append(f"Tray {t}: end depth is missing.")
        elif start is None:
            problems.append(f"Tray {t}: start depth is unknown, because an earlier "
                            f"tray has no end depth.")
        elif end <= start:
            problems.append(f"Tray {t}: end depth {end:.2f} m is not deeper than its "
                            f"start depth {start:.2f} m.")
        elif end - start > 12:
            problems.append(f"Tray {t}: interval is {end - start:.2f} m, which is "
                            f"unusually long - check the depth.")
    return problems


def write_manifest(path, rows):
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
