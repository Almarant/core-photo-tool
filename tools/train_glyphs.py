#!/usr/bin/env python3
"""
Retrain the built-in digit reader.

Only needed if the label stencil kit changes and the app starts leaving depth
boxes blank. It learns from photos you have ALREADY processed correctly, using
the depth in each filename as the label:

    DD26ZOP-006_Dry_Tray22_82.50-86.00m.jpg   ->  the number on the bar is 86.00

Usage:
    python tools/train_glyphs.py <folder of processed photos> [-o out.npz]

Point it at your `processed` folder (it searches recursively). It only learns
from photos where the number of characters it finds matches the number of digits
in the filename, so a mis-segmented photo cannot poison the templates. Aim for
at least ~20 photos covering all ten digits.

Then check the report it prints, and copy the .npz over
corephoto/glyph_templates.npz.
"""
import argparse
import collections
import glob
import os
import re
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from corephoto import glyphs                                     # noqa: E402

GW, GH = 16, 24


def norm(g):
    g = cv2.resize(g, (GW, GH), interpolation=cv2.INTER_AREA).astype(np.float32)
    return cv2.normalize(g, None, 0, 1, cv2.NORM_MINMAX).ravel()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("-o", "--out", default="glyph_templates.npz")
    a = ap.parse_args()

    files = sorted(glob.glob(os.path.join(a.folder, "**", "*.jpg"), recursive=True))
    X, Y, used, skipped = [], [], set(), 0
    for f in files:
        b = os.path.basename(f)
        m = re.search(r"-([\d.]+)m\.jpg$", b)
        if not m:
            continue
        digits = m.group(1).replace(".", "")
        img = cv2.imread(f)
        if img is None:
            continue
        cs = glyphs.characters(glyphs.depth_region(glyphs.bar_slice(img)))
        if len(cs) != len(digits):
            skipped += 1                 # uncertain -> never used for training
            continue
        for g, d in zip(cs, digits):
            X.append(norm(g)); Y.append(int(d))
        used.add(b)

    if not X:
        print("No usable glyphs found. Are these processed photos with depths in "
              "the filename?")
        return 1
    X, Y = np.array(X, np.float32), np.array(Y, np.int8)
    counts = collections.Counter(Y.tolist())
    print(f"learned from {len(used)} photos ({skipped} skipped as uncertain)")
    print(f"glyphs per digit: {dict(sorted(counts.items()))}")
    missing = [d for d in range(10) if counts.get(d, 0) == 0]
    thin = [d for d, c in sorted(counts.items()) if c < 5]
    if missing:
        print(f"  WARNING: no examples of {missing} - those digits will not be read.")
    if thin:
        print(f"  WARNING: very few examples of {thin} - add more photos.")

    cent = np.stack([X[Y == d].mean(0) if counts.get(d) else np.zeros(X.shape[1], np.float32)
                     for d in range(10)])
    pred = ((X[:, None, :] - cent[None, :, :]) ** 2).sum(2).argmin(1)
    print(f"accuracy on its own training glyphs: {(pred == Y).mean() * 100:.1f}%")

    np.savez_compressed(a.out, centroids=cent.astype(np.float32),
                        reject=np.float32(0), gw=np.int16(GW), gh=np.int16(GH),
                        n=np.int32(len(X)))
    print(f"wrote {a.out} ({os.path.getsize(a.out)} bytes)")
    print("Copy it over corephoto/glyph_templates.npz to use it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
