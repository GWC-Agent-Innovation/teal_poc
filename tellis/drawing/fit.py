"""Traced loops (mm) -> exact sketch entities (lines + arcs), snapped to the drawing's numbers.

  1. segment each loop greedily into the longest runs that fit a line / an arc within `tol`
  2. least-squares fit each run; snap arc radii to the dimensioned radii, near-axis lines to exact H/V
  3. corners = intersections of neighbouring entities (tangent joins -> tangent point)
  4. symmetric parts: fit the left half only and mirror it, so the model is exactly symmetric
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.optimize import least_squares
from shapely.geometry import Polygon

from .linework import Linework, Loop


@dataclass
class Line:
    p0: np.ndarray
    p1: np.ndarray
    snapped: str = ""                     # "horizontal" / "vertical"

    kind = "line"

    def dist(self, pts: np.ndarray) -> np.ndarray:
        d = self.p1 - self.p0
        n = np.array([-d[1], d[0]]) / (np.linalg.norm(d) or 1)
        return np.abs((pts - self.p0) @ n)


@dataclass
class Arc:
    c: np.ndarray
    r: float
    p0: np.ndarray
    p1: np.ndarray
    ccw: bool
    snapped: Optional[float] = None      # dimensioned radius it was snapped to

    kind = "arc"

    def dist(self, pts: np.ndarray) -> np.ndarray:
        return np.abs(np.linalg.norm(pts - self.c, axis=1) - self.r)


Entity = Line | Arc


# ---- primitive fits ------------------------------------------------------------------------------

def _line_fit(pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Total-least-squares line: (point on line, unit direction)."""
    m = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - m)
    return m, vt[0]


def _circle_fit(pts: np.ndarray, r_fixed: Optional[float] = None, c0=None, cx_fixed: Optional[float] = None):
    A = np.c_[2 * pts, np.ones(len(pts))]
    b = (pts ** 2).sum(axis=1)
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    c = np.array(sol[:2]) if c0 is None else np.array(c0, float)
    r = math.sqrt(max(sol[2] + c @ c, 1e-9))
    if cx_fixed is not None:
        c[0] = cx_fixed

    def res(v):
        cc = np.array([cx_fixed, v[0]]) if cx_fixed is not None else v[:2]
        rr = r_fixed if r_fixed is not None else v[-1]
        return np.linalg.norm(pts - cc, axis=1) - rr

    x0 = ([c[1]] if cx_fixed is not None else list(c)) + ([] if r_fixed is not None else [r])
    v = least_squares(res, x0).x
    cc = np.array([cx_fixed, v[0]]) if cx_fixed is not None else v[:2]
    return cc, (r_fixed if r_fixed is not None else float(v[-1]))


def _ccw(pts: np.ndarray, c: np.ndarray) -> bool:
    a = np.unwrap(np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0]))
    return a[-1] > a[0]


def _fits_line(pts, tol):
    m, d = _line_fit(pts)
    n = np.array([-d[1], d[0]])
    return np.max(np.abs((pts - m) @ n)) <= tol


def _fits_arc(pts, tol, rmax, radii=(), snap_rel=0.05):
    if len(pts) < 6:
        return False
    c, r = _kasa(pts)
    if not (1.0 < r < rmax):
        return False
    if np.max(np.abs(np.linalg.norm(pts - c, axis=1) - r)) > tol:
        return False
    half = np.linalg.norm(pts[-1] - pts[0]) / 2
    dimensioned = any(abs(r - R) <= snap_rel * R for R in radii)
    if r - math.sqrt(max(r * r - half * half, 0.0)) < (0.6 if dimensioned else 2.5) * tol:
        return False      # barely curved: a line, unless its radius is one the drawing specifies
    a = np.unwrap(np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0]))
    step = np.diff(a)
    return bool(np.all(step > -1e-3) or np.all(step < 1e-3))   # monotonic sweep


# ---- segmentation --------------------------------------------------------------------------------

def _resample(pts: np.ndarray, step: float, closed: bool) -> np.ndarray:
    p = np.vstack([pts, pts[:1]]) if closed else pts
    seg = np.linalg.norm(np.diff(p, axis=0), axis=1)
    s = np.r_[0, np.cumsum(seg)]
    t = np.arange(0, s[-1] - (step * 0.5 if closed else 0), step)
    if not closed and t[-1] < s[-1]:
        t = np.r_[t, s[-1]]
    return np.c_[np.interp(t, s, p[:, 0]), np.interp(t, s, p[:, 1])]


def _kasa(pts: np.ndarray) -> tuple[np.ndarray, float]:
    A = np.c_[2 * pts, np.ones(len(pts))]
    sol, *_ = np.linalg.lstsq(A, (pts ** 2).sum(axis=1), rcond=None)
    c = sol[:2]
    return c, math.sqrt(max(sol[2] + c @ c, 1e-9))


def _corners(pts: np.ndarray, window: int = 3, min_turn_deg: float = 22.0) -> list[int]:
    """Indices of sharp corners (turning angle over +-window points), non-maximum suppressed."""
    n = len(pts)
    turn = np.zeros(n)
    for k in range(window, n - window):
        v1, v2 = pts[k] - pts[k - window], pts[k + window] - pts[k]
        turn[k] = abs(math.degrees(math.atan2(v1[0] * v2[1] - v1[1] * v2[0], v1 @ v2)))
    out = []
    for k in range(window, n - window):
        if turn[k] >= min_turn_deg and turn[k] == turn[max(0, k - window):k + window + 1].max():
            if not out or k - out[-1] > window:
                out.append(k)
    return out


def _segment(pts: np.ndarray, tol: float, rmax: float, radii=(), snap_rel=0.05) -> list[tuple[int, int, str]]:
    """Split at sharp corners, then segment each smooth piece optimally (fewest lines/arcs within tol)."""
    cuts = [0] + _corners(pts) + [len(pts) - 1]
    out: list[tuple[int, int, str]] = []
    for s0, s1 in zip(cuts, cuts[1:]):
        if s1 - s0 < 1:
            continue
        out.extend(_refine(pts, _segment_piece(pts, s0, s1, tol, rmax, radii, snap_rel)))
    return out


def _segment_piece(pts, s0, s1, tol, rmax, radii, snap_rel):
    """Dynamic programme over breakpoints: best[j] = min #segments covering s0..j (ties: prefer lines)."""
    INF = (10 ** 9, 0)
    best = {s0: (0, 0)}
    back: dict[int, tuple[int, str]] = {}
    for j in range(s0 + 1, s1 + 1):
        cand, misses = INF, 0
        for i in range(j - 1, s0 - 1, -1):
            if i not in best:
                continue
            q = pts[i:j + 1]
            kind = "line" if _fits_line(q, tol) else ("arc" if _fits_arc(q, tol, rmax, radii, snap_rel) else None)
            if kind is None:
                misses += 1
                if misses > 25 and j - i > 12:
                    break
                continue
            misses = 0
            score = (best[i][0] + 1, best[i][1] + (1 if kind == "arc" else 0))
            if score < cand:
                cand, back[j] = score, (i, kind)
        if cand == INF:                                    # nothing fits (noise): force a short line
            cand, back[j] = (best[j - 1][0] + 1, best[j - 1][1]), (j - 1, "line")
        best[j] = cand
    runs, j = [], s1
    while j > s0:
        i, kind = back[j]
        runs.append((i, j, kind))
        j = i
    return runs[::-1]


def _sse(q: np.ndarray, kind: str) -> float:
    if kind == "line" or len(q) < 4:
        m, d = _line_fit(q)
        n = np.array([-d[1], d[0]])
        return float((((q - m) @ n) ** 2).sum())
    c, r = _kasa(q)
    return float(((np.linalg.norm(q - c, axis=1) - r) ** 2).sum())


def _refine(pts: np.ndarray, runs: list[tuple[int, int, str]], reach: int = 12) -> list[tuple[int, int, str]]:
    """Move each joint between runs to where the two fits' total squared error is smallest.

    Max-deviation segmentation lets a line creep into a tangent arc by ~sqrt(2*r*tol); least squares does not."""
    runs = [list(r) for r in runs]
    for _ in range(2):
        for k in range(len(runs) - 1):
            a, b = runs[k], runs[k + 1]
            if a[2] == b[2] == "line":
                continue
            lo, hi = max(a[0] + 3, a[1] - reach), min(b[1] - 3, b[1] - 1, a[1] + reach)
            best = min(range(lo, hi + 1), default=a[1],
                       key=lambda j: _sse(pts[a[0]:j + 1], a[2]) + _sse(pts[j:b[1] + 1], b[2]))
            a[1] = b[0] = best
    return [tuple(r) for r in runs]


def _build(pts: np.ndarray, runs, radii: list[float], snap_rel: float, cx_axis: bool, tol: float = 0.9) -> list[Entity]:
    ents: list[Entity] = []
    for k, (i, j, kind) in enumerate(runs):
        q = pts[i:j + 1]
        if kind == "line" or len(q) < 6:
            m, d = _line_fit(q)
            snapped = ""
            ang = math.degrees(math.atan2(d[1], d[0])) % 180
            if min(ang, 180 - ang) < 1.5:
                d, snapped = np.array([1.0, 0.0]), "horizontal"
            elif abs(ang - 90) < 1.5:
                d, snapped = np.array([0.0, 1.0]), "vertical"
            p0 = m + ((q[0] - m) @ d) * d
            p1 = m + ((q[-1] - m) @ d) * d
            ents.append(Line(p0, p1, snapped))
        else:
            c, r = _circle_fit(q)
            # an arc that ends on the symmetry axis must be centred on it
            on_axis = cx_axis and (abs(q[0, 0]) < 0.75 or abs(q[-1, 0]) < 0.75) and abs(c[0]) < 0.2 * r
            snap = None
            for R in sorted(radii, key=lambda R: abs(r - R)):
                if abs(r - R) > snap_rel * R:
                    break
                cs, _ = _circle_fit(q, r_fixed=R, c0=c, cx_fixed=0.0 if on_axis else None)
                if np.max(np.abs(np.linalg.norm(q - cs, axis=1) - R)) <= 1.5 * tol:
                    snap, c, r = R, cs, R
                    break
            if snap is None and on_axis:
                c, r = _circle_fit(q, c0=c, cx_fixed=0.0)
            ents.append(Arc(c, r, q[0].copy(), q[-1].copy(), _ccw(q, c), snap))
    return _merge(ents)


def _merge(ents: list[Entity]) -> list[Entity]:
    out: list[Entity] = []
    for e in ents:
        if out and isinstance(e, Line) and isinstance(out[-1], Line):
            a, b = out[-1].p1 - out[-1].p0, e.p1 - e.p0
            if abs(a[0] * b[1] - a[1] * b[0]) / (np.linalg.norm(a) * np.linalg.norm(b)) < math.sin(math.radians(1.0)):
                out[-1] = Line(out[-1].p0, e.p1, out[-1].snapped or e.snapped)
                continue
        if out and isinstance(e, Arc) and isinstance(out[-1], Arc) and e.ccw == out[-1].ccw \
                and abs(e.r - out[-1].r) < 0.02 * e.r and np.linalg.norm(e.c - out[-1].c) < 0.02 * e.r:
            p = out[-1]
            out[-1] = Arc((p.c + e.c) / 2, p.snapped or e.snapped or (p.r + e.r) / 2, p.p0, e.p1, p.ccw, p.snapped or e.snapped)
            continue
        out.append(e)
    return out


def _merge_closed(ents: list[Entity]) -> list[Entity]:
    """_merge, plus the seam between the last and the first entity of a closed loop."""
    ents = _merge(ents)
    if len(ents) > 2:
        head = _merge([ents[-1], ents[0]])
        if len(head) == 1:
            ents = head + ents[1:-1]
    return ents


# ---- joints --------------------------------------------------------------------------------------

def _intersections(a: Entity, b: Entity) -> list[np.ndarray]:
    if isinstance(a, Line) and isinstance(b, Line):
        d1, d2 = a.p1 - a.p0, b.p1 - b.p0
        den = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(den) < 1e-9 * np.linalg.norm(d1) * np.linalg.norm(d2):
            return []
        t = ((b.p0 - a.p0)[0] * d2[1] - (b.p0 - a.p0)[1] * d2[0]) / den
        return [a.p0 + t * d1]
    if isinstance(a, Arc) and isinstance(b, Line):
        a, b = b, a
    if isinstance(a, Line) and isinstance(b, Arc):
        d = (a.p1 - a.p0) / np.linalg.norm(a.p1 - a.p0)
        f = a.p0 - b.c
        B, C = f @ d, f @ f - b.r ** 2
        disc = B * B - C
        foot = a.p0 - B * d                                      # closest point of the line to the centre
        if disc < 0:
            return [b.c + (foot - b.c) / np.linalg.norm(foot - b.c) * b.r]   # near-tangent: tangent point
        s = math.sqrt(disc)
        return [a.p0 + (-B - s) * d, a.p0 + (-B + s) * d]
    d = np.linalg.norm(b.c - a.c)                                 # arc / arc
    if d < 1e-9:
        return []
    x = (d * d + a.r ** 2 - b.r ** 2) / (2 * d)
    h2 = a.r ** 2 - x * x
    u = (b.c - a.c) / d
    base = a.c + x * u
    if h2 < 0:
        return [base]
    h = math.sqrt(h2)
    return [base + h * np.array([-u[1], u[0]]), base - h * np.array([-u[1], u[0]])]


def _join(ents: list[Entity], closed: bool, tol: float) -> None:
    n = len(ents)
    for k in range(n if closed else n - 1):
        a, b = ents[k], ents[(k + 1) % n]
        seen = (a.p1 + b.p0) / 2
        cands = _intersections(a, b)
        p = min(cands, key=lambda q: np.linalg.norm(q - seen)) if cands else seen
        if np.linalg.norm(p - seen) > 6 * tol:
            p = seen                                              # far intersection: keep the observed joint
        a.p1, b.p0 = p.copy(), p.copy()


def _settle_arcs(ents: list[Entity]) -> None:
    """Make every arc exactly valid: centre equidistant from its (final) endpoints at radius r."""
    for e in ents:
        if not isinstance(e, Arc):
            continue
        chord = e.p1 - e.p0
        L = np.linalg.norm(chord)
        if L < 1e-9:
            continue
        r = max(e.r, L / 2 + 1e-9)
        mid = (e.p0 + e.p1) / 2
        nrm = np.array([-chord[1], chord[0]]) / L
        h = math.sqrt(max(r * r - (L / 2) ** 2, 0.0))
        c1, c2 = mid + h * nrm, mid - h * nrm
        e.c = c1 if np.linalg.norm(c1 - e.c) <= np.linalg.norm(c2 - e.c) else c2
        e.r = r


# ---- symmetry & loops ----------------------------------------------------------------------------

def _mirror(e: Entity) -> Entity:
    m = lambda p: np.array([-p[0], p[1]])  # noqa: E731
    if isinstance(e, Line):
        return Line(m(e.p1), m(e.p0), e.snapped)
    return Arc(m(e.c), e.r, m(e.p1), m(e.p0), e.ccw, e.snapped)


def _left_chain(pts: np.ndarray) -> Optional[np.ndarray]:
    """For a loop crossing the axis exactly twice: the left-side points from one crossing to the other."""
    left = pts[:, 0] <= 0
    flips = np.nonzero(left != np.roll(left, 1))[0]
    if len(flips) != 2:
        return None
    start = next(f for f in flips if left[f])
    order = np.roll(np.arange(len(pts)), -start)
    chain = pts[order][left[order]]
    return chain


def _clip_to_axis(ents: list[Entity]) -> None:
    """The first and last entity of a half-profile must end exactly on x = 0."""
    for e, attr in ((ents[0], "p0"), (ents[-1], "p1")):
        p = getattr(e, attr)
        if isinstance(e, Line):
            d = e.p1 - e.p0
            if abs(d[0]) > 1e-9:
                t = -e.p0[0] / d[0]
                setattr(e, attr, e.p0 + t * d)
            else:
                setattr(e, attr, np.array([0.0, p[1]]))
        else:
            dy = math.sqrt(max(e.r ** 2 - e.c[0] ** 2, 0.0))
            ys = [e.c[1] + dy, e.c[1] - dy]
            setattr(e, attr, np.array([0.0, min(ys, key=lambda y: abs(y - p[1]))]))


@dataclass
class FittedLoop:
    kind: str
    entities: list[Entity]
    max_dev: float           # worst distance from the traced line to the fitted outline, mm
    mirrored: bool

    @property
    def polygon(self) -> Polygon:
        return Polygon(np.vstack([sample(e) for e in self.entities]))


def sample(e: Entity, step: float = 1.0) -> np.ndarray:
    if isinstance(e, Line):
        n = max(int(np.linalg.norm(e.p1 - e.p0) / step), 1)
        return e.p0 + np.linspace(0, 1, n, endpoint=False)[:, None] * (e.p1 - e.p0)
    a0 = math.atan2(*(e.p0 - e.c)[::-1])
    a1 = math.atan2(*(e.p1 - e.c)[::-1])
    sweep = (a1 - a0) % (2 * math.pi) if e.ccw else -((a0 - a1) % (2 * math.pi))
    if np.linalg.norm(e.p1 - e.p0) < 1e-6:                     # full circle
        sweep = 2 * math.pi if e.ccw else -2 * math.pi
    n = max(int(abs(sweep) * e.r / step), 2)
    a = a0 + np.linspace(0, 1, n, endpoint=False) * sweep
    return e.c + e.r * np.c_[np.cos(a), np.sin(a)]


def fit_loop(loop: Loop, radii: list[float], tol: float = 0.9, snap_rel: float = 0.05,
             symmetric: bool = True, partner: Optional["FittedLoop"] = None) -> FittedLoop:
    pts = Polygon(loop.pts_mm).exterior.coords
    pts = np.asarray(pts)[:-1]
    if Polygon(pts).exterior.is_ccw is False:
        pts = pts[::-1]
    rmax = 3 * max(np.ptp(pts[:, 0]), np.ptp(pts[:, 1]), 1.0)

    if partner is not None:                                   # right-hand twin of a fitted left loop
        ents = [_mirror(e) for e in reversed(partner.entities)]
        return FittedLoop(loop.kind, ents, _dev(pts, ents), True)

    dense = _resample(pts, 0.5, closed=True)              # straight edges have no inner vertices
    chain = _left_chain(dense) if symmetric else None
    if chain is not None and len(chain) > 10:
        q = _resample(chain, 1.0, closed=False)
        ents = _build(q, _segment(q, tol, rmax, radii, snap_rel), radii, snap_rel, cx_axis=True, tol=tol)
        _join(ents, closed=False, tol=tol)
        _clip_to_axis(ents)
        ents = _merge_closed(ents + [_mirror(e) for e in reversed(ents)])   # halves meeting on the axis -> one
        _settle_arcs(ents)
        return FittedLoop(loop.kind, ents, _dev(pts, ents), True)

    q = _resample(pts, 1.0, closed=True)
    # start the closed loop at its sharpest corner so no run wraps around the seam
    v1, v2 = q - np.roll(q, 3, axis=0), np.roll(q, -3, axis=0) - q
    turn = np.abs(np.arctan2(v1[:, 0] * v2[:, 1] - v1[:, 1] * v2[:, 0], (v1 * v2).sum(axis=1)))
    q = np.roll(q, -int(np.argmax(turn)), axis=0)
    q = np.vstack([q, q[:1]])
    ents = _build(q, _segment(q, tol, rmax, radii, snap_rel), radii, snap_rel, cx_axis=False, tol=tol)
    _join(ents, closed=True, tol=tol)
    _settle_arcs(ents)
    return FittedLoop(loop.kind, ents, _dev(pts, ents), False)


def _dev(pts: np.ndarray, ents: list[Entity]) -> float:
    outline = Polygon(np.vstack([sample(e, 0.5) for e in ents])).exterior
    from shapely.geometry import Point
    return float(max(outline.distance(Point(p)) for p in pts[:: max(len(pts) // 400, 1)]))


def fit_all(lw: Linework, radii: list[float], symmetric: bool = True, tol: float = 0.9) -> list[FittedLoop]:
    """Fit every loop; mirror pairs (e.g. two eyes) are fitted once on the left and mirrored."""
    loops = lw.loops
    done: dict[int, FittedLoop] = {}
    mirror_poly = lambda p: Polygon(np.c_[-p[:, 0], p[:, 1]])  # noqa: E731
    for i, lp in enumerate(loops):
        if i in done:
            continue
        partner_idx = None
        if symmetric and lp.pts_mm[:, 0].max() < 0:              # wholly on the left: find its right twin
            mp = mirror_poly(lp.pts_mm)
            for j, other in enumerate(loops):
                if j != i and other.kind == lp.kind and j not in done:
                    op = Polygon(other.pts_mm)
                    if op.is_valid and mp.is_valid and mp.intersection(op).area > 0.9 * max(mp.area, op.area):
                        partner_idx = j
                        break
        if symmetric and lp.pts_mm[:, 0].min() > 0:
            continue                                            # right twins are produced from the left
        f = fit_loop(lp, radii, tol=tol, symmetric=symmetric)
        done[i] = f
        if partner_idx is not None:
            done[partner_idx] = fit_loop(loops[partner_idx], radii, tol=tol, partner=f)
    for i, lp in enumerate(loops):                              # right-side loops that had no left twin
        if i not in done:
            done[i] = fit_loop(lp, radii, tol=tol, symmetric=False)
    return [done[i] for i in range(len(loops))]
