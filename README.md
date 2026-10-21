# 2D Engineering Drawing → Parametric SolidWorks Model

```
drawing.png ──Stage 1 (VLM)──▶ geometry.json ──Stage 2 (Python)──▶ validated_geometry.json
            ──Stage 3A (planner)──▶ cad_plan.json ──Stage 3B (executor)──▶ SolidWorks ▶ SLDPRT / STEP / STL
```

## Web front end

```bash
python -m webapp          # http://127.0.0.1:8000
```

Upload a drawing → the five stages run in the background with live status. Tabs: **Drawing vs model** (input
drawing beside an interactive 3D view of the exported STL plus top/front/right/isometric renders),
**Validation** (solved geometry, errors, warnings), **Review**, **CAD plan**, **Verification** (every check with
expected vs measured and the drawing text it came from), **Files** (SLDPRT/STEP/STL, reports, logs).
Deep links: `#<part>/<tab>`.

**Human review.** When Stage 2 reports REVIEW_REQUIRED, CAD stops. In the Review tab you edit the geometry,
write a note (required) and save: it is stored as `geometry_reviewed.json`, every change is diffed into
`review_log.json`, and Stages 2–5 re-run. The pipeline never fills gaps itself; a reviewer does, on the record.
Scripted examples of the reviews used for the sample drawings: `tests/fixtures/review_drawing_1.py`,
`tests/fixtures/review_drawing_2.py`.

| Stage | Command | Responsibility |
|---|---|---|
| all | `python main.py --image input/part.png` | stages 1–5 from the CLI |
| 1 | `python -m src.main --input input/part.png` | VLM reads the drawing → evidence-backed `geometry.json` |
| 2 | `python main.py --geometry output/part/geometry.json --stage validate` | schema, units → mm, dimension sanity, relationship → X/Y/Z solving, multi-view and feasibility checks. No LLM. |
| 3 | `... --stage plan` | deterministic, dependency-checked `cad_plan.json` from a controlled operation library |
| 4 | `... --stage cad` (`--dry-run` to skip SolidWorks) | runs the plan through a `CADBackend` (SolidWorks via COM), exports SLDPRT/STEP/STL |
| 5 | (runs after 4) | measures the built model and compares it with the drawing values → `verification_report.json` + renders |

Stage 2/3 details: [docs/stage2_stage3_design.md](docs/stage2_stage3_design.md). Stage 1 details follow below.

## Stages 2 and 3

**Statuses.** `PASS` → CAD allowed. `REVIEW_REQUIRED` (missing / ambiguous / conflicting info) and `FAIL`
(impossible or malformed geometry) → CAD refused (exit code 2). `--allow-review` is a development override for
REVIEW_REQUIRED only: the planner builds just the fully determined features and lists the rest in
`cad_plan.json → skipped`. FAIL can never be overridden.

**Coordinate system.** mm; origin at the lower-left corner of the base profile on its bottom face; X = length,
Y = width, Z = height. The "plan view" (X/Y) is the view carrying the hole/slot callouts (logged as a warning).

**Outputs** (next to `geometry.json`, or `--output-dir`):
```
validated_geometry.json   solved geometry + per-stage status + warnings/errors (structured)
validation_report.json    issues by stage, solver trace, unit conversions
cad_plan.json             ordered operations + analytic expectations (volume, bbox, cylinder axes)
cad_result.json           execution result + measured model (volume, exact bbox, cylinders, bodies)
verification_report.json  Stage 5: checks by category (validity, overall size, thickness, volume, hole/feature
                          positions, feature count, completeness) -> PASS / REVIEW_REQUIRED / FAIL
cad/render_*.png          orthographic renders of the exported model
pipeline_status.json      per-stage status (used by the web UI)
geometry_reviewed.json, review_log.json   human review (if any)
cad/part.SLDPRT|STEP|STL
logs/validation.log, logs/cad_execution.log
```

**Model verification (Stage 5).** After building, the backend measures the SolidWorks body (volume, exact bounding
box, every cylindrical face's axis/centre/radius, body count, rebuild errors). Stage 5 compares this with the
drawing-derived geometry and the planner's analytic expectations. A misplaced hole or wrong cut direction fails
even if SolidWorks raised no error. An incomplete model (override, skipped features) is REVIEW_REQUIRED.

**SolidWorks notes.** Tested with SolidWorks 2026 (rev 34). The part is built on the Top Plane, so SolidWorks
model coordinates are `(X, Y, Z) = (x, z, -y)` of the part frame (SolidWorks Y is up); STEP/STL are exported in
those model coordinates (STL is not translated to positive space). Set `SW_PART_TEMPLATE` if no default part
template is configured. The backend closes its document but leaves SolidWorks running.

**Supported (POC):** rectangular base, through/blind holes, bosses (+ concentric bores), straight slots,
rectangular pockets/cutouts, fillets/chamfers on the base's vertical corners, **lugs** (upright ears/tabs with
full-round, flat or radiused tops and a cross hole) and **gussets** (triangular ribs on a lug face);
relationships from edges, centring, centre-line alignment, concentric, feature-to-feature offsets,
linear/circular patterns. Feasibility checks include zero-thickness walls (tangent features), holes under lugs
or gussets, and gussets reaching into a lug's cross hole.
Not yet: revolve/pattern execution, pocket corner radii, side (horizontal-axis) features, threads (reported, built
at the printed Ø), non-rectangular bases, GD&T.

**Tests.** `python -m pytest tests` (73 offline tests, incl. web API). Live SolidWorks tests: `RUN_SOLIDWORKS=1 python -m pytest tests`.
Fixtures for the CLI: `python -m tests.fixtures.make_fixtures` → `tests/fixtures/<case>/geometry.json`.

---

# Stage 1 — 2D Engineering Drawing → Geometry JSON

```
2D drawing → VLM pass 1 (extract) → VLM pass 2 (checker verifies against the drawing)
          → deterministic evidence audit → Pydantic validation → output/<name>/geometry.json
```

### How hallucinations are controlled

| Layer | What it does |
|---|---|
| Extraction prompt (`prompts/engineering_extraction_prompt.txt`) | Printed values only, no arithmetic, no visual symmetry, conflicts → `ambiguities`, every positional dimension states its `orientation`, self-check list. |
| Verification pass (`prompts/verification_prompt.txt`) | A second VLM call traces every dimension line to what it really connects, recounts instances, checks location completeness, and logs its changes in `review_log`. Falls back to pass 1 if it fails. Skip with `--no-verify`. |
| Evidence audit (`src/validator.py`, no LLM) | Rejects any value that (a) has no evidence, (b) does not literally appear in its evidence text (i.e. was computed), (c) has evidence describing reasoning ("symmetric appearance", "assumed"...), or (d) has an orientation that contradicts its reference edge. Rejected values become `null` with `rejected_value`/`rejection_reason` and are added to `missing_information`. Null values not listed as missing are added automatically. |

The VLM acts only as an **information extractor**. It never estimates dimensions from pixels, never invents
missing values (they become `null` + an entry in `missing_information`), and never resolves ambiguous
dimensions silently (they go to `ambiguities`). No CAD, STEP/STL or coordinate solving happens here — that is Stage 2.

## 1. Installation

```bash
python -m venv .venv && .venv\Scripts\activate      # optional
pip install -r requirements.txt
```

## 2. Environment configuration

```bash
copy .env.example .env      # then edit .env
```

| Variable | Meaning |
|---|---|
| `VLM_API_KEY` | API key (never printed or logged) |
| `VLM_MODEL` | Vision-capable model name |
| `VLM_BASE_URL` | Optional OpenAI-compatible endpoint (Azure/OpenRouter/vLLM/Ollama...) |
| `VLM_TIMEOUT_SECONDS` | Request timeout (default 180) |
| `VLM_MAX_TOKENS` | Max response tokens (default 16000). Domo truncates at ~4k without it. |
| `VLM_JSON_MODE` | `true` if the provider supports `response_format=json_object` |

**Domo AI Service Layer:** set `DOMO_API_KEY`, `DOMO_BASE_URL` (e.g. `https://<instance>.domo.com/api`) and
`DOMO_MODEL` (e.g. `domo.google.gemini-2.5-pro`). It is auto-selected when `VLM_API_KEY` is empty (or force it
with `VLM_PROVIDER=domo`). It uses `/ai/v1/image/text`; note `/ai/v1/text/generation` silently ignores images.

The provider is isolated in `src/vlm_client.py`. To use another provider, add a class with
`extract(image_path, prompt) -> str` and a `model` attribute, and return it from `create_client()`.

## 3. Where to put drawings

Put images in `input/`. Missing directories (`output/`, per-part folders) are created automatically.

## 4. Run

```bash
python -m src.main --input input/RP-001.png
```

Optional: `--output-dir <dir>` (default `output`), `--no-verify` (single VLM pass: faster, less reliable).
A run with verification takes ~1–1.5 min with Gemini 2.5 Pro via Domo. Exit code is `0` for success/partial, `1` for failure.

Offline self-test with a fake VLM (no API key needed): `python -m tests.test_pipeline`

## 5. Output structure

```
output/RP-001/
├── geometry.json            # main Stage 1 output (only written if features were extracted)
├── raw_vlm_response.json    # raw model text of every pass (and retries), always kept
└── extraction_report.json   # status summary
```

Report statuses: `success` (features, nothing missing/ambiguous), `partial` (features extracted but required
values are missing or ambiguities exist), `failed` (bad JSON, schema violation, no features, API error).
The status is computed by the application, not trusted from the model.

## 6. Example output (abridged)

```json
{
  "part": {"part_number": "RP-001", "part_name": null, "units": "mm"},
  "views": [{"name": "TOP", "view_type": "orthographic"}],
  "overall_dimensions": {"length": {"value": 100.0, "unit": "mm", "status": "extracted",
      "evidence": {"raw_text": "100", "view": "TOP", "source_type": "dimension"}}},
  "features": [
    {"id": "FEATURE_001", "type": "base_plate",
     "dimensions": {"thickness": {"value": null, "unit": "mm", "status": "missing"}}},
    {"id": "HOLE_001", "type": "hole", "quantity": 4, "depth_type": "through",
     "dimensions": {"diameter": {"value": 10.0, "unit": "mm",
        "evidence": {"raw_text": "4X Ø10 THRU", "view": "TOP", "source_type": "callout"}}}}
  ],
  "relationships": [{"feature_id": "HOLE_001", "relationships": [
     {"type": "offset_from_edge", "reference": "left_edge", "value": 20.0, "unit": "mm"}]}],
  "missing_information": [{"field": "FEATURE_001.thickness",
     "reason": "No thickness dimension found in the drawing", "required_for_cad": true}],
  "ambiguities": [],
  "extraction_metadata": {"status": "partial"}
}
```

`extraction_report.json`:

```json
{"input_file": "RP-001.png", "extraction_status": "partial", "features_detected": 2,
 "missing_required_values": 1, "ambiguities": 0, "errors": []}
```

## 7. Current limitations

- Extraction quality depends entirely on the VLM. Small text, dense drawings and low-resolution scans can
  produce misreads; evidence (`raw_text`, `view`) is included so values can be checked by a human.
- The schema validates structure, not engineering correctness. It does not cross-check that values are
  consistent (e.g. hole inside plate) — that belongs in Stage 2.
- The audit catches *computed* and *unevidenced* values, but not a *misattributed* one: if the model quotes a
  number that really is printed but belongs to a different dimension line, only the verification pass can
  catch it, and it does not always. Expect occasional wrong relationships on dense drawings; check them
  against `dimension_location` / evidence before trusting a `success` result.
- Results vary run to run (VLM sampling). Sizes, callouts and notes are stable; positional relationships
  are the least stable part.
- Single image only (no multi-page PDFs); no coordinate solving, no CAD generation.
- Only the OpenAI-compatible client is implemented; the pipeline was verified offline with a fake VLM and
  has not been run against a live model in this repository (no API key/sample drawing was available).
"# teal_poc" 
