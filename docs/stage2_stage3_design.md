# Stage 1 → Stage 2 → Stage 3 interface

## 1. Existing layout

| Path | Role |
|---|---|
| `src/` | **Stage 1** (unchanged): `main.py` CLI, `extractor.py` pipeline, `vlm_client.py`, `schemas.py` (permissive Pydantic), `validator.py` (evidence audit) |
| `prompts/` | Stage 1 extraction + verification prompts |
| `output/<part>/geometry.json` | Stage 1 result (real examples: `output/drawing_1/`, `output/drawing_2/`) |

Stage 2/3 are added beside it (`stage2/`, `stage3/`, `utils/`, root `main.py`) and read `geometry.json`
only; they never import `src/`.

## 2. What Stage 1 actually produces

```jsonc
{
  "part": {"part_number", "part_name", "units": "mm"|null, "material", "scale", "projection"},
  "views": [{"name": "FRONT VIEW", "view_type", ...}],
  "overall_dimensions": {"length": Q, "width": Q, "height": Q},
  "features": [{"id", "type": <free snake_case>, "quantity", "dimensions": {<free name>: Q},
                "depth_type": "through"|"blind"|..., "thread", "evidence", "notes", ...}],
  "relationships": [{"feature_id", "relationships": [
      {"type": "offset_from_edge"|"center_to_center"|"aligned_with"|..., "reference": <free str>,
       "target": "center"|..., "instance": int|null, "orientation": "horizontal"|"vertical"|...,
       "value": number|null, "unit", "evidence": {...}, "dimension_location"}]}],
  "tolerances": [...], "notes": [...],
  "missing_information": [{"field", "reason", "required_for_cad"}],
  "ambiguities": [{"field", "possible_interpretations", "evidence"}],
  "validation_flags": [...], "review_log": [...]
}
// Q = {"value": number|null, "unit", "status": extracted|missing|ambiguous|rejected,
//      "tolerance", "confidence", "evidence": {"raw_text", "view", "source_type"}}
```

### Directly consumable
- Units per value + `part.units`; values with evidence and status.
- Base size: `overall_dimensions` and/or `base_plate` feature dimensions.
- Hole/slot/fillet/chamfer sizes, quantity, `depth_type`.
- Positional relationships with edge references, orientation, instance, value and the view they came from.
- Stage 1's own `missing_information` / `ambiguities` → carried over as REVIEW_REQUIRED issues.

### Gaps Stage 2 has to handle (not fabricate)
| Gap | Stage 2 handling |
|---|---|
| Free-form feature types (`base_plate`, `boss`, `rib`, `ear`...) | Alias table → canonical type; unknown types → `UNSUPPORTED_FEATURE` (REVIEW_REQUIRED) |
| Free-form dimension names (`thickness`/`height`, `end_radius`, `distance`/`length`) | Per-type alias table |
| Which view is the base *plan* view (X/Y) | Deterministic rule: view named TOP/PLAN, else the view carrying the hole/slot callouts; logged as a warning |
| Multi-instance features with `instance: null` relationships | Only allowed when quantity is 1; otherwise `AMBIGUOUS_INSTANCE` |
| `target` null on hole relationships | Drafting convention: holes are located to their centre → assumed, warning logged |
| Slot orientation not captured | `orientation` field if present, else `SLOT_ORIENTATION_UNKNOWN` |
| Edge selection for fillets/chamfers | Supported when the text/quantity says base corners (e.g. "4 PLACES", "CORNER"); else REVIEW |
| One value per dimension (no per-view list) | Multi-view check compares `overall_dimensions` vs base feature dims (different evidence views) and any optional `occurrences: [{view, value, unit}]` list |

### Proposed minimal Stage 1 changes (not applied)
1. Prompt: record every repeat of the same dimension in other views as `occurrences` → enables full multi-view checking.
2. Prompt: add `orientation` (`along_x`/`along_y`/angle) to slots and `edges` to fillets/chamfers.
Stage 2 works without them; it just returns REVIEW_REQUIRED where the information is absent.

## 3. Stage 2 output: `validated_geometry.json`

```jsonc
{
  "status": "PASS" | "REVIEW_REQUIRED" | "FAIL",
  "part_name": "SP-002", "units": "mm",
  "coordinate_system": {"origin": "base_lower_left_bottom", "x": "length", "y": "width", "z": "height",
                        "plan_view": "FRONT VIEW"},
  "base": {"id", "type": "rectangular_prism", "length", "width", "thickness"},     // mm
  "features": [
    {"id": "HOLE_001", "type": "hole", "diameter": 10, "through": true, "depth": null,
     "instances": [{"x": 20, "y": 15, "z": 15, "x_source": "...", "y_source": "..."}]},
    {"id": "SLOT_001", "type": "slot", "length": 100, "width": 20, "orientation": "along_x", "instances": [...]},
    {"id": "BOSS_001", "type": "boss", "diameter": 50, "height": 35, "instances": [...]},
    {"id": "FILLET_001", "type": "fillet", "radius": 10, "edges": "base_vertical_corners"},
    {"id": "CHAMFER_001", "type": "chamfer", "distance": 10, "angle": 45, "edges": "base_vertical_corners"}
  ],
  "measurements": [{"field", "original_value", "original_unit", "normalized_value_mm", "evidence"}],
  "validation": {"schema", "units", "dimensions", "relationships", "multiview", "geometry"},  // per-stage status
  "warnings": [Issue], "errors": [Issue]
}
// Issue = {"status", "stage", "code", "message", "feature_id", "evidence"}
```
`validation_report.json` = all issues grouped by stage + counts + solved-relationship trace.

## 4. Stage 3 `cad_plan.json`

```jsonc
{
  "part_name": "SP-002", "units": "mm", "source_status": "PASS",
  "operations": [
    {"id": "OP001", "operation": "create_sketch", "plane": "XY", "offset": 0},
    {"id": "OP002", "operation": "rectangle", "sketch": "OP001", "origin": [0,0], "length": 120, "width": 80},
    {"id": "OP003", "operation": "extrude", "profile": "OP001", "distance": 15, "direction": "+Z"},
    {"id": "OP004", "operation": "create_sketch", "plane": "XY", "offset": 15},
    {"id": "OP005", "operation": "circle", "sketch": "OP004", "center": [60,40], "diameter": 50},
    {"id": "OP006", "operation": "extrude", "profile": "OP004", "distance": 35},
    {"id": "OP007", "operation": "create_sketch", "plane": "XY", "offset": 0},
    {"id": "OP008", "operation": "circle", "sketch": "OP007", "center": [60,40], "diameter": 30},
    {"id": "OP009", "operation": "cut_extrude", "profile": "OP007", "extent": "through_all_both"},
    {"id": "OP010", "operation": "fillet", "target": "OP003", "edges": "base_vertical_corners", "radius": 10}
  ]
}
```
Sketches are placed on the XY plane at an explicit Z offset instead of "select top face": it is
deterministic and does not depend on picking faces by position in the CAD tool. Through cuts go both
directions from Z=0 so they cut the full body.

Order: base → bosses → cuts (holes, slots, pockets) → edge treatments (fillet, chamfer).
Patterns are expanded into explicit instances by the planner (POC); `linear_pattern`/`circular_pattern`
exist in the operation library but are not emitted yet.

## 5. Files

```
main.py                         CLI for stages 2/3
utils/units.py geometry.py logging_config.py
stage2/models.py schema_validator.py unit_normalizer.py dimension_validator.py
       relationship_solver.py coordinate_solver.py multiview_checker.py geometry_checker.py
       validation_report.py pipeline.py
stage3/planner/operation_models.py planner.py dependency_validator.py plan_validator.py
stage3/cad/backend.py executor.py backends/dry_run_backend.py backends/solidworks_backend.py
tests/test_stage2.py test_relationship_solver.py test_geometry_checker.py test_cad_planner.py fixtures/
```
