"""
Optional label-bar OCR. Pre-fill only - never trusted.

Measured on 72 real DD26ZOP labels with Tesseract 5.3: hole 36%, tray 59%,
end depth 67% correct. That is far too low to name files unattended, which is
why the app always shows the label bar next to an editable field and validates
the whole depth sequence before writing anything.

If Tesseract is not installed the app still works - fields just start empty.
"""
import os
import re
import shutil
import subprocess
import tempfile
import cv2
import numpy as np

CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-· "


def available():
    return shutil.which("tesseract") is not None


def _prep(bar):
    """Keep only the white letter tiles, then binarise inside them."""
    g = cv2.cvtColor(bar, cv2.COLOR_BGR2GRAY) if bar.ndim == 3 else bar
    bl = cv2.GaussianBlur(g, (0, 0), 3)
    tile = (bl > max(140, np.percentile(bl, 90) * 0.72)).astype(np.uint8) * 255
    tile = cv2.morphologyEx(tile, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25)))
    out = np.full_like(g, 255)
    out[tile > 0] = g[tile > 0]
    return cv2.threshold(out, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]


def read_bar(bar):
    """Returns (hole_suffix, tray_no, end_depth_m); any element may be None."""
    if not available() or bar is None or bar.size == 0:
        return (None, None, None)
    img = _prep(bar)
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    try:
        cv2.imwrite(tmp.name, img)
        p = subprocess.run(["tesseract", tmp.name, "stdout", "--psm", "6",
                            "-c", "tessedit_char_whitelist=" + CHARS],
                           capture_output=True, text=True, timeout=30)
        txt = p.stdout
    except Exception:
        return (None, None, None)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    return parse(txt)


def parse(txt):
    t = re.sub(r"\s+", " ", re.sub(r"[|\n]+", " ", txt.upper()))
    hole = re.search(r"DD\s*\d{2}\s*Z\s*[O0]\s*P\s*[-\s]*(\d{2,3})", t)
    tray = re.search(r"T\s*R\s*A\s*Y\s*(\d{1,2})", t)
    depth = None
    # the stencil decimal is a raised dot, which OCR often reads as - or ,
    m = re.search(r"DEPTH\s*(\d{1,3})\s*[-.,·\s]\s*(\d{2})\b", t)
    if m:
        depth = float(f"{m.group(1)}.{m.group(2)}")
    else:
        m = re.search(r"DEPTH\s*(\d{3,5})\b", t)
        if m:
            s = m.group(1)
            depth = float(s[:-2] + "." + s[-2:])
    return (hole.group(1) if hole else None,
            int(tray.group(1)) if tray else None,
            depth)
