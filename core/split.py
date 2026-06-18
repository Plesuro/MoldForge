"""Split the mold into two halves.

Default is a *contoured* parting surface that follows the model's mid-profile
(self-registering, MoldBoxer-style), with a flat-plane fallback if the contoured
cut produces anything invalid. Flat splits also get conical alignment keys.
"""

import math

import bpy
import bmesh
from mathutils import Vector

from . import util


def split_parts(mold, master, axis, props, coll, wall, radial_center=None):
    """Split the mold body into the requested number of printable pieces.

    Two pieces use the contoured/planar parting (with keys/teeth/wings). Three or
    more use a *radial* split into wedges around the vertical axis — each wedge pulls
    straight out, so a model with undercuts on every side (where no single two-part
    parting releases) can still come free. Returns a list of parts.

    ``radial_center`` is the centre the radial clamp wings were built around; when
    given, the wedge cuts use it so every seam falls exactly on its wing (the wings
    shift the mold's bounding box, so re-deriving the centre would miss them)."""
    n = max(int(getattr(props, "parts_count", 2)), 2)
    if n <= 2:
        half_a, half_b = split(mold, master, axis, props, coll, wall)
        return [half_a, half_b]
    return _split_radial(mold, master, props, coll, wall, n, center=radial_center)


def resolve_axis(props, obj):
    """The horizontal axis ('X' or 'Y') the halves separate along.

    For ``AUTO`` we pick the pull direction the model *releases* best along — the
    one trapping fewer undercuts — and only fall back to the wider footprint when the
    two are about equal (a convex shape releases either way, so wider = neater halves).
    An explicit X/Y is honoured as-is."""
    axis = props.split_axis
    if axis != 'AUTO':
        return axis
    mn, mx = util.world_bbox(obj)
    wider = 'X' if (mx.x - mn.x) >= (mx.y - mn.y) else 'Y'
    ux = util.undercut_fraction(obj, 'X')
    uy = util.undercut_fraction(obj, 'Y')
    if abs(ux - uy) <= 0.02:
        return wider
    return 'X' if ux < uy else 'Y'


def _valid(obj):
    return util.part_is_valid(obj)[0]


def split(mold, master, axis, props, coll, wall):
    mn, mx = util.world_bbox(mold)
    ai = {'X': 0, 'Y': 1}[axis]
    # Parting plane position along the split axis (clamped so neither half vanishes).
    cap = (mx[ai] - mn[ai]) * 0.4
    off = max(min(getattr(props, "split_offset", 0.0), cap), -cap)

    half_a = half_b = None
    contoured = getattr(props, "contoured", True) and not getattr(props, "block", False)
    if contoured:
        try:
            ca, cb = _split_contoured(mold, master, axis, coll, off)
            util.remove_small_islands(ca)   # boolean slivers, not a reason to reject
            util.remove_small_islands(cb)
            if _valid(ca) and _valid(cb):
                half_a, half_b = ca, cb
            else:
                util.remove_object(ca)
                util.remove_object(cb)
        except Exception:
            for o in [x for x in list(coll.objects)
                      if x.name.startswith("MF_Mold_") or x.name.startswith("MF_part")]:
                util.remove_object(o)

    contoured_used = half_a is not None
    if not contoured_used:
        half_a, half_b = _split_planar(mold, axis, coll, off)
        util.remove_small_islands(half_a)
        util.remove_small_islands(half_b)

    util.remove_object(mold)

    if not half_a.data.polygons or not half_b.data.polygons:
        raise RuntimeError("Splitting produced an empty half.")

    # A contoured parting self-registers, and clamp wings + their bolts already
    # align a flat parting — so only add (otherwise tiny, on a thin wall) registration
    # keys when neither is in play.
    if (props.key_count > 0 and not contoured_used
            and not getattr(props, "wings", False)):
        if getattr(props, "registration", 'KEYS') == 'TEETH':
            _add_teeth(half_a, half_b, props, axis, ai, mn, mx, wall, coll, off)
        else:
            _add_keys(half_a, half_b, props, axis, ai, mn, mx, wall, coll, off)

    return half_a, half_b


def _split_planar(mold, axis, coll, off=0.0):
    mn, mx = util.world_bbox(mold)
    size = mx - mn
    center = (mn + mx) * 0.5
    ai = {'X': 0, 'Y': 1}[axis]
    big = max(size.x, size.y, size.z) * 2.0 + 10.0

    def side_cube(sign):
        c = center.copy()
        c[ai] = center[ai] + off + sign * (big * 0.5)
        return util.add_box("MF_cut", c, Vector((big, big, big)), coll)

    cube_neg = side_cube(-1.0)
    cube_pos = side_cube(+1.0)
    half_a = util.duplicate_object(mold, "MF_Mold_A", coll)
    util.boolean(half_a, cube_neg, 'DIFFERENCE')   # keep +side
    half_b = util.duplicate_object(mold, "MF_Mold_B", coll)
    util.boolean(half_b, cube_pos, 'DIFFERENCE')   # keep -side
    util.remove_object(cube_neg)
    util.remove_object(cube_pos)
    return half_a, half_b


def _split_contoured(mold, master, axis, coll, off=0.0):
    """Split with a parting surface that follows the master's mid-depth profile
    (flat outside the silhouette). half_a = +axis side, half_b = -axis side."""
    mn, mx = util.world_bbox(mold)
    cutter = _contoured_cutter(master, axis, mn, mx, coll, off)
    half_a = util.duplicate_object(mold, "MF_Mold_A", coll)
    util.boolean(half_a, cutter, 'DIFFERENCE')   # remove -axis solid -> keep +axis
    half_b = util.duplicate_object(mold, "MF_Mold_B", coll)
    util.boolean(half_b, cutter, 'INTERSECT')    # keep overlap -> -axis side
    util.remove_object(cutter)
    return half_a, half_b


def _contoured_cutter(master, axis, mn, mx, coll, off=0.0, res=48):
    """A closed solid bounded above (in +axis) by the master's mid-depth surface
    and extending far into -axis. Subtracting it keeps the +axis half. ``off`` slides
    the whole parting surface along the split axis."""
    ai = {'X': 0, 'Y': 1}[axis]
    u, v = [i for i in range(3) if i != ai]
    size = mx - mn
    big = size.length * 2.0 + 10.0
    cen = (mn[ai] + mx[ai]) * 0.5 + off
    near = cen - big
    far = cen + big
    margin = max(size[u], size[v]) * 0.2 + 1.0
    umin, umax = mn[u] - margin, mx[u] + margin
    vmin, vmax = mn[v] - margin, mx[v] + margin

    def mid_height(uu, vv):
        o1 = Vector((0.0, 0.0, 0.0)); o1[u] = uu; o1[v] = vv; o1[ai] = far
        d1 = Vector((0.0, 0.0, 0.0)); d1[ai] = -1.0
        hit1, loc1, _n1, _i1 = master.ray_cast(o1, d1)
        o2 = Vector((0.0, 0.0, 0.0)); o2[u] = uu; o2[v] = vv; o2[ai] = near
        d2 = Vector((0.0, 0.0, 0.0)); d2[ai] = 1.0
        hit2, loc2, _n2, _i2 = master.ray_cast(o2, d2)
        if hit1 and hit2:
            return (loc1[ai] + loc2[ai]) * 0.5 + off
        return cen   # outside the silhouette -> flat

    n = res + 1
    coords = [[(umin + (umax - umin) * i / res, vmin + (vmax - vmin) * j / res)
               for j in range(n)] for i in range(n)]
    H = [[mid_height(*coords[i][j]) for j in range(n)] for i in range(n)]

    # Smooth the height field so the parting is a clean wave, not a staircase.
    for _ in range(8):
        S = [row[:] for row in H]
        for i in range(1, n - 1):
            for j in range(1, n - 1):
                S[i][j] = (H[i][j] + H[i - 1][j] + H[i + 1][j]
                           + H[i][j - 1] + H[i][j + 1]) / 5.0
        H = S

    bm = bmesh.new()
    top = {}
    bot = {}
    for i in range(n):
        for j in range(n):
            uu, vv = coords[i][j]
            ct = Vector((0.0, 0.0, 0.0)); ct[u] = uu; ct[v] = vv; ct[ai] = H[i][j]
            cb = Vector((0.0, 0.0, 0.0)); cb[u] = uu; cb[v] = vv; cb[ai] = near
            top[(i, j)] = bm.verts.new(ct)
            bot[(i, j)] = bm.verts.new(cb)
    for i in range(res):
        for j in range(res):
            bm.faces.new((top[(i, j)], top[(i, j + 1)], top[(i + 1, j + 1)], top[(i + 1, j)]))
            bm.faces.new((bot[(i, j)], bot[(i + 1, j)], bot[(i + 1, j + 1)], bot[(i, j + 1)]))
    for i in range(res):
        bm.faces.new((top[(i, 0)], top[(i + 1, 0)], bot[(i + 1, 0)], bot[(i, 0)]))
        bm.faces.new((top[(i, res)], bot[(i, res)], bot[(i + 1, res)], top[(i + 1, res)]))
    for j in range(res):
        bm.faces.new((top[(0, j)], bot[(0, j)], bot[(0, j + 1)], top[(0, j + 1)]))
        bm.faces.new((top[(res, j)], top[(res, j + 1)], bot[(res, j + 1)], bot[(res, j)]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return util.new_mesh_object("MF_part", bm, coll)


def _add_teeth(half_a, half_b, props, axis, ai, mn, mx, wall, coll, off=0.0):
    """Interlocking castellation along the seam: a row of square teeth raised on
    half_a that seat into matching pockets in half_b — stronger anti-slip than round
    pins. Each tooth is kept only where it actually overlaps the wall, so none float."""
    center = (mn + mx) * 0.5
    h = next(i for i in range(3) if i != ai and i != 2)
    seam = center[ai] + off
    tooth = max(wall * 0.8, 0.6)
    depth = max(wall * 0.5, 0.4)
    n = max(props.key_count * 2, 4)
    for k in range(n):
        if k % 2 == 0:                        # castellate: tooth on every other slot
            continue
        hh = mn[h] + (mx[h] - mn[h]) * (k + 0.5) / n
        c = center.copy(); c[ai] = seam; c[h] = hh; c[2] = center.z
        size = Vector((0.0, 0.0, 0.0))
        size[ai] = depth * 2.0; size[h] = tooth; size[2] = tooth * 2.0
        box = util.add_box("MF_tooth", c, size, coll)
        probe = util.duplicate_object(box, "MF_ttest", coll)
        util.boolean(probe, half_a, 'INTERSECT')
        inside = bool(probe.data.polygons)
        util.remove_object(probe)
        if inside:
            socket = util.duplicate_object(box, "MF_tsock", coll)
            util.boolean(half_a, box, 'UNION')        # raise the tooth on A
            util.boolean(half_b, socket, 'DIFFERENCE')  # matching pocket in B
            util.remove_object(socket)
        util.remove_object(box)


def _add_keys(half_a, half_b, props, axis, ai, mn, mx, wall, coll, off=0.0):
    """Conical pins on half_a's parting face that seat into sockets in half_b.

    For each candidate position we ray-cast half_a to find where the wall
    actually is and seat the key half a wall-thickness inside it. Positions with
    no wall are skipped, so a key never ends up floating as a disconnected island.
    """
    center = (mn + mx) * 0.5
    half_z = (mx.z - mn.z) * 0.5
    h = next(i for i in range(3) if i != ai and i != 2)
    reach = max(mx.x - mn.x, mx.y - mn.y, mx.z - mn.z) + 10.0

    key_r = min(props.key_radius, max(wall * 0.45, 0.2))
    seat = props.key_depth * 0.5
    depth = props.key_depth + seat
    center_ai = center[ai] + off + (-props.key_depth + seat) * 0.5
    probe_ai = center[ai] + off + min(seat * 0.5, half_z * 0.5)

    layouts = {
        1: [(1.0, 0.0)],
        2: [(1.0, 0.0), (-1.0, 0.0)],
        3: [(1.0, 0.4), (1.0, -0.4), (-1.0, 0.0)],
        4: [(1.0, 0.4), (1.0, -0.4), (-1.0, 0.4), (-1.0, -0.4)],
    }
    plan = layouts.get(min(props.key_count, 4), layouts[4])

    placed = 0
    for h_sign, z_frac in plan:
        z = center.z + z_frac * half_z
        origin = center.copy()
        origin[ai] = probe_ai
        origin[2] = z
        origin[h] = center[h] + h_sign * reach
        direction = Vector((0.0, 0.0, 0.0))
        direction[h] = -h_sign

        hit, loc, _normal, _idx = half_a.ray_cast(origin, direction)
        if not hit:
            continue

        c = center.copy()
        c[ai] = center_ai
        c[2] = z
        c[h] = loc[h] - h_sign * wall * 0.5

        pin = util.add_cone(f"MF_key_{placed}", c, key_r * 0.45, key_r, depth, axis, coll)
        socket = util.duplicate_object(pin, f"MF_key_socket_{placed}", coll)
        util.boolean(half_a, pin, 'UNION')
        util.remove_object(pin)
        util.boolean(half_b, socket, 'DIFFERENCE')
        util.remove_object(socket)
        placed += 1


def cut_horizontal(parts, coll, hz):
    """Horizontal Split: cut every piece into a Top and Bottom stack at height
    ``hz`` (each prints shorter — the XL-mold workflow). The bolted flange ring
    welded on before the vertical split provides the mating lip on both sides of
    the seam. A piece lying entirely on one side is kept as-is."""
    out = []
    for part in parts:
        mn, mx = util.world_bbox(part)
        if hz <= mn.z + 0.2 or hz >= mx.z - 0.2:
            out.append(part)
            continue
        size = mx - mn
        big = max(size.x, size.y, size.z) * 2.0 + 10.0
        cen = (mn + mx) * 0.5
        top = util.duplicate_object(part, part.name + "_Top", coll)
        below = util.add_box("MF_hcut", Vector((cen.x, cen.y, hz - big * 0.5)),
                             Vector((big, big, big)), coll)
        util.boolean(top, below, 'DIFFERENCE')
        util.remove_object(below)
        above = util.add_box("MF_hcut", Vector((cen.x, cen.y, hz + big * 0.5)),
                             Vector((big, big, big)), coll)
        util.boolean(part, above, 'DIFFERENCE')
        util.remove_object(above)
        part.name = part.name + "_Bot"
        util.remove_small_islands(part)
        util.remove_small_islands(top)
        out.extend([part, top])
    return out


# --- Radial multi-part split (3-4 wedges around the vertical axis) ---------- #

def _split_radial(mold, master, props, coll, wall, n, center=None):
    """Carve the mold into ``n`` equal wedges around the vertical axis through its
    centre. Each wedge is the mold intersected with a pie-slice prism; intersecting
    two watertight solids keeps each wedge watertight. Adjacent wedges then get a
    best-effort registration pin straddling their shared seam.

    ``center`` defaults to the (winged) mold's bbox centre, but the caller passes the
    clamp wings' centre so the seams cut exactly through the wings."""
    mn, mx = util.world_bbox(mold)
    if center is None:
        center = (mn + mx) * 0.5
    big = max(mx.x - mn.x, mx.y - mn.y) * 2.0 + 10.0
    z0, z1 = mn.z - 1.0, mx.z + 1.0
    labels = "ABCDEFGH"

    parts = []
    for k in range(n):
        a0 = 2.0 * math.pi * k / n
        a1 = 2.0 * math.pi * (k + 1) / n
        cutter = _pie_prism(center, big, z0, z1, a0, a1, coll)
        part = util.duplicate_object(mold, f"MF_Mold_{labels[k]}", coll)
        util.boolean(part, cutter, 'INTERSECT')
        util.remove_object(cutter)
        util.remove_small_islands(part)
        parts.append(part)
    util.remove_object(mold)

    for part in parts:
        if not part.data.polygons:
            raise RuntimeError("Radial split produced an empty wedge.")

    # Winged seams register via the bolted flange lips, so pins are only needed
    # when wings are off (mirrors the two-part rule).
    if props.key_count > 0 and not getattr(props, "wings", False):
        _add_radial_keys(parts, center, mn, mx, props, wall, coll, n)
    return parts


def _pie_prism(center, big, z0, z1, a0, a1, coll):
    """A triangular vertical prism spanning the angular sector [a0, a1] around the
    vertical axis through ``center``. The sector is < 180 deg (n >= 3), so a triangle
    from the axis out to two far points (radius ``big`` >> mold) covers every mold
    point in that wedge; the straight chord falls well outside the mold."""
    cx, cy = center.x, center.y
    p0 = (cx + big * math.cos(a0), cy + big * math.sin(a0))
    p1 = (cx + big * math.cos(a1), cy + big * math.sin(a1))
    bm = bmesh.new()
    ab = bm.verts.new((cx, cy, z0))
    ab0 = bm.verts.new((p0[0], p0[1], z0))
    ab1 = bm.verts.new((p1[0], p1[1], z0))
    at = bm.verts.new((cx, cy, z1))
    at0 = bm.verts.new((p0[0], p0[1], z1))
    at1 = bm.verts.new((p1[0], p1[1], z1))
    bm.faces.new((ab, ab1, ab0))          # bottom
    bm.faces.new((at, at0, at1))          # top
    bm.faces.new((ab, ab0, at0, at))      # side along a0
    bm.faces.new((ab0, ab1, at1, at0))    # outer arc chord
    bm.faces.new((ab1, ab, at, at1))      # side along a1
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return util.new_mesh_object("MF_pie", bm, coll)


def _add_radial_keys(parts, center, mn, mx, props, wall, coll, n):
    """A vertical registration pin straddling each radial seam: a ridge on one wedge
    seating into a groove on its neighbour, so banded-together wedges can't shift
    sideways. Best-effort — a pin that can't be seated in the wall is skipped (the
    overlap probe), so it never floats. The mold is at the origin here, so ray casts
    and placements use world coordinates directly (matrix_world is identity)."""
    zc = (mn.z + mx.z) * 0.5
    half_z = (mx.z - mn.z) * 0.5
    key_r = min(props.key_radius, max(wall * 0.45, 0.2))
    reach = max(mx.x - mn.x, mx.y - mn.y) + 10.0
    eps = math.radians(4.0)
    depth = min(half_z * 1.4, max(wall * 3.0, 3.0))

    for k in range(n):
        theta = 2.0 * math.pi * k / n            # seam shared by wedge k and k-1
        a = parts[k]
        b = parts[(k - 1) % n]
        # Probe just *inside* wedge a (angle + eps) to find its outer wall radius.
        ca, sa = math.cos(theta + eps), math.sin(theta + eps)
        origin = Vector((center.x + ca * reach, center.y + sa * reach, zc))
        hit, loc, _nrm, _idx = a.ray_cast(origin, Vector((-ca, -sa, 0.0)))
        if not hit:
            continue
        r_hit = math.hypot(loc.x - center.x, loc.y - center.y)
        r = max(r_hit - wall * 0.9, r_hit * 0.5)
        # The pin sits ON the seam plane (angle theta), inside the wall, mid-height.
        c = Vector((center.x + math.cos(theta) * r,
                    center.y + math.sin(theta) * r, zc))
        pin = util.add_cone("MF_rkey", c, key_r, key_r, depth, 'Z', coll)

        probe = util.duplicate_object(pin, "MF_rtest", coll)
        util.boolean(probe, a, 'INTERSECT')
        seated = bool(probe.data.polygons)
        util.remove_object(probe)
        if not seated:
            util.remove_object(pin)
            continue

        socket = util.duplicate_object(pin, "MF_rsock", coll)
        a_bak, b_bak = a.data.copy(), b.data.copy()
        try:
            util.boolean(a, pin, 'UNION')          # ridge on wedge a
            util.boolean(b, socket, 'DIFFERENCE')  # groove in neighbour b
            if not (util.part_is_valid(a)[0] and util.part_is_valid(b)[0]):
                raise RuntimeError("registration pin would break a wedge")
            for bak in (a_bak, b_bak):
                if bak.users == 0:
                    bpy.data.meshes.remove(bak)
        except Exception:                          # roll back; a key never breaks a wedge
            for obj, bak in ((a, a_bak), (b, b_bak)):
                cur = obj.data
                obj.data = bak
                if cur.users == 0:
                    bpy.data.meshes.remove(cur)
        finally:
            util.remove_object(pin)
            util.remove_object(socket)
