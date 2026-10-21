"""Orthographic renders of the exported STL (part frame) for side-by-side comparison with the drawing.

Each view is a true 2D projection: shaded faces painted back-to-front plus the model's feature edges
(silhouette/sharp edges only, not the triangulation), cropped tightly to the part.
"""
from __future__ import annotations

import logging
import struct
from pathlib import Path

import numpy as np

log = logging.getLogger("stage5.render")

# view -> (horizontal axis vector, vertical axis vector) in the part frame (X length, Y width, Z up)
VIEWS = {
    "top": (np.array([1.0, 0, 0]), np.array([0, 1.0, 0])),
    "front": (np.array([1.0, 0, 0]), np.array([0, 0, 1.0])),
    "right": (np.array([0, 1.0, 0]), np.array([0, 0, 1.0])),
    "isometric": (np.array([0.7071, 0.7071, 0]), np.array([-0.4082, 0.4082, 0.8165])),  # viewer at (+1,-1,+1)
}
SHARP_DEG = 25.0


def load_stl_part_frame(path: Path) -> np.ndarray:
    """Binary or ASCII STL exported by SolidWorks (model Y up) -> triangles in part frame (x, y, z)."""
    data = Path(path).read_bytes()
    if data[:5].lower() == b"solid" and b"facet" in data[:400]:
        pts = [list(map(float, l.split()[1:4])) for l in data.decode(errors="ignore").splitlines()
               if l.strip().startswith("vertex")]
        tri = np.array(pts, dtype=float).reshape(-1, 3, 3)
    else:
        n = struct.unpack("<I", data[80:84])[0]
        dt = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
        tri = np.frombuffer(data[84:84 + n * 50], dtype=dt)["v"].astype(float)
    return np.stack([tri[..., 0], -tri[..., 2], tri[..., 1]], axis=-1)  # SW (X, Y, Z) -> (x, -Z, Y)


def _feature_edges(tri: np.ndarray, normals: np.ndarray) -> np.ndarray:
    """Edges shared by faces meeting at more than SHARP_DEG, plus open boundary edges."""
    verts, inv = np.unique(np.round(tri.reshape(-1, 3), 5), axis=0, return_inverse=True)
    f = inv.reshape(-1, 3)
    e = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    owner = np.tile(np.arange(len(f)), 3)
    e_sorted = np.sort(e, axis=1)
    order = np.lexsort((e_sorted[:, 1], e_sorted[:, 0]))
    e_sorted, owner = e_sorted[order], owner[order]
    keep = []
    i = 0
    cos_lim = np.cos(np.radians(SHARP_DEG))
    while i < len(e_sorted):
        j = i + 1
        while j < len(e_sorted) and (e_sorted[j] == e_sorted[i]).all():
            j += 1
        faces = owner[i:j]
        if len(faces) != 2 or np.dot(normals[faces[0]], normals[faces[1]]) < cos_lim:
            keep.append(e_sorted[i])
        i = j
    return verts[np.array(keep)] if keep else np.zeros((0, 2, 3))


def _render(tri: np.ndarray, normals: np.ndarray, edges: np.ndarray, name: str, out: Path, dims: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection, PolyCollection

    u, v = VIEWS[name]
    w = np.cross(u, v)  # towards the viewer
    p2 = np.stack([tri @ u, tri @ v], axis=-1)
    depth = (tri @ w).mean(axis=1)
    facing = normals @ w
    order = np.argsort(depth)
    shade = 0.55 + 0.4 * np.clip(np.abs(facing), 0, 1)
    colors = np.stack([0.62 * shade, 0.70 * shade, 0.78 * shade, np.ones_like(shade)], axis=1)

    lo, hi = p2.reshape(-1, 2).min(0), p2.reshape(-1, 2).max(0)
    span = hi - lo
    fig_w = 6.0
    fig_h = max(2.0, fig_w * (span[1] + 0.12 * span.max()) / (span[0] + 0.12 * span.max()))
    fig, ax = plt.subplots(figsize=(fig_w, min(fig_h, 7.5)))
    ax.add_collection(PolyCollection(p2[order], facecolors=colors[order], edgecolors=colors[order], linewidths=0.3))
    if len(edges):
        e2 = np.stack([edges @ u, edges @ v], axis=-1)
        ax.add_collection(LineCollection(e2, colors="#1f2a35", linewidths=0.8))
    pad = 0.06 * span.max()
    ax.set_xlim(lo[0] - pad, hi[0] + pad)
    ax.set_ylim(lo[1] - pad, hi[1] + pad)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title(f"{name.upper()}   {dims}", fontsize=9, loc="left")
    fig.savefig(out, dpi=130, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def render_views(stl_path: Path, out_dir: Path) -> dict[str, str]:
    tri = load_stl_part_frame(stl_path)
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    normals = n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    edges = _feature_edges(tri, normals)
    lo, hi = tri.reshape(-1, 3).min(0), tri.reshape(-1, 3).max(0)
    dims = f"{hi[0] - lo[0]:.1f} × {hi[1] - lo[1]:.1f} × {hi[2] - lo[2]:.1f} mm"
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for name in VIEWS:
        path = out_dir / f"render_{name}.png"
        _render(tri, normals, edges, name, path, dims)
        files[name] = str(path)
    log.info("Rendered %d views to %s", len(files), out_dir)
    return files
