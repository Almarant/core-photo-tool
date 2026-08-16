"""
Label-bar OCR. Pre-fill only, never trusted.

Measured on 72 real DD26ZOP labels (Tesseract 5.3):

  whole bar, default settings ....... hole 36%, tray 59%, end depth 67%
  depth region + digit whitelist .... 38% exact
  ... plus sequence-based repair .... 57% correct, 38% blank, 6% SILENTLY WRONG

That last 6% is the reason nothing here is auto-accepted. The common failure is
a dropped leading digit (110.75 read as 10.75), which breaks the depth sequence
and so can be caught and often repaired. What survives repair is the nasty kind:
22.90 read as 22.3, still increasing, still a plausible interval, undetectable.
So the app shows every OCR value as unconfirmed until a human passes over it.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import cv2
import numpy as np

DIGITS = "0123456789."


def _bundled_tesseract():
    """PyInstaller unpacks bundled binaries next to the app; prefer that copy."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        for name in ("tesseract.exe", "tesseract"):
            p = os.path.join(base, name)
            if os.path.exists(p):
                return p
    return None


def tesseract_path():
    return _bundled_tesseract() or shutil.which("tesseract")


def available():
    return tesseract_path() is not None


def _run(img, psm, whitelist):
    exe = tesseract_path()
    if not exe:
        return ""
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    env = dict(os.environ)
    base = getattr(sys, "_MEIPASS", None)
    if base and os.path.isdir(os.path.join(base, "tessdata")):
        env["TESSDATA_PREFIX"] = base
    try:
        cv2.imwrite(tmp.name, img)
        p = subprocess.run([exe, tmp.name, "stdout", "--psm", str(psm),
                            "-c", "tessedit_char_whitelist=" + whitelist],
                           capture_output=True, text=True, timeout=30, env=env,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return p.stdout
    except Exception:
        return ""
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _tiles(g):
    """Mask of the white letter tiles."""
    bl = cv2.GaussianBlur(g, (0, 0), 3)
    m = (bl > max(140, np.percentile(bl, 90) * 0.72)).astype(np.uint8) * 255
    return cv2.morphologyEx(m, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25)))


def depth_crop(bar):
    """The rightmost group of white tiles = the END DEPTH number.

    Padded generously on the left: a tight crop clips the leading digit, which
    was the single biggest source of wrong readings in testing.
    """
    if bar is None or bar.size == 0:
        return None
    g = cv2.cvtColor(bar, cv2.COLOR_BGR2GRAY) if bar.ndim == 3 else bar
    m = _tiles(g)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
    groups = [(st[i, 0], st[i, 1], st[i, 2], st[i, 3]) for i in range(1, n)
              if st[i, 4] > 6000 and st[i, 3] > 35 and st[i, 2] > 60]
    if not groups:
        return None
    x, y, w, h = max(groups, key=lambda b: b[0])
    padx, pady = int(0.60 * h), int(0.30 * h)      # generous left/right pad
    sub = g[max(y - pady, 0):min(y + h + pady, g.shape[0]),
            max(x - padx, 0):min(x + w + padx, g.shape[1])]
    if sub.size == 0:
        return None
    sub = cv2.resize(sub, (sub.shape[1] * 3, sub.shape[0] * 3), interpolation=cv2.INTER_CUBIC)
    sub = cv2.threshold(sub, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    return cv2.copyMakeBorder(sub, 30, 30, 30, 30, cv2.BORDER_CONSTANT, value=255)


def _to_depth(text):
    s = "".join(re.findall(r"\d", text))
    if len(s) < 3:
        return None
    s = s[:5]
    try:
        return float(s[:-2] + "." + s[-2:])
    except ValueError:
        return None


def read_depth(bar):
    """Best-effort end depth from the label bar, or None."""
    if not available():
        return None
    crop = depth_crop(bar)
    if crop is None:
        return None
    for psm in (8, 7, 13, 6):
        d = _to_depth(_run(crop, psm, DIGITS))
        if d is not None:
            return d
    return None


def repair_depth(value, prev_end, min_interval=0.3, max_interval=8.0):
    """Use the depth sequence to fix a dropped leading digit.

    Returns the repaired value only when exactly one candidate fits between
    prev_end + min_interval and prev_end + max_interval; otherwise None, so the
    field is left blank rather than filled with a guess.
    """
    if value is None or prev_end is None:
        return value
    cands = [value]
    ip, dp = f"{value:.2f}".split(".")
    for d in "123456789":
        cands.append(float(d + ip + "." + dp))
    ok = [c for c in cands if min_interval <= c - prev_end <= max_interval]
    return ok[0] if len(ok) == 1 else None


def read_hole_and_tray(bar):
    """Hole suffix and tray number from the whole bar. Weaker than the depth
    read (36% / 59% in testing) - a hint for the first row only."""
    if not available() or bar is None:
        return (None, None)
    g = cv2.cvtColor(bar, cv2.COLOR_BGR2GRAY) if bar.ndim == 3 else bar
    tile = _tiles(g)
    out = np.full_like(g, 255)
    out[tile > 0] = g[tile > 0]
    img = cv2.threshold(out, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    t = re.sub(r"\s+", " ", re.sub(r"[|\n]+", " ",
               _run(img, 6, "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-· ").upper()))
    hole = re.search(r"DD\s*\d{2}\s*Z\s*[O0]\s*P\s*[-\s]*(\d{2,3})", t)
    tray = re.search(r"T\s*R\s*A\s*Y\s*(\d{1,2})", t)
    return (hole.group(1) if hole else None,
            int(tray.group(1)) if tray else None)
