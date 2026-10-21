"""Write the acceptance cases as geometry.json files: python -m tests.fixtures.make_fixtures"""
from __future__ import annotations

import json
from pathlib import Path

from . import builders as b

HERE = Path(__file__).resolve().parent


def full_plate() -> dict:
    """150 x 90 x 12 plate: corner fillets, 4 holes, a slot, a pocket, a boss with a bore, a blind hole."""
    g = b.plate(150, 90, 12)
    g["part"]["part_number"] = "FULL-001"
    g["overall_dimensions"]["height"] = b.Q(32, view="FRONT")  # 12 plate + 20 boss
    holes = b.hole("HOLE_001", diameter=8, quantity=4)
    holes["dimensions"]["diameter"]["evidence"]["raw_text"] = "4X Ø8 THRU"
    g = b.add_feature(g, holes, [
        b.rel("offset_from_edge", "left_edge", 15, "horizontal", instance=1),
        b.rel("offset_from_edge", "bottom_edge", 15, "vertical", instance=1),
        b.rel("offset_from_edge", "right_edge", 15, "horizontal", instance=2),
        b.rel("offset_from_edge", "bottom_edge", 15, "vertical", instance=2),
        b.rel("offset_from_edge", "left_edge", 15, "horizontal", instance=3),
        b.rel("offset_from_edge", "top_edge", 15, "vertical", instance=3),
        b.rel("offset_from_edge", "right_edge", 15, "horizontal", instance=4),
        b.rel("offset_from_edge", "top_edge", 15, "vertical", instance=4),
    ])
    slot = {"id": "SLOT_001", "type": "slot", "quantity": 1, "depth_type": "through", "orientation": "along_y",
            "dimensions": {"length": b.Q(40), "width": b.Q(10), "end_radius": b.Q(5, "R5")}}
    g = b.add_feature(g, slot, [b.rel("offset_from_edge", "left_edge", 35, "horizontal"),
                                b.rel("centered_vertical", "base")])
    pocket = {"id": "POCKET_001", "type": "pocket", "quantity": 1,
              "dimensions": {"length": b.Q(20), "width": b.Q(30), "depth": b.Q(4, view="FRONT")}}
    g = b.add_feature(g, pocket, [b.rel("offset_from_edge", "right_edge", 30, "horizontal"),
                                  b.rel("centered_vertical", "base")])
    g = b.add_feature(g, b.boss("BOSS_001", diameter=30, height=20), [
        b.rel("offset_from_edge", "left_edge", 80, "horizontal"), b.rel("centered_vertical", "base")])
    g = b.add_feature(g, b.hole("BORE_001", diameter=16), [b.rel("concentric", "BOSS_001")])
    blind = b.hole("HOLE_002", diameter=6, depth_type="blind")
    blind["dimensions"]["depth"] = b.Q(6, "Ø6 ▽6", view="FRONT")
    g = b.add_feature(g, blind, [b.rel("offset_from_edge", "left_edge", 60, "horizontal"),
                                 b.rel("offset_from_edge", "top_edge", 15, "vertical")])
    return b.add_feature(g, {"id": "FILLET_001", "type": "fillet", "quantity": 4,
                             "dimensions": {"radius": b.Q(6, "R6 (4 PLACES)")},
                             "evidence": {"raw_text": "CORNER RADIUS R6 (4 PLACES)", "view": "TOP"}})


def chamfer_plate() -> dict:
    g = b.case2_plate_hole()
    g["part"]["part_number"] = "CHAMFER-001"
    return b.add_feature(g, {"id": "CHAMFER_001", "type": "chamfer", "quantity": 4,
                             "dimensions": {"distance": b.Q(8, "8 x 45°"), "angle": b.Q(45, "8 x 45°", unit="deg")},
                             "evidence": {"raw_text": "CORNER CHAMFERS 8 x 45° (4 PLACES)", "view": "TOP"}})


def clevis_bracket() -> dict:
    """Consistent clevis bracket: 120x80x15 base, 4 mounting holes, R10 corners, two full-round ears with a
    Ø30 cross hole, and an outer gusset on each ear."""
    g = b.plate(120, 80, 15)
    g["part"]["part_number"] = "CLEVIS-001"
    g["overall_dimensions"]["height"] = b.Q(85, view="FRONT")  # 15 base + 70 ears
    holes = b.hole("HOLE_001", diameter=12, quantity=4)
    rels = []
    for i, (x, y) in enumerate([(20, 20), (100, 20), (20, 60), (100, 60)], start=1):
        rels += [b.rel("offset_from_edge", "left_edge", x, "horizontal", instance=i, source_type="table"),
                 b.rel("offset_from_edge", "bottom_edge", y, "vertical", instance=i, source_type="table")]
    g = b.add_feature(g, holes, rels)
    g = b.add_feature(g, {"id": "FILLET_001", "type": "fillet", "quantity": 4, "dimensions": {"radius": b.Q(10, "R10")},
                          "evidence": {"raw_text": "BASE CORNER RADIUS R10 (4 PLACES)", "view": "TOP"}})
    ear = {"id": "EAR_001", "type": "ear", "quantity": 2, "normal_axis": "x", "top_type": "full_round",
           "dimensions": {"thickness": b.Q(20, "EAR THICKNESS = 20", view="FRONT"), "width": b.Q(40, view="RIGHT"),
                          "height": b.Q(70, view="RIGHT"), "hole_diameter": b.Q(30, "Ø30 THROUGH", view="RIGHT"),
                          "hole_height": b.Q(50, view="RIGHT")}}
    g = b.add_feature(g, ear, [b.rel("offset_from_edge", "left_edge", 40, "horizontal", instance=1, view="FRONT"),
                               b.rel("offset_from_edge", "right_edge", 40, "horizontal", instance=2, view="FRONT"),
                               b.rel("centered_vertical", "base", None, raw="40 TYP")])
    return b.add_feature(g, {"id": "RIB_001", "type": "rib", "quantity": 2, "attached_to": "EAR_001", "side": "outer",
                             "centered_on_lug": True,
                             "dimensions": {"rise": b.Q(30, "30 (RISE)", view="DETAIL"), "run": b.Q(20, view="FRONT"),
                                            "thickness": b.Q(10, "RIB THICKNESS = 10", view="DETAIL")}})


def mounting_bracket() -> dict:
    """120x60x15 base, R10 corners, 4X Ø12 as a grid (12 from left/top, 96 x 36 spacing), Ø60x35 centred boss,
    Ø40 bore, radial ribs along ±X (run 15, rise 20, thickness 10)."""
    g = b.plate(120, 60, 15)
    g["part"]["part_number"] = "MB-TEST"
    g["overall_dimensions"]["height"] = b.Q(50, view="FRONT")
    holes = b.hole("HOLE_001", diameter=12, quantity=4)
    g = b.add_feature(g, holes, [
        b.rel("offset_from_edge", "left_edge", 12, "horizontal"), b.rel("center_to_center", "", 96, "horizontal"),
        b.rel("offset_from_edge", "top_edge", 12, "vertical"), b.rel("center_to_center", "", 36, "vertical")])
    g = b.add_feature(g, {"id": "FILLET_001", "type": "fillet", "quantity": 4, "dimensions": {"radius": b.Q(10, "R10")},
                          "evidence": {"raw_text": "R10 (4 PLACES) corners", "view": "TOP"}})
    g = b.add_feature(g, b.boss("BOSS_001", diameter=60, height=35), [b.rel("centered_on_plate", "base")])
    g = b.add_feature(g, b.hole("BORE_001", diameter=40), [b.rel("concentric", "BOSS_001")])
    g = b.add_feature(g, {"id": "RIB_001", "type": "rib", "quantity": 2, "attached_to": "BOSS_001",
                          "directions": ["+x", "-x"],
                          "dimensions": {"rise": b.Q(20, view="FRONT"), "run": b.Q(15, view="FRONT"),
                                         "thickness": b.Q(10)}})
    g["dimension_chains"] = [{"view": "TOP", "orientation": "horizontal", "values": [12, 96, 12], "unit": "mm",
                              "from": "left_edge", "to": "right_edge"}]
    return g


def main() -> None:
    cases = {**b.CASES, "full_plate": full_plate, "chamfer_plate": chamfer_plate, "clevis_bracket": clevis_bracket,
             "mounting_bracket": mounting_bracket}
    for name, fn in cases.items():
        d = HERE / name
        d.mkdir(exist_ok=True)
        (d / "geometry.json").write_text(json.dumps(fn(), indent=2, ensure_ascii=False), encoding="utf-8")
        print("wrote", d / "geometry.json")


if __name__ == "__main__":
    main()
