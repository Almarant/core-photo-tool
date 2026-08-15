"""
Pairing, dry/wet decision, depth-interval chaining, filenames, validation.

Kept separate from the GUI so it can be tested and reused.
"""
import os
import csv
import re


def pair_photos(sources):
    """Adjacent photos of the same tray, in filename order.

    Each tray is photographed twice (dry and wet), and the two shots are always
    adjacent in capture order. Pairing on adjacency is reliable; deciding WHICH
    is wet from order is not - see decide_wet.
    """
    s = sorted(sources)
    return [tuple(s[i:i + 2]) for i in range(0, len(s), 2)]


def decide_wet(pair, core_L):
    """Within a pair, the darker core is wet.

    Do NOT use shooting order: photographers sometimes shoot wet first (this
    happened on DD26ZOP-006 tray 25). Returns (wet_source, margin).
    """
    if len(pair) != 2:
        return None, 0.0
    a, b = pair
    La, Lb = core_L.get(a, 0.0), core_L.get(b, 0.0)
    return (a if La < Lb else b), abs(La - Lb)


def make_name(hole, condition, tray, start_m, end_m, ext=".jpg"):
    return f"{hole}_{condition}_Tray{tray}_{start_m:.2f}-{end_m:.2f}m{ext}"


def chain_depths(rows, first_start):
    """rows: list of dicts with 'tray' and 'end_m', in tray order.
    Returns the same list with 'start_m' filled from the previous tray's end."""
    out = []
    prev = first_start
    for r in sorted(rows, key=lambda x: x["tray"]):
        r = dict(r)
        r["start_m"] = prev
        prev = r["end_m"]
        out.append(r)
    return out


def validate(hole, rows, first_start, n_photos_by_tray):
    """Human-readable problems, worst first. Empty list means good to go.

    The point of this function: tray numbers and depths are highly constrained,
    so typos are catchable before anything is written to disk.
    """
    problems = []
    if not hole or not re.match(r"^[A-Za-z0-9][A-Za-z0-9._\-]*$", hole):
        problems.append("Hole ID is empty or contains characters that are not "
                        "safe in a filename.")
    if not rows:
        problems.append("No trays to process.")
        return problems

    trays = [r["tray"] for r in rows]
    if len(set(trays)) != len(trays):
        dupes = sorted({t for t in trays if trays.count(t) > 1})
        problems.append(f"Duplicate tray numbers: {dupes}")
    gaps = [b for a, b in zip(sorted(trays), sorted(trays)[1:]) if b != a + 1]
    if gaps:
        problems.append(f"Tray numbers are not consecutive - jumps before tray {gaps}. "
                        "That may be fine if trays are genuinely missing, but check.")

    if first_start is None:
        problems.append("Start depth of the first tray is missing.")
    chained = chain_depths(rows, first_start if first_start is not None else 0.0)
    for r in chained:
        if r["end_m"] is None:
            problems.append(f"Tray {r['tray']}: end depth is missing.")
        elif r["end_m"] <= r["start_m"]:
            problems.append(f"Tray {r['tray']}: end depth {r['end_m']:.2f} m is not "
                            f"greater than its start depth {r['start_m']:.2f} m.")
        elif r["end_m"] - r["start_m"] > 12:
            problems.append(f"Tray {r['tray']}: interval is "
                            f"{r['end_m'] - r['start_m']:.2f} m - unusually long, check the depth.")

    for t, n in sorted(n_photos_by_tray.items()):
        if n != 2:
            problems.append(f"Tray {t}: {n} photo(s), expected 2 (one dry, one wet).")
    return problems


def write_manifest(path, rows):
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
