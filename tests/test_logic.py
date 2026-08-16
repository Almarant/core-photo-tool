"""Pure-logic checks. No GUI, so this runs on a CI box with no display."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from corephoto import naming, core, ocr   # noqa: E402


def main():
    srcs = ["a", "b", "c", "d", "e"]
    g = naming.suggest_trays(srcs)
    assert [g[s] for s in srcs] == [1, 1, 2, 2, 3]

    # grouping is by explicit tray number, so a reshoot cannot shift later trays
    tray_of = {"a": 1, "b": 1, "c": 1, "d": 2, "e": 2}
    gr = naming.group_by_tray(tray_of)
    assert gr == {1: ["a", "b", "c"], 2: ["d", "e"]}, gr

    # darker core is wet, regardless of order; override always wins
    c, m = naming.decide_conditions(["a", "b"], {"a": 120.0, "b": 70.0})
    assert c == {"a": "Dry", "b": "Wet"} and m == 50.0
    c, _ = naming.decide_conditions(["a", "b"], {"a": 70.0, "b": 120.0})
    assert c == {"a": "Wet", "b": "Dry"}, "shooting order must not decide it"
    c, _ = naming.decide_conditions(["a", "b"], {"a": 70.0, "b": 120.0}, {"a": "Dry"})
    assert c["a"] == "Dry", "user override must win"

    ch = naming.chain_depths([1, 2, 3], {1: 3.5, 2: 7.1, 3: 10.0}, 0.0)
    assert ch[2] == (3.5, 7.1) and ch[3] == (7.1, 10.0), ch
    ch = naming.chain_depths([1, 2], {1: None, 2: 7.1}, 0.0)
    assert ch[2] == (0.0, 7.1), "a missing depth must not shift later trays silently"

    assert naming.make_name("DD26ZOP-006", "Dry", 22, 82.5, 86.0) == \
        "DD26ZOP-006_Dry_Tray22_82.50-86.00m.jpg"
    assert naming.disambiguate(["x.jpg", "x.jpg", "y.jpg"]) == ["x.jpg", "x_2.jpg", "y.jpg"]

    p = naming.validate("H1", {1: ["a", "b"], 2: ["c", "d"]}, {1: 5.0, 2: 4.0}, 0.0)
    assert any("not deeper" in x for x in p), p
    p = naming.validate("", {1: ["a"]}, {1: 5.0}, 0.0)
    assert any("Hole ID" in x for x in p) and any("Expected 2" in x for x in p), p
    p = naming.validate("H1", {1: ["a", "b"], 2: ["c", "d"]}, {1: 3.5, 2: 7.1}, 0.0)
    assert p == [], p

    # a dropped leading digit is repairable from the sequence; ambiguity is not
    assert ocr.repair_depth(14.32, 110.75) == 114.32
    assert ocr.repair_depth(89.70, 86.00) == 89.70
    assert ocr.repair_depth(None, 86.0) is None
    assert ocr.repair_depth(5.0, 200.0) is None, "no plausible candidate -> blank"

    pr = core.Params()
    assert abs(pr.scale - 5952 / ((744 + 12) * 8)) < 1e-9
    print("all logic checks passed")


if __name__ == "__main__":
    main()
