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
    """Assign Dry/Wet within one tray. The darker core is wet.

    Shooting order is NOT used - photographers sometimes shoot wet first
    (DD26ZOP-006 tray 25 did). With more than two photos, the darkest is wet and
    the rest are dry; with one, it is dry unless overridden.

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


def chain_depths(trays, ends, first_start):
    """trays: sorted tray numbers. ends: {tray: end_depth}.
    Returns {tray: (start, end)} with each tray starting where the last ended."""
    out, prev = {}, first_start
    for t in sorted(trays):
        end = ends.get(t)
        out[t] = (prev, end)
        if end is not None:
            prev = end
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
