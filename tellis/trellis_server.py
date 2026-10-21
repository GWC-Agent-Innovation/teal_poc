"""Minimal TRELLIS.2 HTTP server for the GPU machine.

    pip install fastapi uvicorn python-multipart   (plus TRELLIS.2 itself)
    python trellis_server.py --host 0.0.0.0 --port 7960      # copy this file + trellis_client.py
    # on the Tellis machine:  TRELLIS_URL=http://<gpu-host>:7960

POST /generate (multipart: image, seed) -> model/gltf-binary
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

try:
    from .trellis_client import LocalTrellis, TrellisError
except ImportError:  # running as a standalone script
    from trellis_client import LocalTrellis, TrellisError  # type: ignore[no-redef]

app = FastAPI(title="TRELLIS.2 server")
_model = LocalTrellis()


@app.get("/health")
def health() -> dict:
    return {"ok": True, "model": _model.model, "loaded": LocalTrellis._pipeline is not None}


@app.post("/generate")
def generate(image: UploadFile = File(...), seed: int = Form(0)) -> Response:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / (Path(image.filename or "image.png").name)
        src.write_bytes(image.file.read())
        try:
            out = _model.generate(src, Path(tmp) / "mesh.glb", seed=seed)
        except TrellisError as exc:
            raise HTTPException(500, str(exc)) from exc
        return Response(out.read_bytes(), media_type="model/gltf-binary")


if __name__ == "__main__":
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=7960)
    ap.add_argument("--model", default="microsoft/TRELLIS.2-4B")
    a = ap.parse_args()
    _model.model = a.model
    uvicorn.run(app, host=a.host, port=a.port)
