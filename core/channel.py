"""Bottom-fill pour channel: pour beside the model, fill from the base up.

Pouring from the top drapes silicone over the master and traps air on a
complex sculpt. The bottom-fill channel is a straight pipe standing on the
parting seam just outside the case, joined to it by a thin fin at every height.
At the bottom a gate opens into the cavity right above its floor, so the
silicone enters at the base and rises evenly around the model. The top funnel
(if on) becomes the riser and air escape.

The channel sits ON the parting seam - the same contoured surface the split
uses - so each half carries half of it: when the halves come apart the cured
sprue lifts out of two half-channels instead of being locked inside one half.
Its radius is its own setting: it does not depend on the silicone thickness.
"""

import math
import types

import bpy
import bmesh
from mathutils import Vector

from . import util


def seam_frame(mold, axis, props, parting=None, radial_center=None, flip=False):
    """Where the channel can go: a namespace with ``center``, the horizontal
    direction ``d`` along the seam it runs out on, the seam's normal ``n`` and
    ``point(t, z)`` - the point on the parting surface ``t`` out along ``d``
    from the centre at height ``z``. Two-piece split: either end of the seam
    (the side the model reaches out least, or the other with ``flip``); radial
    split: the first seam."""
    mn, mx = util.world_bbox(mold)
    multipart = int(getattr(props, "parts_count", 2)) >= 3
    if multipart:
        c = radial_center if radial_center is not None else (mn + mx) * 0.5
        center = Vector((c.x, c.y, 0.0))
        d = Vector((1.0, 0.0, 0.0))
        n = Vector((0.0, 1.0, 0.0))

        def point(t, z):
            return Vector((center.x + t, center.y, z))
        return types.SimpleNamespace(center=center, d=d, n=n, point=point,
                                     multipart=True, sides=(d,))
    ai = {'X': 0, 'Y': 1}[axis]
    h = next(i for i in range(3) if i != ai and i != 2)
    center = (mn + mx) * 0.5
    center.z = 0.0
    cap = (mx[ai] - mn[ai]) * 0.4
    off = max(min(getattr(props, "split_offset", 0.0), cap), -cap)
    if parting is not None:
        off = parting.off
    seam_flat = center[ai] + off
    n = Vector((0.0, 0.0, 0.0)); n[ai] = 1.0

    def make(sgn):
        d = Vector((0.0, 0.0, 0.0)); d[h] = sgn

        def point(t, z):
            p = center.copy()
            p[h] = center[h] + sgn * t
            p.z = z
            s = None
            if parting is not None:
                from . import split as _split
                s = _split.parting_at(parting, p[h], z)
            p[ai] = seam_flat if s is None else s
            return p
        return d, point

    sides = []
    for sgn in (1.0, -1.0):
        sides.append(make(sgn))
    # the side where the mold reaches out least gets the channel (shortest fin)
    reach = []
    for d, point in sides:
        r = _reach(mold, point, d, (mn.z + mx.z) * 0.5, (mx - mn).length + 10.0)
        reach.append(r if r is not None else 1e9)
    k = 0 if reach[0] <= reach[1] else 1
    if flip:
        k = 1 - k
    d, point = sides[k]
    return types.SimpleNamespace(center=center, d=d, n=n, point=point,
                                 multipart=False, ai=ai, h=h)


def _reach(obj, point, d, z, far):
    """How far out along ``d`` (from the frame centre) ``obj`` reaches at height
    z on the seam surface, or None. The ray starts on the surface far out, then
    once more from the surface position where it first hit (a contoured seam
    is not straight)."""
    t_hit = None
    o = point(far, z)
    for _ in range(3):
        hit, loc, _n, _i = obj.ray_cast(o, -d)
        if not hit:
            return t_hit
        t_new = far - (loc - o).dot(-d)
        if t_hit is not None and abs(t_new - t_hit) < 0.05:
            return t_new
        t_hit = t_new
        o = point(t_hit, z) + d * (far - t_hit)
    return t_hit


def _sweep(name, pts, r, coll, segs=32):
    """A closed round tube of radius ``r`` along the polyline ``pts`` (rings kept
    perpendicular to the line, capped at both ends)."""
    pts = [Vector(p) for p in pts]
    clean = [pts[0]]
    for p in pts[1:]:
        if (p - clean[-1]).length > 1e-4:
            clean.append(p)
    pts = clean
    if len(pts) < 2:
        return None
    tans = []
    for i in range(len(pts)):
        a = pts[max(i - 1, 0)]
        b = pts[min(i + 1, len(pts) - 1)]
        tans.append((b - a).normalized())
    ref = Vector((0.0, 0.0, 1.0))
    if abs(tans[0].dot(ref)) > 0.9:
        ref = Vector((1.0, 0.0, 0.0))
    u = tans[0].cross(ref).normalized()
    bm = bmesh.new()
    rings = []
    for i, p in enumerate(pts):
        t = tans[i]
        u = (u - t * u.dot(t))
        if u.length < 1e-6:
            u = t.orthogonal()
        u.normalize()
        v = t.cross(u)
        rings.append([bm.verts.new(p + (u * math.cos(2 * math.pi * k / segs)
                                        + v * math.sin(2 * math.pi * k / segs)) * r)
                      for k in range(segs)])
    for i in range(len(rings) - 1):
        for k in range(segs):
            kn = (k + 1) % segs
            bm.faces.new((rings[i][k], rings[i][kn], rings[i + 1][kn], rings[i + 1][k]))
    c0 = bm.verts.new(pts[0])
    c1 = bm.verts.new(pts[-1])
    for k in range(segs):
        kn = (k + 1) % segs
        bm.faces.new((rings[0][kn], rings[0][k], c0))
        bm.faces.new((rings[-1][k], rings[-1][kn], c1))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return util.new_mesh_object(name, bm, coll)


def _sheet(name, frame, rows, thick, coll):
    """A thin solid lying on the seam surface: for each height z in ``rows`` a
    span ``(z, t_in, t_out)`` along the seam, ``thick`` across it (half to each
    side, so the split halves it)."""
    K = 8
    bm = bmesh.new()
    grid = {}
    for side, sgn in ((0, -0.5), (1, 0.5)):
        for i, (z, t0, t1) in enumerate(rows):
            for k in range(K + 1):
                t = t0 + (t1 - t0) * k / K
                grid[(side, i, k)] = bm.verts.new(frame.point(t, z) + frame.n * (sgn * thick))
    m = len(rows)
    for i in range(m - 1):
        for k in range(K):
            bm.faces.new((grid[(0, i, k)], grid[(0, i, k + 1)],
                          grid[(0, i + 1, k + 1)], grid[(0, i + 1, k)]))
            bm.faces.new((grid[(1, i, k)], grid[(1, i + 1, k)],
                          grid[(1, i + 1, k + 1)], grid[(1, i, k + 1)]))
    for i in range(m - 1):                       # inner and outer edges
        for k in (0, K):
            bm.faces.new((grid[(0, i, k)], grid[(0, i + 1, k)],
                          grid[(1, i + 1, k)], grid[(1, i, k)]))
    for i in (0, m - 1):                         # bottom and top edges
        for k in range(K):
            bm.faces.new((grid[(0, i, k)], grid[(1, i, k)],
                          grid[(1, i, k + 1)], grid[(0, i, k + 1)]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return util.new_mesh_object(name, bm, coll)


def build_channel(mold, cavity, model, props, frame, floor_z, coll, open_below=False):
    """Weld the channel's pipe, fin and mouth onto the still-solid ``mold``
    (before the cavity is carved). ``floor_z`` is the lowest height the gate may
    open into the cavity (the cavity floor, a key plate's rim, a Locking Base's
    model base); ``open_below`` = the pipe runs on down through the open bottom.
    Returns the geometry the bore phase needs, or ``(None, reason)``."""
    mn, mx = util.world_bbox(mold)
    far = (mx - mn).length + 20.0
    bore_r = max(getattr(props, "channel_radius", 4.0), 1.0)
    jacket = props.box_style == 'POUR_BOX'
    wall = max(props.shell_wall if jacket else props.wall_thickness, 1.6)
    tube_out = bore_r + wall
    gap = getattr(props, "silicone_gap", props.wall_thickness)

    # The gate: just above the floor, where the cavity is at the seam.
    gate_z = floor_z + (bore_r if open_below else bore_r + 0.4)
    t_cav = None
    for _ in range(12):
        t_cav = _reach(cavity, frame.point, frame.d, gate_z, far)
        if t_cav is not None:
            break
        gate_z += 1.0
    if t_cav is None:
        return None, "the channel found no cavity to open into at the base"

    # How far out the mold reaches along the seam, height by height.
    mmn_z, mmx_z = util.world_bbox(model)[0].z, util.world_bbox(model)[1].z
    top = mmx_z + (gap if jacket else 0.0)
    z0 = gate_z - tube_out
    rows_z = []
    nz = max(int((top - z0) / 3.0), 4)
    for i in range(nz + 1):
        rows_z.append(z0 + (top - z0) * i / nz)
    reach = [(z, _reach(mold, frame.point, frame.d, z, far)) for z in rows_z]
    hits = [r for _z, r in reach if r is not None]
    if not hits:
        return None, "the channel found no mold wall on the seam"
    t_pipe = max(hits) + tube_out - 1.0           # welded 1 mm into the widest point
    shell_top = max((z for z, r in reach if r is not None), default=top)
    throat = max(top + 3.0, shell_top + 1.5)
    fh = max(getattr(props, "funnel_height", 12.0), 1.0)
    apex = throat + max(fh * 0.6, 6.0)
    mouth_r = max(bore_r * 1.8, bore_r + 3.0)

    # Pipe along the seam, from under the gate to the throat.
    steps = max(int((throat - z0) / 4.0), 2)
    pipe_pts = [frame.point(t_pipe, z0 + (throat - z0) * i / steps) for i in range(steps + 1)]
    pipe = _sweep("MF_chan", pipe_pts, tube_out, coll)
    # Fin: from the mold's surface out to the pipe at every height, so the pipe is
    # never a cantilever where the model narrows.
    fin_t = max(2.0 * wall, 3.0)
    rows = []
    for z, r in reach:
        # 3 mm into the case: between the sampled heights the surface may dip
        # in, and a fin that only touched it would leave slits (anything that
        # reaches into the cavity is carved away with it)
        t_in = (r - 3.0) if r is not None else t_pipe - tube_out
        t_in = min(t_in, t_pipe - 0.5)
        rows.append((z, t_in, t_pipe))
    rows.append((throat, t_pipe - tube_out, t_pipe))
    fin = _sheet("MF_chanfin", frame, rows, fin_t, coll)
    # Mouth: a cup above the throat to pour into.
    tp = frame.point(t_pipe, throat)
    cup = util.add_cone("MF_chancup", Vector((tp.x, tp.y, (throat + apex) * 0.5)),
                        tube_out, mouth_r + wall, apex - throat, 'Z', coll, segments=48)
    # Collar: the gate runs from the pipe to the cavity through the fin, which
    # is thinner than the gate - without a tube round it the gate would cut
    # through the fin's faces and leak out sideways.
    n_c = max(int((t_pipe - t_cav) / 2.0), 2)
    collar_pts = [frame.point(t_pipe - (t_pipe - (t_cav - 1.0)) * i / n_c, gate_z)
                  for i in range(n_c + 1)]
    collar = _sweep("MF_chancollar", collar_pts, tube_out, coll)
    elbow = util.add_sphere("MF_chancollar", frame.point(t_pipe, gate_z), tube_out, coll,
                            subdiv=3)
    for part in (pipe, fin, cup, collar, elbow):
        if part is not None:
            util.boolean(mold, part, 'UNION')
            util.remove_object(part)
    return {"frame": frame, "t_pipe": t_pipe, "t_cav": t_cav, "gate_z": gate_z,
            "z0": z0, "throat": throat, "apex": apex, "bore_r": bore_r,
            "mouth_r": mouth_r, "open_below": open_below, "floor_z": floor_z,
            "cup": (tp.x, tp.y, mouth_r + wall)}, ""


def bore_channel(mold, geo, coll):
    """Drill the channel after the cavity is carved: the pipe from the gate up
    through the mouth, and the gate along the seam into the cavity."""
    fr = geo["frame"]
    r = geo["bore_r"]
    gz = geo["gate_z"]
    tp = geo["t_pipe"]
    bottom = (geo["floor_z"] - 2.0) if geo["open_below"] else gz
    steps = max(int((geo["throat"] - bottom) / 4.0), 2)
    pts = [fr.point(tp, bottom + (geo["throat"] - bottom) * i / steps)
           for i in range(steps + 1)]
    top_pt = fr.point(tp, geo["throat"])
    pts.append(Vector((top_pt.x, top_pt.y, geo["apex"] + 1.0)))
    cutters = [_sweep("MF_chanbore", pts, r, coll)]
    # the gate: from the pipe's axis in along the seam, 2 mm into the cavity
    t_end = geo["t_cav"] - 2.0
    n = max(int((tp - t_end) / 2.0), 2)
    gpts = [fr.point(tp - (tp - t_end) * i / n, gz) for i in range(n + 1)]
    cutters.append(_sweep("MF_chanbore", gpts, r, coll))
    elbow = util.add_sphere("MF_chanbore", fr.point(tp, gz), r, coll, subdiv=3)
    cutters.append(elbow)
    cupb = util.add_cone("MF_chanbore",
                         Vector((top_pt.x, top_pt.y, (geo["throat"] + geo["apex"] + 0.5) * 0.5)),
                         r, geo["mouth_r"], geo["apex"] + 0.5 - geo["throat"], 'Z', coll,
                         segments=48)
    cutters.append(cupb)
    for c in cutters:
        if c is not None:
            util.boolean(mold, c, 'DIFFERENCE')
            util.remove_object(c)
