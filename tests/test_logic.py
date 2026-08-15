"""Pure-logic checks. No GUI, so this runs on a CI box with no display."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from corephoto import naming, core, ocr   # noqa: E402


def main():
    assert naming.pair_photos(["b", "a", "d", "c"]) == [("a", "b"), ("c", "d")]

    # the darker core is wet, regardless of shooting order
    assert naming.decide_wet(("a", "b"), {"a": 120.0, "b": 70.0})[0] == "b"
    assert naming.decide_wet(("a", "b"), {"a": 70.0, "b": 120.0})[0] == "a"

    ch = naming.chain_depths([{"tray": 2, "end_m": 7.1}, {"tray": 1, "end_m": 3.5}], 0.0)
    assert [r["start_m"] for r in ch] == [0.0, 3.5]

    assert naming.make_name("DD26ZOP-006", "Dry", 22, 82.5, 86.0) == \
        "DD26ZOP-006_Dry_Tray22_82.50-86.00m.jpg"

    # validation catches the mistakes that matter
    p = naming.validate("H1", [{"tray": 1, "end_m": 5.0}, {"tray": 2, "end_m": 4.0}], 0.0,
                        {1: 2, 2: 2})
    assert any("not greater" in x for x in p), p
    p = naming.validate("", [{"tray": 1, "end_m": 5.0}], 0.0, {1: 1})
    assert any("Hole ID" in x for x in p) and any("expected 2" in x for x in p), p

    # OCR parser handles the raised-dot decimal that reads as - or ,
    assert ocr.parse("DD26Z0P - 006 TRA Y22 END DEPTH 86-00") == ("006", 22, 86.0)
    assert ocr.parse("DD26ZOP -007 TRAY 21 END DEPTH 8472")[2] == 84.72

    pr = core.Params()
    assert abs(pr.scale - 5952 / ((744 + 12) * 8)) < 1e-9
    print("all logic checks passed")


if __name__ == "__main__":
    main()
