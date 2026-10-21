"""TRELLIS.2 image -> textured 3D mesh (GLB). Provider isolation, like src/vlm_client.py.

  local   runs microsoft/TRELLIS.2 in-process (needs the `trellis2` + `o_voxel` packages and a CUDA GPU, 24 GB+)
  remote  POSTs the image to tellis/trellis_server.py running on a GPU machine (TRELLIS_URL)
  huggingface  calls the official microsoft/TRELLIS.2 Space on Hugging Face (no local GPU; HF_TOKEN for quota)
  none    no mesh; only uploaded meshes work

This module imports nothing from the rest of the repo so it can be copied to the GPU machine on its own.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional, Protocol

log = logging.getLogger("tellis.trellis")


class TrellisError(Exception):
    pass


class TrellisClient(Protocol):
    name: str

    def generate(self, image_path: Path, out_glb: Path, seed: int = 0) -> Path: ...


class LocalTrellis:
    """In-process TRELLIS.2. The pipeline (~4B params) is loaded once and reused."""

    name = "local"
    _pipeline = None
    _lock = threading.Lock()

    def __init__(self, model: str = "microsoft/TRELLIS.2-4B", texture_size: int = 2048,
                 decimation_target: int = 300_000):
        self.model, self.texture_size, self.decimation_target = model, texture_size, decimation_target

    def _load(self):
        if LocalTrellis._pipeline is None:
            import os

            os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
            os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
            try:
                from trellis2.pipelines import Trellis2ImageTo3DPipeline
            except ImportError as exc:
                raise TrellisError("TRELLIS.2 is not installed (see https://github.com/microsoft/TRELLIS.2).") from exc
            log.info("Loading %s ...", self.model)
            p = Trellis2ImageTo3DPipeline.from_pretrained(self.model)
            p.cuda()
            LocalTrellis._pipeline = p
        return LocalTrellis._pipeline

    def generate(self, image_path: Path, out_glb: Path, seed: int = 0) -> Path:
        from PIL import Image

        with LocalTrellis._lock:  # one GPU job at a time
            pipeline = self._load()
            import o_voxel

            mesh = pipeline.run(Image.open(image_path), seed=seed)[0]
            mesh.simplify(16_777_216)  # nvdiffrast limit
            glb = o_voxel.postprocess.to_glb(
                vertices=mesh.vertices, faces=mesh.faces, attr_volume=mesh.attrs, coords=mesh.coords,
                attr_layout=mesh.layout, voxel_size=mesh.voxel_size,
                aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
                decimation_target=self.decimation_target, texture_size=self.texture_size,
                remesh=True, remesh_band=1, remesh_project=0, verbose=False,
            )
            out_glb.parent.mkdir(parents=True, exist_ok=True)
            glb.export(str(out_glb))
        return out_glb


class RemoteTrellis:
    name = "remote"

    def __init__(self, url: str, timeout: float = 900.0):
        if not url:
            raise TrellisError("TRELLIS_URL is not set.")
        self.url, self.timeout = url.rstrip("/"), timeout

    def generate(self, image_path: Path, out_glb: Path, seed: int = 0) -> Path:
        import httpx

        try:
            with image_path.open("rb") as f:
                r = httpx.post(f"{self.url}/generate", files={"image": (image_path.name, f)},
                               data={"seed": str(seed)}, timeout=self.timeout)
        except httpx.TimeoutException as exc:
            raise TrellisError("TRELLIS.2 server timed out (raise TRELLIS_TIMEOUT_SECONDS).") from exc
        except httpx.HTTPError as exc:
            raise TrellisError(f"Could not reach the TRELLIS.2 server at {self.url}: {exc}") from exc
        if r.status_code >= 400:
            raise TrellisError(f"TRELLIS.2 server error (HTTP {r.status_code}): {r.text[:300]}")
        if not r.content.startswith(b"glTF"):
            raise TrellisError("TRELLIS.2 server did not return a GLB file.")
        out_glb.parent.mkdir(parents=True, exist_ok=True)
        out_glb.write_bytes(r.content)
        return out_glb


class HuggingFaceTrellis:
    """The official TRELLIS.2 demo Space (ZeroGPU). The image is uploaded to Hugging Face.

    Anonymous users get almost no GPU quota; set HF_TOKEN (https://huggingface.co/settings/tokens) for more.
    Space API (one session): /start_session -> /preprocess_image -> /image_to_3d -> /extract_glb."""

    name = "huggingface"

    def __init__(self, space: str = "microsoft/TRELLIS.2", token: str = "", resolution: str = "1024",
                 decimation_target: int = 300_000, texture_size: int = 2048):
        self.space, self.token, self.resolution = space, token or None, resolution
        self.decimation_target, self.texture_size = decimation_target, texture_size

    def generate(self, image_path: Path, out_glb: Path, seed: int = 0) -> Path:
        import shutil

        from gradio_client import Client, handle_file
        from gradio_client.exceptions import AppError

        try:
            client = Client(self.space, token=self.token, verbose=False)
            client.predict(api_name="/start_session")
            prompt = client.predict(handle_file(str(image_path)), api_name="/preprocess_image")
            log.info("Space %s: image -> 3D (resolution %s)", self.space, self.resolution)
            client.predict(handle_file(prompt), seed, self.resolution, api_name="/image_to_3d")
            glb, _ = client.predict(self.decimation_target, self.texture_size, api_name="/extract_glb")
        except AppError as exc:
            msg = str(exc)
            if "quota" in msg.lower() and not self.token:
                msg += " (set HF_TOKEN in .env for a larger quota)"
            raise TrellisError(f"Hugging Face TRELLIS.2: {msg}") from exc
        except Exception as exc:  # noqa: BLE001 - network, Space sleeping/building, API changed
            raise TrellisError(f"Hugging Face Space {self.space} failed: {type(exc).__name__}: {exc}") from exc
        if not glb or not Path(glb).is_file():
            raise TrellisError("Hugging Face TRELLIS.2 returned no GLB file.")
        out_glb.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(glb, out_glb)
        return out_glb


def create_client(provider: str, url: str = "", model: str = "microsoft/TRELLIS.2-4B", timeout: float = 900.0,
                  hf_space: str = "microsoft/TRELLIS.2", hf_token: str = "") -> Optional[TrellisClient]:
    if provider == "none":
        return None
    if provider == "huggingface":
        return HuggingFaceTrellis(hf_space, hf_token)
    if provider == "remote":
        return RemoteTrellis(url, timeout)
    if provider == "local":
        return LocalTrellis(model)
    raise TrellisError(f"Unknown TRELLIS_PROVIDER '{provider}' (use huggingface, remote, local or none).")
