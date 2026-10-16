"""Stand legs: cast in the pour box itself, standing upside down on legs.

After the silicone cures it stays in the pour box shells - they fit it exactly.
To cast, the mold is turned over so the silicone's open base faces up. It rests
on the pour funnel's mouth, which carries the weight; the legs only keep it from
tipping over. So they are short, thin struts from the top of the shell to flat
feet level with the funnel's mouth (one cut planes the feet and the mouth
together), and they reach out only as far as the mold's centre of gravity needs:
it must tip at least MIN_TIP degrees before it would fall.

Each leg sits off the parting seam (on the diagonals, or in the middle of each
radial wedge) so the split never cuts through one, and starts where it is
shortest to reach its foot from the shell. With a tilt the feet are cut on an
angled plane: the mold stands tilted and air rises out of one side of the cast
as it is poured. Tilting moves the centre of gravity sideways, so the tilt is
turned to the side that needs the least leg, and the feet on the low side move
out to keep it inside the stance.
"""

import math

import bpy
import bmesh
from mathutils import Matrix, Vector

from . import util

MAX_LEAN = math.radians(60.0)    # from upright: flatter than this is a shelf, not a leg
MIN_TIP = math.radians(10.0)     # how far it must tip before it falls over
CUT = 0.3                        # the feet plane trims this much off the funnel's mouth
PAD_H = 3.0                      # foot pad thickness


def _reach(mold, centre, d, z, far):
    """How far out along the horizontal unit ``d`` from ``centre`` the mold
    reaches at height z, or None."""
    o = Vector((centre.x, centre.y, z)) + d * far
    hit, loc, _n, _i = mold.ray_cast(o, -d)
    if not hit:
        return None
    return (Vector((loc.x, loc.y, 0.0)) - Vector((centre.x, centre.y, 0.0))).dot(d)


def _world_tris(obj):
    """World vertex positions and triangle vertex indices of ``obj``'s mesh."""
    import numpy as np
    me = obj.data
    me.calc_loop_triangles()
    co = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3).astype(np.float64)
    m = np.array(obj.matrix_world, dtype=np.float64)
    co = co @ m[:3, :3].T + m[:3, 3]
    tri = np.empty(len(me.loop_triangles) * 3, dtype=np.int32)
    me.loop_triangles.foreach_get("vertices", tri)
    return co, tri.reshape(-1, 3)


def _centroid(co, tri):
    """Centre of volume of a closed mesh (the filled mold: shell, silicone and
    cast are all about as dense as one another)."""
    import numpy as np
    a, b, c = co[tri[:, 0]], co[tri[:, 1]], co[tri[:, 2]]
    v = np.einsum("ij,ij->i", a, np.cross(b, c)) / 6.0
    vol = v.sum()
    if abs(vol) < 1e-9:
        return None
    g = ((a + b + c) * v[:, None]).sum(axis=0) / (4.0 * vol)
    return Vector(g.tolist())


def _hull(pts):
    """Convex hull (counter-clockwise) of 2D points (tuples)."""
    pts = sorted(set(pts))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, hi = [], []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(hi) >= 2 and cross(hi[-2], hi[-1], p) <= 0:
            hi.pop()
        hi.append(p)
    return lo[:-1] + hi[:-1]


def _margin(hull, q):
    """Signed distance from q to the nearest edge of the hull (positive
    inside) and that edge's two end points."""
    best, edge = None, None
    for i in range(len(hull)):
        a, b = hull[i], hull[(i + 1) % len(hull)]
        ex, ey = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(ex, ey)
        if ln < 1e-9:
            continue
        s = (ex * (q[1] - a[1]) - ey * (q[0] - a[0])) / ln
        if best is None or s < best:
            best, edge = s, (a, b)
    return (best if best is not None else -1.0), edge


def leg_directions(axis, props, radial_center=None, centre=None):
    """Horizontal directions for the legs: the four diagonals between the two
    halves' seam ends, or the middle of each radial wedge."""
    n = int(getattr(props, "parts_count", 2))
    if n >= 3:
        return [Vector((math.cos(2 * math.pi * (k + 0.5) / n),
                        math.sin(2 * math.pi * (k + 0.5) / n), 0.0)) for k in range(n)]
    return [Vector((math.cos(a), math.sin(a), 0.0))
            for a in (math.pi * 0.25, math.pi * 0.75, math.pi * 1.25, math.pi * 1.75)]


def leg_size(model_height):
    """(strut radius, foot pad radius): they hold the mold upright, the funnel
    carries its weight."""
    leg_r = min(max(0.012 * model_height + 1.0, 2.0), 2.5)
    return leg_r, leg_r + 2.5


def _anchor(prof, R, fz, leg_r):
    """Where on the shell a leg to a foot ``R`` out at height ``fz`` (its pad's
    underside) is shortest: ``(z, r, length)`` or None. ``prof`` lists the
    shell's reach ``(z, r)`` along the leg's direction, top down. The leg must
    lean MAX_LEAN at most and clear the shell all the way up."""
    best = None
    for i, (za, ra) in enumerate(prof):
        if ra is None or za >= fz - 0.5:
            continue
        ar = ra - 0.8 * leg_r                    # the root sinks into the shell
        dr, dzz = R - ar, fz - za
        if abs(dr) > dzz * math.tan(MAX_LEAN):
            continue
        ok = True
        for zb, rb in prof[:i]:
            if rb is None or zb > fz or zb < za + 2.0 * leg_r:
                continue
            if rb > ar + dr * (zb - za) / dzz - 0.5 * leg_r:
                ok = False
                break
        if not ok:
            continue
        ln = math.hypot(dr, dzz)
        cost = ln + 0.3 * abs(dr)                # a flat strut bends: prefer upright
        if best is None or cost < best[3]:
            best = (za, ar, ln, cost)
    return best[:3] if best else None


def add_stand_legs(mold, model, props, coll, axis, radial_center=None, keep_out=()):
    """Weld the stand legs onto the still-solid ``mold`` (before the cavity is
    carved, so a root that reaches into it is trimmed with it). ``model`` is the
    model the mold was built around; ``keep_out`` holds ``(x, y, r)`` discs on
    top the feet stay clear of (the funnel mouths, the channel's cup). Returns
    ``(legs_built, note, info)``; info carries the plan (``tip_deg``: how far
    the turned-over mold tips before it falls; ``struts``; ``n``: the feet
    plane's normal) or None."""
    mn, mx = util.world_bbox(mold)
    mmn, mmx = util.world_bbox(model)
    H = mmx.z - mmn.z
    if radial_center is not None and int(getattr(props, "parts_count", 2)) >= 3:
        centre = Vector((radial_center.x, radial_center.y, 0.0))
    else:
        centre = Vector(((mmn.x + mmx.x) * 0.5, (mmn.y + mmx.y) * 0.5, 0.0))
    far = (mx - mn).length + 20.0
    leg_r, pad_r = leg_size(H)
    tilt = math.radians(max(min(getattr(props, "stand_tilt", 0.0), 30.0), 0.0))
    rise = math.tan(tilt)

    co, tri = _world_tris(mold)
    g = _centroid(co, tri)
    if g is None:
        return 0, "stand legs skipped - the shell is not a closed solid", None
    dirs = leg_directions(axis, props, radial_center)
    dz = max(min(H / 80.0, 2.0), 0.75)
    zs = []
    z = mx.z
    while z > mmn.z + 0.3 * H:
        zs.append(z)
        z -= dz
    profs = [[(z, _reach(mold, centre, d, z, far)) for z in zs] for d in dirs]
    live = [k for k in range(len(dirs)) if any(r is not None for _z, r in profs[k])]
    if len(live) < 3:
        return 0, ("stand legs skipped - the shell has no room for three legs off "
                   "the seam"), None
    # the feet stay within the mold's own footprint
    cap = max(Vector((x - centre.x, y - centre.y, 0.0)).length
              for x in (mn.x, mx.x) for y in (mn.y, mx.y))
    keep = [(Vector((x, y, 0.0)), r) for x, y, r in keep_out]
    start = []
    for d in dirs:
        R = pad_r + 2.0          # just clear of the funnel's mouth and the cup
        while R < cap and any(((centre + d * R) - c).length < r + pad_r + 1.5
                              for c, r in keep):
            R += 1.0
        start.append(R)

    def plan(tdir):
        """Feet, legs and stance for the plane rising along ``tdir`` (None:
        level). The plane touches the top of the mold - the funnel's mouth -
        CUT below that, so one cut planes the mouth and the feet together."""
        s = rise if tdir is not None else 0.0
        td = tdir if tdir is not None else Vector((1.0, 0.0, 0.0))

        def lift_of(x, y, zz):
            return zz - ((x - centre.x) * td.x + (y - centre.y) * td.y) * s
        lift = lift_of(co[:, 0], co[:, 1], co[:, 2])
        z0 = float(lift.max()) - CUT

        def plane_z(p):
            return z0 + ((p.x - centre.x) * td.x + (p.y - centre.y) * td.y) * s
        n = (Vector((0.0, 0.0, 1.0)) - td * s).normalized()
        t1 = n.orthogonal().normalized()
        t2 = n.cross(t1)

        def flat(p):
            return (round(p.dot(t1), 3), round(p.dot(t2), 3))
        touch = co[lift >= z0 - 1e-6]
        contacts = [flat(Vector(q.tolist())) for q in touch[:: max(len(touch) // 400, 1)]]
        p0 = Vector((centre.x, centre.y, z0))
        h = max((p0 - g).dot(n), 1e-6)       # centre of gravity above the table
        g2 = flat(g)

        def foot(k, R):
            xy = centre + dirs[k] * R
            return Vector((xy.x, xy.y, plane_z(xy)))

        # Feet start just clear of the funnel's mouth (and the channel's cup)
        # and move out, a step at a time, until it would have to tip MIN_TIP
        # before it falls.
        radii = {k: min(start[k], cap) for k in live}
        for _ in range(400):
            feet2 = {flat(foot(k, radii[k])): k for k in live}
            hull = _hull(contacts + list(feet2))
            margin, edge = _margin(hull, g2) if len(hull) >= 3 else (-h, None)
            tip = math.atan2(margin, h)
            if tip >= MIN_TIP:
                break
            grow = [feet2[e] for e in (edge or ()) if e in feet2]
            grow = ([k for k in grow if radii[k] < cap]
                    or [k for k in live if radii[k] < cap])
            if not grow:
                break
            for k in grow:
                radii[k] = min(radii[k] + 1.0, cap)
        struts, total = [], 0.0
        for k in live:
            # a foot no leg can reach comes back in until one can
            a = None
            while a is None and radii[k] >= start[k] - 1e-6:
                f = foot(k, radii[k])
                a = _anchor(profs[k], radii[k], f.z - PAD_H, leg_r)
                if a is None:
                    radii[k] -= 1.0
            if a is None:
                return None
            anchor = Vector((centre.x, centre.y, a[0])) + dirs[k] * a[1]
            struts.append((anchor, f, abs(radii[k] - a[1])))
            total += a[2]
        hull = _hull(contacts + [flat(f) for _a, f, _r in struts])
        margin = _margin(hull, g2)[0] if len(hull) >= 3 else -h
        tip = math.atan2(margin, h)
        return {"tdir": tdir, "z0": z0, "n": n, "t1": t1, "t2": t2, "p0": p0, "h": h,
                "tip": tip, "radii": radii, "struts": struts, "length": total}

    if tilt > 0.0:
        # any side may be the low one: take the one that needs the least leg
        cands = [plan(Vector((math.cos(a), math.sin(a), 0.0)))
                 for a in (math.pi * 0.25 * i for i in range(8))]
    else:
        cands = [plan(None)]
    cands = [c for c in cands if c is not None]
    if not cands:
        return 0, ("stand legs skipped - the shell has no room for legs that "
                   "reach their feet"), None
    best = min(cands, key=lambda c: (c["tip"] < MIN_TIP - 1e-6, c["length"]
                                     if c["tip"] >= MIN_TIP - 1e-6 else -c["tip"]))

    bak = mold.data.copy()
    isl0, nm0 = util.island_count(mold), util.nonmanifold_count(mold)
    built = 0
    s = rise if best["tdir"] is not None else 0.0
    try:
        for anchor, tip, arm in best["struts"]:
            # a strut that reaches out a long way is a lever: a little thicker
            r = min(leg_r + 0.02 * max(arm - 15.0, 0.0), leg_r * 1.4)
            if tip.z - PAD_H - anchor.z > 0.5:
                bar = util.add_tube("MF_leg", anchor, tip, r, coll, segs=16)
                util.boolean(mold, bar, 'UNION')
                util.remove_object(bar)
                knee = util.add_sphere("MF_leg", anchor, r * 1.15, coll, subdiv=2)
                util.boolean(mold, knee, 'UNION')
                util.remove_object(knee)
            # the foot pad spans the plane across its whole width, tilt or not
            lo = tip.z - PAD_H - pad_r * s
            hi = tip.z + 1.0 + pad_r * s
            pad = util.add_cone("MF_leg", Vector((tip.x, tip.y, (lo + hi) * 0.5)),
                                pad_r, pad_r, hi - lo, 'Z', coll, segments=24)
            util.boolean(mold, pad, 'UNION')
            util.remove_object(pad)
            built += 1
        # One cut for the feet and the funnel's mouth: on one plane.
        size = far * 3.0
        basis = Matrix((best["t1"], best["t2"], best["n"])).transposed().to_4x4()
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0, matrix=(
            Matrix.Translation(best["p0"] + best["n"] * (size * 0.5)) @ basis
            @ Matrix.Diagonal((size, size, size, 1.0))))
        cutter = util.new_mesh_object("MF_legcut", bm, coll)
        util.boolean(mold, cutter, 'DIFFERENCE')
        util.remove_object(cutter)
        if util.island_count(mold) > isl0 or util.nonmanifold_count(mold) > nm0:
            raise RuntimeError("legs broke the shell")
    except Exception:
        cur = mold.data
        mold.data = bak
        if cur.users == 0:
            bpy.data.meshes.remove(cur)
        for o in list(coll.objects):
            if o.name.startswith(("MF_leg",)):
                util.remove_object(o)
        return 0, "stand legs skipped - they could not be joined cleanly to this shell", None
    if bak.users == 0:
        bpy.data.meshes.remove(bak)
    how = f", feet cut at {math.degrees(tilt):.0f} degrees" if tilt > 0 else ""
    tip_deg = math.degrees(best["tip"])
    steady = (f" - it tips over at {max(tip_deg, 0.0):.0f} degrees, keep it still"
              if tip_deg < math.degrees(MIN_TIP) - 0.5 else "")
    info = {**best, "tip_deg": tip_deg, "g": g, "leg_r": leg_r, "pad_r": pad_r}
    return built, f"{built} stand legs{how}{steady}", info
