#!/usr/bin/env python3
"""Exact closed-mesh containment for CRM fill metrics.

The optional fill-detector STEP is converted to a watertight, metre-scale OBJ
in the drum's local frame (local +Z is the drum axis).  At runtime it is
transformed by the excavator's full position and rotation, so it behaves as the
negative of the physical interior.  SPH particle centres are classified with
two exact geometric tests:

* odd ray/parity crossings against the closed triangle surface;
* no triangle closer than the SPH particle radius ``rp``.

Together those tests keep the existing "whole particle inside" rule: a centre
must be inside the detector and at least ``rp`` away from its boundary.  Because
a rotating non-axisymmetric cavity would otherwise flag the same fixed soil as
fill once per revolution, an :class:`ExcavatedState` latch makes capture
monotonic: a particle stays counted while elevated once it has entered the
mesh, and clears when it settles back to the bed.  The detector never changes
which particles the solver creates, SPH/BCE bookkeeping, or the capacity volume.

Only NumPy is imported at module load time.  Point queries use a small in-tree
AABB BVH so a fine STEP-derived surface stays practical at CRM frame rates.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np

# A fixed, slightly oblique direction avoids ray/vertex and ray/edge hits on
# axis-aligned CAD surfaces while keeping the parity test deterministic.
RAY_DIRECTION = np.array([1.0, 1.0e-6, 1.0e-5], dtype=np.float64)
RAY_DIRECTION /= np.linalg.norm(RAY_DIRECTION)

_MERGE_GRID = 1.0e-9  # 1 nm; STL/OBJ export precision is already ~1 nm here
_LEAF_SIZE = 16
_RAY_EPS = 1.0e-12


class FillSurfaceError(ValueError):
    """Raised when a fill-detector OBJ cannot be used for containment."""


def load_obj(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Read an OBJ surface as ``(vertices, triangles)`` float/int arrays.

    Duplicate vertices (as written by ``stl_to_obj.py``) are merged on a 1 nm
    grid so edge/winding validation sees the geometric surface rather than the
    OBJ's per-triangle vertex duplication.
    """
    path = Path(path)
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_no, line in enumerate(handle, 1):
                parts = line.strip().split()
                if not parts or parts[0].startswith("#"):
                    continue
                if parts[0] == "v":
                    if len(parts) < 4:
                        raise FillSurfaceError(f"{path}:{line_no}: incomplete vertex")
                    vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
                elif parts[0] == "f":
                    indices: list[int] = []
                    for token in parts[1:]:
                        raw = token.split("/", 1)[0]
                        try:
                            index = int(raw)
                        except ValueError as exc:
                            raise FillSurfaceError(
                                f"{path}:{line_no}: invalid face index {token!r}"
                            ) from exc
                        if index < 0:
                            index = len(vertices) + index
                        else:
                            index -= 1
                        if index < 0 or index >= len(vertices):
                            raise FillSurfaceError(
                                f"{path}:{line_no}: face index out of range"
                            )
                        indices.append(index)
                    if len(indices) < 3:
                        raise FillSurfaceError(f"{path}:{line_no}: degenerate face")
                    for i in range(1, len(indices) - 1):
                        faces.append((indices[0], indices[i], indices[i + 1]))
    except OSError as exc:
        raise FillSurfaceError(f"cannot read fill detector OBJ {path}: {exc}") from exc

    if len(faces) < 1:
        raise FillSurfaceError(f"{path}: surface has no triangles")

    raw = np.asarray(vertices, dtype=np.float64)
    quantized = np.round(raw / _MERGE_GRID).astype(np.int64)
    unique, inverse = np.unique(quantized, axis=0, return_inverse=True)
    merged = unique.astype(np.float64) * _MERGE_GRID
    tri = np.asarray(faces, dtype=np.int64)
    tri = inverse[tri]
    keep = (tri[:, 0] != tri[:, 1]) & (tri[:, 1] != tri[:, 2]) & (tri[:, 0] != tri[:, 2])
    tri = tri[keep]
    if len(tri) < 1:
        raise FillSurfaceError(f"{path}: surface collapsed to no triangles")
    return merged, tri


def validate_watertight(vertices: np.ndarray, faces: np.ndarray) -> None:
    """Require a closed, consistently wound triangle surface."""
    edges = np.concatenate((
        faces[:, [0, 1]],
        faces[:, [1, 2]],
        faces[:, [2, 0]],
    ), axis=0)
    keys = np.sort(edges, axis=1)
    _unique, inverse, counts = np.unique(
        keys, axis=0, return_inverse=True, return_counts=True
    )
    if np.any(counts != 2):
        bad = int(np.count_nonzero(counts != 2))
        raise FillSurfaceError(
            f"fill detector surface is not watertight ({bad} open/non-manifold edges)"
        )
    # A closed orientable surface uses each directed edge exactly once.  This
    # catches mixed face winding, which would corrupt ray/parity classification.
    direction = (edges[:, 0] < edges[:, 1]).astype(np.int64)
    codes = inverse * 2 + direction
    _codes, dir_counts = np.unique(codes, return_counts=True)
    if np.any(dir_counts != 1):
        bad = int(np.count_nonzero(dir_counts != 1))
        raise FillSurfaceError(
            f"fill detector surface has inconsistent face winding ({bad} edges)"
        )


class _Bvh:
    """Minimal midpoint BVH over triangles, with per-leaf vectorized queries."""

    def __init__(self, vertices: np.ndarray, faces: np.ndarray) -> None:
        tri = vertices[np.asarray(faces, dtype=np.int64)]
        self.tri = np.ascontiguousarray(tri, dtype=np.float64)
        self.a = np.ascontiguousarray(self.tri[:, 0])
        self.e1 = np.ascontiguousarray(self.tri[:, 1] - self.a)
        self.e2 = np.ascontiguousarray(self.tri[:, 2] - self.a)
        self.pvec = np.cross(RAY_DIRECTION, self.e2)
        self.det = np.einsum("ij,ij->i", self.e1, self.pvec)
        self.inv_det = np.zeros_like(self.det)
        np.divide(1.0, self.det, out=self.inv_det, where=np.abs(self.det) > 1.0e-30)

        self.bounds: list[np.ndarray] = []
        self.left: list[int] = []
        self.right: list[int] = []
        self.start: list[int] = []
        self.count: list[int] = []

        order = np.arange(len(self.tri), dtype=np.int64)
        self._build(order)

    def _add_node(self, order: np.ndarray) -> int:
        points = self.tri[order]
        lo = np.min(points.reshape(-1, 3), axis=0)
        hi = np.max(points.reshape(-1, 3), axis=0)
        index = len(self.bounds)
        self.bounds.append(np.stack((lo, hi)))
        self.left.append(-1)
        self.right.append(-1)
        self.start.append(-1)
        self.count.append(-1)
        return index

    def _build(self, order: np.ndarray) -> int:
        index = self._add_node(order)
        if len(order) <= _LEAF_SIZE:
            self.start[index] = int(order[0])
            self.count[index] = len(order)
            return index

        centers = self.tri[order].mean(axis=1)
        lo = self.bounds[index][0]
        hi = self.bounds[index][1]
        axis = int(np.argmax(hi - lo))
        split = len(order) // 2
        partitioned = np.argpartition(centers[:, axis], split - 1)
        left_order = order[partitioned[:split]]
        right_order = order[partitioned[split:]]
        if len(left_order) == 0 or len(right_order) == 0:  # pragma: no cover
            self.start[index] = int(order[0])
            self.count[index] = len(order)
            return index
        self.left[index] = self._build(left_order)
        self.right[index] = self._build(right_order)
        return index

    def _ray_node_hits(self, points: np.ndarray, node_ids: np.ndarray) -> np.ndarray:
        bounds = np.asarray(self.bounds, dtype=np.float64)[node_ids]
        lo, hi = bounds[:, 0], bounds[:, 1]
        t0 = (lo - points) / RAY_DIRECTION
        t1 = (hi - points) / RAY_DIRECTION
        near = np.minimum(t0, t1)
        far = np.maximum(t0, t1)
        t_enter = np.max(near, axis=1)
        t_exit = np.min(far, axis=1)
        return t_exit >= np.maximum(t_enter, 0.0)

    def _near_node_hits(self, points: np.ndarray, node_ids: np.ndarray,
                        radius: float) -> np.ndarray:
        """Whether each point is closer than ``radius`` to a node's AABB."""
        bounds = np.asarray(self.bounds, dtype=np.float64)[node_ids]
        lo, hi = bounds[:, 0], bounds[:, 1]
        delta = np.maximum(np.maximum(lo - points, points - hi), 0.0)
        return np.einsum("ij,ij->i", delta, delta) < radius * radius

    def _ray_leaf_odd(self, points: np.ndarray, node_id: int) -> np.ndarray:
        start = self.start[node_id]
        count = self.count[node_id]
        a = self.a[start:start + count]
        e1 = self.e1[start:start + count]
        pvec = self.pvec[start:start + count]
        inv_det = self.inv_det[start:start + count]
        e2 = self.e2[start:start + count]

        tvec = points[:, None, :] - a[None, :, :]
        u = np.einsum("mkj,kj->mk", tvec, pvec) * inv_det[None, :]
        qvec = np.cross(tvec, e1[None, :, :])
        v = np.einsum("mkj,j->mk", qvec, RAY_DIRECTION) * inv_det[None, :]
        t = np.einsum("kj,mkj->mk", e2, qvec) * inv_det[None, :]
        hits = (
            (np.abs(inv_det)[None, :] > 0.0)
            & (u >= -1.0e-12) & (v >= -1.0e-12)
            & (u + v <= 1.0 + 1.0e-12)
            & (t > _RAY_EPS)
        )
        return (hits.sum(axis=1) % 2) == 1

    @staticmethod
    def _point_triangle_dist2(points: np.ndarray, a: np.ndarray, b: np.ndarray,
                              c: np.ndarray) -> np.ndarray:
        """Squared distance from each point to each triangle."""
        ab = b - a
        ac = c - a
        ap = points[:, None, :] - a[None, :, :]
        d1 = np.einsum("mkj,kj->mk", ap, ab)
        d2 = np.einsum("mkj,kj->mk", ap, ac)

        bp = points[:, None, :] - b[None, :, :]
        d3 = np.einsum("mkj,kj->mk", bp, ab)
        d4 = np.einsum("mkj,kj->mk", bp, ac)

        cp = points[:, None, :] - c[None, :, :]
        d5 = np.einsum("mkj,kj->mk", cp, ab)
        d6 = np.einsum("mkj,kj->mk", cp, ac)

        # Project onto the triangle plane and test the barycentric coordinates.
        # If the projection is outside, the closest point lies on one of the
        # three edges; picking the nearest segment keeps the distance exact.
        normal = np.cross(ab, ac)
        normal2 = np.einsum("kj,kj->k", normal, normal)
        normal = np.broadcast_to(normal, ap.shape)
        normal2 = np.broadcast_to(normal2[None, :], ap.shape[:2])

        d00 = np.einsum("kj,kj->k", ab, ab)
        d01 = np.einsum("kj,kj->k", ab, ac)
        d11 = np.einsum("kj,kj->k", ac, ac)
        d20 = np.einsum("mkj,kj->mk", ap, ab)
        d21 = np.einsum("mkj,kj->mk", ap, ac)
        denom = d00[None, :] * d11[None, :] - d01[None, :] ** 2
        safe_denom = np.where(np.abs(denom) > 1.0e-30, denom, 1.0)
        u = (d11[None, :] * d20 - d01[None, :] * d21) / safe_denom
        v = (d00[None, :] * d21 - d01[None, :] * d20) / safe_denom
        in_triangle = (u >= -1.0e-12) & (v >= -1.0e-12) & (u + v <= 1.0 + 1.0e-12)
        in_plane = in_triangle & (normal2 > 1.0e-30)
        plane_point = (
            a[None, :, :]
            + u[..., None] * np.broadcast_to(ab, ap.shape)
            + v[..., None] * np.broadcast_to(ac, ap.shape)
        )

        def segment_point(start: np.ndarray, end: np.ndarray) -> np.ndarray:
            edge = end - start
            edge2 = np.einsum("kj,kj->k", edge, edge)
            safe_edge2 = np.where(edge2 > 1.0e-30, edge2, 1.0)
            t = np.einsum(
                "mkj,kj->mk", points[:, None, :] - start[None, :, :], edge
            ) / safe_edge2[None, :]
            t = np.clip(t, 0.0, 1.0)
            return start[None, :, :] + t[..., None] * edge[None, :, :]

        edge_ab = segment_point(a, b)
        edge_ac = segment_point(a, c)
        edge_bc = segment_point(b, c)
        edge_dist2 = [
            np.einsum("mkj,mkj->mk", points[:, None, :] - edge, points[:, None, :] - edge)
            for edge in (edge_ab, edge_ac, edge_bc)
        ]
        best_edge = np.minimum(np.minimum(edge_dist2[0], edge_dist2[1]), edge_dist2[2])
        best_index = np.argmin(np.stack(edge_dist2, axis=0), axis=0)
        edge_point = np.select(
            [(best_index == 0)[..., None], (best_index == 1)[..., None]],
            [edge_ab, edge_ac],
            default=edge_bc,
        )
        closest = np.where(in_plane[..., None], plane_point, edge_point)
        delta = points[:, None, :] - closest
        return np.einsum("mkj,mkj->mk", delta, delta)

    def _near_leaf(self, points: np.ndarray, node_id: int,
                   margin2: float) -> np.ndarray:
        start = self.start[node_id]
        count = self.count[node_id]
        tri = self.tri[start:start + count]
        dist2 = self._point_triangle_dist2(points, tri[:, 0], tri[:, 1], tri[:, 2])
        return np.any(dist2 < margin2, axis=1)

    def parity_counts(self, points: np.ndarray) -> np.ndarray:
        """Number of surface crossings for one fixed ray from each point."""
        points = np.ascontiguousarray(points, dtype=np.float64)
        if points.ndim != 2 or points.shape[1] != 3:
            raise ValueError("points must be an (N, 3) array")
        counts = np.zeros(len(points), dtype=np.int64)
        point_ids = np.arange(len(points), dtype=np.int64)
        if not len(point_ids):
            return counts
        stack = [(point_ids, np.zeros(len(point_ids), dtype=np.int64))]
        while stack:
            pids, nids = stack.pop()
            hit = self._ray_node_hits(points[pids], nids)
            pids, nids = pids[hit], nids[hit]
            if not len(nids):
                continue
            leaves = np.asarray([self.count[int(n)] >= 0 for n in nids], dtype=bool)
            if np.any(leaves):
                for node_id in np.unique(nids[leaves]):
                    subset = pids[nids == node_id]
                    counts[subset[self._ray_leaf_odd(points[subset], int(node_id))]] += 1
            internals = ~leaves
            if np.any(internals):
                ipids = pids[internals]
                inids = nids[internals]
                left = np.asarray([self.left[int(n)] for n in inids], dtype=np.int64)
                right = np.asarray([self.right[int(n)] for n in inids], dtype=np.int64)
                stack.append((ipids, right))
                stack.append((ipids, left))
        return counts

    def points_near_surface(self, points: np.ndarray, margin: float) -> np.ndarray:
        """Boolean mask for points closer than ``margin`` to any triangle."""
        points = np.ascontiguousarray(points, dtype=np.float64)
        if points.ndim != 2 or points.shape[1] != 3:
            raise ValueError("points must be an (N, 3) array")
        near = np.zeros(len(points), dtype=bool)
        point_ids = np.arange(len(points), dtype=np.int64)
        if not len(point_ids) or margin <= 0.0:
            return near
        margin2 = float(margin) ** 2
        stack = [(point_ids, np.zeros(len(point_ids), dtype=np.int64))]
        while stack:
            pids, nids = stack.pop()
            active = ~near[pids]
            pids, nids = pids[active], nids[active]
            if not len(pids):
                continue
            hit = self._near_node_hits(points[pids], nids, float(margin))
            pids, nids = pids[hit], nids[hit]
            if not len(nids):
                continue
            leaves = np.asarray([self.count[int(n)] >= 0 for n in nids], dtype=bool)
            if np.any(leaves):
                for node_id in np.unique(nids[leaves]):
                    mask = nids == node_id
                    subset = pids[mask]
                    near[subset[self._near_leaf(points[subset], int(node_id),
                                                margin2)]] = True
            internals = ~leaves
            if np.any(internals):
                ipids = pids[internals]
                inids = nids[internals]
                left = np.asarray([self.left[int(n)] for n in inids], dtype=np.int64)
                right = np.asarray([self.right[int(n)] for n in inids], dtype=np.int64)
                stack.append((ipids, right))
                stack.append((ipids, left))
        return near


class ExcavatedState:
    """Monotonic per-particle excavation latch for a rotating detector mesh.

    The supplied mesh is the negative of the drum interior and follows the
    excavator pose exactly.  A fixed soil particle can therefore enter and
    leave that cavity once per revolution.  A particle is captured the first
    time it is both inside the eroded mesh and lifted above the initial bed,
    then stays counted until it settles back to the bed.  This removes the
    rotating fill/non-fill sweep without changing the mesh or adding any
    spatial region outside the supplied surface.
    """

    def __init__(self) -> None:
        self._seen = np.zeros(0, dtype=bool)

    def update(self, inside_mesh: np.ndarray, lifted: np.ndarray) -> np.ndarray:
        inside = np.asarray(inside_mesh, dtype=bool)
        elevated = np.asarray(lifted, dtype=bool)
        if inside.shape != elevated.shape:
            raise ValueError("inside_mesh and lifted must have the same shape")
        if self._seen.shape != inside.shape:
            self._seen = np.zeros(inside.shape, dtype=bool)
        # Settled soil leaves the excavated set; capture is otherwise permanent.
        self._seen &= elevated
        self._seen |= inside & elevated
        return self._seen.copy()

    def reset(self) -> None:
        self._seen = np.zeros(0, dtype=bool)


class FillDetector:
    """Closed-surface particle containment in the drum-local frame.

    The detector owns its :class:`ExcavatedState` so the monotonic latch is used
    by every caller of ``compute_metrics``.  Chrono's SPH marker array keeps a
    fixed particle count and stable index order for the whole run; the latch is
    therefore keyed by that index.  No particle IDs are exposed by the Python
    API, and re-ordering the marker array would be a breaking Chrono change.
    """

    def __init__(self, vertices: np.ndarray, faces: np.ndarray,
                 bvh: _Bvh | None = None) -> None:
        if bvh is None:
            validate_watertight(vertices, faces)
            bvh = _Bvh(vertices, faces)
        self.vertices = vertices
        self.faces = faces
        self.bvh = bvh
        self.excavated_state = ExcavatedState()

    def with_fresh_state(self) -> "FillDetector":
        """Return a detector sharing the cached BVH but with a new latch.

        Geometry caching is important for a fine STEP-derived surface, but a
        cached mutable latch would leak between independent ``compute_metrics``
        calls.  A driver that needs persistence keeps the returned detector's
        ``excavated_state`` and passes it on every frame.
        """
        return FillDetector(self.vertices, self.faces, self.bvh)

    def contains_with_margin(self, points: np.ndarray, margin: float = 0.0) -> np.ndarray:
        """True where a whole sphere of radius ``margin`` fits inside the surface."""
        inside = (self.bvh.parity_counts(points) % 2) == 1
        if margin > 0.0:
            inside &= ~self.bvh.points_near_surface(points, margin)
        return inside

    def excavated_mask(self, points: np.ndarray, margin: float,
                       lifted: np.ndarray,
                       state: ExcavatedState | None = None) -> np.ndarray:
        """Latched excavated set for particles inside this rotating surface."""
        active = self.excavated_state if state is None else state
        return active.update(self.contains_with_margin(points, margin), lifted)

    def reset_excavation(self) -> None:
        """Forget prior captures (for a new run reusing a cached detector)."""
        self.excavated_state.reset()


@lru_cache(maxsize=8)
def _load_cached(path: str, signature: tuple[int, int]) -> FillDetector:
    vertices, faces = load_obj(Path(path))
    return FillDetector(vertices, faces)


def load_fill_detector(path: Path | str | None) -> FillDetector | None:
    """Load and cache a detector OBJ; ``None`` keeps the analytic detector."""
    if path is None or str(path).strip() == "":
        return None
    obj = Path(path).expanduser().resolve()
    if not obj.is_file():
        raise FillSurfaceError(f"fill detector OBJ not found: {obj}")
    stat = obj.stat()
    try:
        # Only geometry is cached.  Each load gets a fresh excavation latch so
        # unrelated callers cannot inherit another run's captured particles.
        return _load_cached(str(obj), (stat.st_mtime_ns, stat.st_size)).with_fresh_state()
    except FillSurfaceError:
        _load_cached.cache_clear()
        raise


def detector_definition(detector: FillDetector | None) -> str:
    return (
        "exact ray/parity containment in the STEP detector OBJ following the "
        "full excavator pose, eroded by one SPH particle radius, with monotonic "
        "excavation latching and a lift gate"
        if detector is not None
        else "nominal cylinder eroded by one SPH particle radius, intersected "
             "with the lift gate"
    )
