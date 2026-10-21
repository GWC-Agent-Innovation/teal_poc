# Tellis: 2D image → TRELLIS.2 → 3D model → SolidWorks

```
 2D image ──▶ TRELLIS.2 ──▶ 3D model ──▶ SolidWorks ──▶ SLDPRT / STEP
              image → mesh   repair,      import as
              (GLB)          decimate,    solid body
                             scale (mm)
                                   │
                    backend (FastAPI) ──▶ frontend (interactive 3D)
```

| Step | File | What it does |
|---|---|---|
| TRELLIS.2 | `trellis_client.py` | Turns the image into a textured mesh (GLB), through the Hugging Face Space or your own GPU. The mesh is Y-up and sits inside a unit cube. |
| 3D model | `mesh_prep.py` | Repairs the mesh and decimates it to `max_faces`. Then it turns it Z-up, scales the longest side to your size in mm, and sets it on the floor. |
| SolidWorks | `solidworks_import.py` | Imports the mesh as a **solid body**, then saves `part.SLDPRT` and exports `part.STEP` and `part.STL`. It reuses the repo's COM backend. |
| Backend | `server.py` | FastAPI. Runs one job at a time, because SolidWorks and the GPU can each only handle one. |
| Frontend | `static/` | Upload either a photo for image-to-3D or a dimensioned drawing for traced CAD, view live stages and previews, and download the generated files. Image-to-3D jobs also support re-running with a new size or face budget. |

## Run (from the repository root)

```bash
pip install -r requirements.txt -r tellis/requirements.txt
python -m tellis serve                                    # http://127.0.0.1:8100
python -m tellis run --image drawing.png --size-mm 120    # CLI
python -m tellis run --image drawing.png --mesh my.glb    # skip TRELLIS.2, use an existing mesh
python -m tellis drawing --image drawing.png --thickness 10 --relief 3  # dimensioned drawing -> traced CAD
python -m pytest tellis/tests                             # add RUN_SOLIDWORKS=1 to include the SolidWorks import
```

In the web UI, choose **Multi-view engineering drawing to CAD** to submit a full engineering sheet. It sends all
orthographic, section, detail, and isometric views together through the repository's evidence-backed geometry
extractor, cross-view solver, CAD planner, SolidWorks builder, and verification stages. Missing or conflicting
dimensions stop the build for review. Choose **Single-view traced profile to plate** only for one-view flat parts.

## TRELLIS.2

| `TRELLIS_PROVIDER` | What it does |
|---|---|
| `huggingface` (default) | Calls the official [microsoft/TRELLIS.2](https://huggingface.co/spaces/microsoft/TRELLIS.2) Space. You don't need a GPU. **Set `HF_TOKEN`**: anonymous users get only a few minutes of GPU per day, which isn't enough for one model. Get a free read token at https://huggingface.co/settings/tokens. With a Pro account the quota is much larger. Your images are uploaded to Hugging Face. |
| `remote` | Your own GPU machine (NVIDIA, 24 GB or more). Run `python trellis_server.py --port 7960` there, then set `TRELLIS_URL=http://<gpu>:7960`. |
| `local` | TRELLIS.2 installed in this environment (needs the same kind of GPU). |
| `none` | Only uploaded meshes work. |

## Things to know

- **Size.** TRELLIS.2 has no idea of real size, so give the length of the longest side (`--size-mm`, or the
  field in the UI). Without it, the longest side is set to `TELLIS_DEFAULT_SIZE_MM` (100 mm) and the job shows a warning.
- **Faces vs time.** SolidWorks makes one B-rep face per triangle. Measured on this machine: 5,000 faces
  took about 11 s and 20,000 faces about 97 s. The default is 20,000 (`TELLIS_MAX_FACES`).
- **What you get.** You get a solid body that you can measure, section, cut, add features to, and export as STEP.
  It is a faceted body, **not** a feature tree with editable sketches and dimensions.
- **Input images.** TRELLIS.2 is trained on pictures of objects (photos and renders). For a multi-view engineering
  drawing, crop it to a single isometric or shaded view first; line-only orthographic views give poor meshes.
- If the mesh cannot be closed, SolidWorks imports it as a surface body instead, and the job shows a warning.

## Outputs (`tellis/output/<job>/`)

`input.*`, `trellis/mesh.glb` (or `trellis/uploaded.*`), `model.stl` (mm, Z-up), `model_sw.stl` (SolidWorks
frame), `mesh_report.json`, `cad_result.json`, `cad/part.SLDPRT|STEP|STL`, `logs/pipeline.log`, `job.json`.

## Dimensioned 2D drawing → exact SolidWorks part (`tellis/drawing/`)

TRELLIS.2 turns a line drawing into a flat sheet of paper, so drawings use a separate path:

```
drawing ─┬─ linework.py   OpenCV: keep the thick part lines, drop thin dimension lines/text/leaders
         │                (symmetric drawings: keep only lines that have a mirror partner)
         │                -> outline, inner regions, cut-outs (stroke centre, mm, scaled by the overall height)
         └─ dimensions.py VLM reads every dimension value (radii, lengths, angles, overall height)
fit.py     corner split + optimal line/arc segmentation, least-squares joints, radii snapped to the drawing's
           radii only when the snapped arc still fits the trace, left half mirrored for exact symmetry
build.py   10 mm plate + 3 mm raised main panel + through-cuts -> stage3 cad_plan -> SolidWorks
```

```bash
python -m tellis drawing --image input/ironman_mask.jpg --thickness 10 --relief 3
```

Outputs in `tellis/output/drawing-<name>/`: `overlay.png` (the fit drawn over the drawing: check it first),
`entities.json` (every line/arc; `snapped_to_dimension` marks radii taken from the drawing), `dimensions.json`,
`cad_plan.json`, `cad_result.json`, `cad/part.SLDPRT|STEP|STL`, `sw_*.png`.

Limits today: a single view becomes a flat part (thickness is your input); lengths are measured from the
trace (radii are snapped, linear dimensions are only checked); lines and arcs only (no splines); the SolidWorks
sketch has geometry but no driving dimensions yet.
