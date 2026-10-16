"""Build the mold body.

Returns ``(object, info)`` where ``info`` may carry a pre-computed
``silicone_volume`` (for the pour box, the silicone is the gap, not the part
you print).
"""

import math
import os
import types

import bpy
import bmesh
from mathutils import Matrix, Vector

from . import constants as C
from . import util, volume, meshprep


def _solidify_dilation(obj, props):
    """Turn a fresh Solidify result into one solid dilation.

    A closed master's Solidify is hollow — the outer offset surface plus an
    enclosed cavity shell. Deleting the negative-volume cavity shells yields the
    EXACT dilated solid with full surface detail (a voxel remesh would smooth
    the cavity that's supposed to capture the model's detail). Only an offset
    that needs repair gets the voxel remesh (which also fills): still in pieces
    after the void fill, left non-manifold by it (the void shell touched the
    outer), or folded over itself across a deep crease (self-intersections — a
    watertight single island the other checks can't see, but the flaps print as
    slits on the shell and serrated wing edges)."""
    util.fill_enclosed_voids(obj)
    if (util.island_count(obj) > 1
            or util.nonmanifold_count(obj) > 0
            or util.has_self_intersections(obj)):
        meshprep.voxel_remesh(obj, getattr(props, "detail_voxel", 1.0))


def _effective_bolts(props):
    """How many bolt holes to drill per wing/seam: ``None`` = place automatically
    by flange height (Auto Bolts on), an int = that exact count — and 0 really
    means zero (no bolt holes)."""
    if getattr(props, "bolt_auto", True):
        return None
    return getattr(props, "bolt_count", 0)


def build_shell(master, props, coll, detail=None):
    if props.box_style == 'POUR_BOX':
        return _build_pour_box(master, props, coll)
    # Direct (SOLID/BLOCK) mold: return the OUTER solid plus the cavity cutter and
    # let the pipeline carve the cavity LAST — after the funnel and wings are unioned
    # on — exactly like the pour box. That welds the spout to a solid body (no
    # floating gap) and the cut trims its base flush. The cutter is the full-detail
    # model, so the printed impression keeps the model's detail.
    cutter = detail if detail is not None else util.duplicate_object(master, "MF_cav", coll)
    if getattr(props, "block", False):
        mn, mx = util.world_bbox(master)
        wall = props.wall_thickness
        size = (mx - mn) + Vector((2.0 * wall, 2.0 * wall, 2.0 * wall))
        outer = util.add_box("MF_Mold", (mn + mx) * 0.5, size, coll)
    else:
        outer = _dilate_solid(master, props.wall_thickness, "MF_Mold", props, coll)
    return outer, {"cavity_cutter": cutter}


def _apply_solidify(obj, distance, even):
    mod = obj.modifiers.new("mf_solidify", 'SOLIDIFY')
    mod.thickness = distance
    mod.offset = 1.0
    mod.use_even_offset = even
    mod.use_quality_normals = True
    util.apply_all_modifiers(obj)


def _offset_exploded(obj, master, distance):
    """True if Solidify flung vertices to (near-)infinity. ``use_even_offset`` scales
    the offset by 1/cos(half the crease angle), which DIVERGES at a near-flat (~180 deg)
    or razor-sharp crease — exactly what extruded logos/text and a sawtooth/concave
    footprint have — so a few verts shoot millions of units out. A true dilation grows
    each extent by at most 2*distance, so a span wildly past that (or non-finite) is the
    blow-up."""
    nmn, nmx = util.world_bbox(obj)
    if not all(math.isfinite(c) for c in (*nmn, *nmx)):
        return True
    omn, omx = util.world_bbox(master)
    for i in range(3):
        plausible = (omx[i] - omn[i]) + 2.0 * abs(distance) + 1.0
        if (nmx[i] - nmn[i]) > 4.0 * plausible:
            return True
    return False


def _dilate_solid(master, distance, name, props, coll):
    """A solid the shape of the model grown outward by ``distance`` (the closed
    layer between the model surface and its outward offset).

    Even-offset keeps the dilation a uniform thickness, but it can diverge to infinity
    at a near-flat or very sharp crease (extruded text, a sawtooth base). When that
    happens we rebuild with a plain normal offset: it can pinch in thin spots (the
    void-fill + remesh below repair that) but it never shoots to infinity, so the mold
    can't blow up into a multi-million-unit spike that later OOMs a remesh or wrecks the
    split."""
    obj = util.duplicate_object(master, name, coll)
    _apply_solidify(obj, distance, even=True)
    if _offset_exploded(obj, master, distance):
        util.remove_object(obj)
        obj = util.duplicate_object(master, name, coll)
        _apply_solidify(obj, distance, even=False)
    _solidify_dilation(obj, props)
    return obj


def _build_pour_box(master, props, coll):
    """A hollow printed container: walls of ``shell_wall`` whose inner cavity is
    the model grown by ``silicone_gap``. You nest the model inside and pour
    silicone into the gap.

        outer = dilate(model, gap + shell)   # region [0, gap+shell]
        inner = dilate(model, gap)           # region [0, gap]
        box   = outer - inner                # region [gap, gap+shell]

    The silicone you actually pour is the gap *around* the model — inner minus the
    model itself — not the whole dilated solid; it is kept as ``MF_Skin`` so the
    user can see exactly the silicone they'll pour.

    With ``skin_keys`` (the glove / mother-mold workflow, typically a thin gap)
    registration bumps are raised on that silicone, with matching pockets in the
    printed jacket, so the cured skin can't shift or slump.

    The box is returned SOLID, with the cavity cutter (``inner``) handed back in
    ``info`` — the pipeline carves the cavity only AFTER the funnel/wings are
    unioned on, so the cavity cut trims the funnel base flush instead of leaving
    a wall lip hanging into the opening.
    """
    gap = props.silicone_gap
    shell = props.shell_wall
    outer = _dilate_solid(master, gap + shell, "MF_Mold", props, coll)
    inner = _dilate_solid(master, gap, "MF_inner", props, coll)
    if getattr(props, "skin_keys", False):
        _add_skin_keys(inner, gap, shell, coll)
    silicone_volume = max(volume.mesh_volume(inner) - volume.mesh_volume(master), 0.0)

    info = {"silicone_volume": silicone_volume,
            "skin": _build_skin_preview(inner, master, coll),
            "cavity_cutter": inner}
    return outer, info


def _regrow_footprint(slab, distance, sh, props, coll):
    """Grow a thin footprint slab outward by ``distance`` in 2D (a Minkowski offset, so
    concave outlines stay clean) and return a fresh thin slab with vertical walls.
    Consumes ``slab``."""
    grown = _dilate_solid(slab, distance, "MF_plg", props, coll)
    util.remove_object(slab)
    gmn, gmx = util.world_bbox(grown)
    midz = (gmn.z + gmx.z) * 0.5
    big = (gmx - gmn).length * 2.0 + 10.0
    cut = util.add_box("MF_plgs",
                       Vector(((gmn.x + gmx.x) * 0.5, (gmn.y + gmx.y) * 0.5, midz)),
                       Vector((big, big, sh)), coll)
    util.boolean(grown, cut, 'INTERSECT')
    util.remove_object(cut)
    util.remove_small_islands(grown)
    return grown


def _footprint_prism(model, grow, z0, z1, props, coll, name):
    """A SMOOTH vertical prism of the model's base footprint grown outward by ``grow``
    (a 2D Minkowski offset, clean on concave outlines), spanning z0..z1 — no sawtooth.
    Shared by the plinth body (before its teeth are cut) and the jacket's smooth outer
    skirt (so the teeth never telegraph to the shell's outside)."""
    mn, mx = util.world_bbox(model)
    cx, cy = (mn.x + mx.x) * 0.5, (mn.y + mx.y) * 0.5
    SZ = (mx - mn).length * 2.0 + 10.0
    sh = max(min((z1 - z0) * 0.15, 2.0), 0.4)
    base = util.duplicate_object(model, name, coll)
    slab = util.add_box(name + "_x", Vector((cx, cy, mn.z + sh * 0.5)), Vector((SZ, SZ, sh)), coll)
    util.boolean(base, slab, 'INTERSECT')
    util.remove_object(slab)
    util.remove_small_islands(base)
    if grow > 0.0:
        base = _regrow_footprint(base, grow, sh, props, coll)
    fmn, fmx = util.world_bbox(base)
    fzc = (fmn.z + fmx.z) * 0.5
    fth = max(fmx.z - fmn.z, 0.2)
    base.data.transform(Matrix.Translation((0.0, 0.0, -fzc)))
    base.data.transform(Matrix.Diagonal((1.0, 1.0, (z1 - z0) / fth, 1.0)))
    base.data.transform(Matrix.Translation((0.0, 0.0, (z0 + z1) * 0.5)))
    base.data.update()
    return base


def _cross_section_loop(obj, z):
    """The ordered XY outline of ``obj`` at height ``z`` (its largest cross-section loop).
    Bisecting the solid leaves just the cut edges; we walk them into an ordered ring.
    Returns a list of (x, y) or None."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    try:
        geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
        bmesh.ops.bisect_plane(bm, geom=geom, dist=1e-6,
                               plane_co=(0.0, 0.0, z), plane_no=(0.0, 0.0, 1.0),
                               clear_inner=True, clear_outer=True)
        bm.verts.ensure_lookup_table()
        adj = {}
        for e in bm.edges:
            a, b = e.verts
            adj.setdefault(a, []).append(b)
            adj.setdefault(b, []).append(a)
        loops = []
        seen = set()
        for start in adj:
            if start in seen:
                continue
            loop = [start]
            seen.add(start)
            prev, cur = None, start
            while True:
                nxts = [v for v in adj.get(cur, ()) if v is not prev and v not in seen]
                if not nxts:
                    break
                nxt = nxts[0]
                seen.add(nxt)
                loop.append(nxt)
                prev, cur = cur, nxt
            if len(loop) >= 3:
                loops.append(loop)
        if not loops:
            return None
        best = max(loops, key=len)
        return [(v.co.x, v.co.y) for v in best]
    finally:
        bm.free()


def _sawtooth_solid(loop, cx, cy, zb0, zb1, N, T, grow, coll, name):
    """A capped ring-solid from footprint outline ``loop`` (centred near cx, cy), spanning
    zb0..zb1, its side pushed radially out by a triangle wave (N teeth of depth T, flat at
    the caps) plus a uniform ``grow``. Built ring-by-ring at fixed heights so it stays a
    clean manifold — no Solidify offset that could self-intersect and be voxel-remeshed
    smooth. The plinth is grow=0; the shell's socket is grow=tolerance — the same teeth a
    hair wider — so the teeth transfer to the socket at ANY base height (a Solidify-offset
    socket smoothed them off on a short, fine-pitch plinth: the bug)."""
    n = len(loop)
    K = max(N * 16, 32)
    span = max(zb1 - zb0, 1e-4)
    bm = bmesh.new()
    rings = []
    for k in range(K + 1):
        z = zb0 + (zb1 - zb0) * k / K
        t = (z - zb0) / span * N
        tri = 1.0 - abs(2.0 * (t - math.floor(t)) - 1.0)
        push = T * tri + grow
        ring = []
        for (x, y) in loop:
            dx, dy = x - cx, y - cy
            r = math.hypot(dx, dy)
            if r > 1e-6:
                x = x + dx / r * push
                y = y + dy / r * push
            ring.append(bm.verts.new((x, y, z)))
        rings.append(ring)
    for k in range(K):
        for i in range(n):
            j = (i + 1) % n
            bm.faces.new((rings[k][i], rings[k][j], rings[k + 1][j], rings[k + 1][i]))
    bm.faces.new(list(reversed(rings[0])))         # bottom cap
    bm.faces.new(rings[K])                          # top cap
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return util.new_mesh_object(name, bm, coll)


def build_locking_plinth(model, props, coll):
    """A footprint-hugging base plinth with a fine triangular-sawtooth (zigzag) side
    profile, PLUS a matching tolerance socket for the shells. Kept HIGH-POLY.

    Built from the model's OWN base footprint (full resolution, never a remeshed proxy):
    take a SMOOTH grown-by-margin prism, extract its clean outline loop, then build BOTH the
    plinth and its socket as stacks of rings at fixed heights (a clean /\\/\\ profile). The
    socket is the same rings pushed out by the lock tolerance, so its teeth match the plinth
    exactly at ANY base height. Returns ``(plinth, socket)`` — the pipeline unites the
    plinth into the high-poly positive and carves the socket into the shells. Pre-flatten
    the model's base."""
    mn, mx = util.world_bbox(model)
    z_bot = mn.z
    H = max(props.lock_height, 1.0)
    M = max(props.lock_margin, 0.0)
    T = max(props.lock_tooth_depth, 0.2)
    N = max(int(getattr(props, "lock_teeth", 3)), 1)
    tol = max(getattr(props, "lock_tolerance", 0.2), 0.0)
    weld = min(H * 0.25, 2.0)                       # overlap up into the model to weld
    zb0, zb1 = z_bot - H, z_bot + weld

    prism = _footprint_prism(model, M, zb0, zb1, props, coll, "MF_plinth")
    loop = _cross_section_loop(prism, (zb0 + zb1) * 0.5)
    if loop is None or len(loop) < 3:
        # Rare: no clean outline. Fall back to a smooth plinth + a dilated socket.
        socket = (_dilate_solid(prism, tol, "MF_socket", props, coll) if tol > 0.0
                  else util.duplicate_object(prism, "MF_socket", coll))
        return prism, socket
    util.remove_object(prism)
    cx = sum(p[0] for p in loop) / len(loop)
    cy = sum(p[1] for p in loop) / len(loop)
    plinth = _sawtooth_solid(loop, cx, cy, zb0, zb1, N, T, 0.0, coll, "MF_plinth")
    socket = _sawtooth_solid(loop, cx, cy, zb0, zb1, N, T, tol, coll, "MF_socket")
    return plinth, socket


def build_locking_jacket(proxy, hi_model, plinth, socket, props, coll):
    """Pour-box jacket for a Locking Base: the silicone gap around the MODEL, plus the snug
    tolerance ``socket`` that hugs the sawtooth PLINTH so the printed shells lock onto it.

    The base region the user sees (the outer skirt + the socket) is built from the
    HIGH-POLY model footprint (``hi_model``) and the high-poly ``plinth``/``socket``, so
    it's clean and fine — never the coarse remesh. Only the model-body cavity/outer is
    dilated from ``proxy`` (a cleaned, possibly remeshed copy that merges stray islands),
    which is fine because the silicone captures the body's detail from the master you nest.

    ``socket`` is pre-built from the same rings as the plinth (a hair wider), so its teeth
    survive at any base height — a Solidify-offset socket used to self-intersect on a short,
    fine-pitch plinth and get voxel-remeshed smooth. The OUTSIDE of the base stays SMOOTH:
    the outer skirt is a plain footprint prism, not the toothed socket, so the teeth never
    telegraph to the shell's exterior. Returns ``(outer, info)``; the pipeline carves the
    cavity cutter LAST and opens the bottom."""
    gap = props.silicone_gap
    shell = props.shell_wall
    tol = max(getattr(props, "lock_tolerance", 0.2), 0.0)
    M = max(props.lock_margin, 0.0)
    T = max(props.lock_tooth_depth, 0.2)
    pmn, pmx = util.world_bbox(plinth)

    # Outer jacket: the model-body wall (gap+shell, from the proxy) UNION a SMOOTH,
    # HIGH-POLY base skirt grown just far enough to keep >= shell of wall outside the
    # socket's deepest tooth, and reaching past the socket top so the rim shoulder stays
    # solid (no slot into the cavity at the inside rim corner).
    outer = _dilate_solid(proxy, gap + shell, "MF_Mold", props, coll)
    skirt = _footprint_prism(hi_model, M + T + tol + shell,
                             pmn.z, pmx.z + tol + shell, props, coll, "MF_skirt")
    util.boolean(outer, skirt, 'UNION')
    util.remove_object(skirt)

    # Cavity cutter: the silicone gap around the model body (from the proxy) UNION the
    # tolerance socket around the sawtooth plinth (the teeth grip on the inside).
    inner = _dilate_solid(proxy, gap, "MF_inner", props, coll)
    silicone_volume = max(volume.mesh_volume(inner) - volume.mesh_volume(proxy), 0.0)
    skin = _build_skin_preview(inner, proxy, coll)
    util.boolean(inner, socket, 'UNION')
    util.remove_object(socket)
    return outer, {"silicone_volume": silicone_volume, "skin": skin, "cavity_cutter": inner}


def socket_pocket_cutter(master, plinth, props, coll):
    """The AIR POCKET a Locking Base socket opens into: everything inside the socket
    column that has model directly above it, from just below the plinth top up to
    the model's underside. Returns a cutter object, or None when the plinth has no
    clean outline.

    A model with a hollow or domed underside (a shell lying on its back, a saucer, a
    body standing on a rim) touches the plinth only along that rim, so the plinth -
    and the socket - are the size of the rim while the model arches over an empty
    pocket above the socket top. The jacket is right there: its wall follows the
    underside a gap+shell away, the pocket stays open to the mouth. But the clamp
    wings are cut from a rind dilated ``offset+width`` from the model, and under
    the arch "outward from the model" points DOWN into the pocket: clipped to the
    seam slab, the rind became a straight bar bridging the socket mouth along the
    parting line, hanging from the cavity ceiling, flush with the socket top. It was
    outside both the silicone gap and the socket, so no cutter ever touched it.

    Built as an upward-ray heightfield of the model's underside over the column,
    intersected with the column. Only the space UNDER the model counts - the flange
    beside a narrow body standing on a wide foot is the wing proper and is left
    alone - and only inside the column, so a flange hugging a sphere's lower half is
    never trimmed. The column is the plinth outline pulled IN to half a millimetre
    inside the shell wall (gap+shell, or the socket margin if that is smaller): a
    wider column would reach past the wall of a stem standing under a bulge and
    slit the flange off that wall. What is left of a bar between the column and the
    socket's edge sits inside the solid skirt shoulder, so nothing shows."""
    loop = None
    pmn, pmx = util.world_bbox(plinth)
    try:
        loop = _cross_section_loop(plinth, pmx.z - 0.1)     # ~the smooth top ring
    except Exception:
        loop = None
    if loop is None or len(loop) < 3:
        return None
    cx = sum(q[0] for q in loop) / len(loop)
    cy = sum(q[1] for q in loop) / len(loop)
    M = max(props.lock_margin, 0.0)
    wall = props.silicone_gap + props.shell_wall
    mmn, mmx = util.world_bbox(master)
    z_floor = pmx.z - 1.0            # inside the socket band: the socket owns what's below
    column = _sawtooth_solid(loop, cx, cy, z_floor - 1.0, mmx.z + 1.0, 1, 0.0,
                             min(M, wall) - 0.5 - M, coll, "MF_wcol")
    cmn, cmx = util.world_bbox(column)
    n = int(max(min(round(max(cmx.x - cmn.x, cmx.y - cmn.y) / 1.5), 80), 24))
    under = _under_model_solid(master, cmn.x - 1.0, cmx.x + 1.0, cmn.y - 1.0,
                               cmx.y + 1.0, z_floor, coll, n)
    util.boolean(under, column, 'INTERSECT')
    util.remove_object(column)
    if not under.data.polygons:
        util.remove_object(under)
        return None
    return under


def _under_model_solid(master, x0, x1, y0, y1, z_floor, coll, n):
    """Heightfield solid of the space directly BELOW ``master`` over an n x n grid on
    [x0, x1] x [y0, y1]: each vertex rises from ``z_floor`` to the model's underside
    (the first hit of an upward ray), or stays a hair above the floor where nothing
    is overhead. The field is dilated by one vertex so it never falls short at the
    rim of a hollow (a stub of the bar left welded to the wall)."""
    dg = bpy.context.evaluated_depsgraph_get()
    mw = master.matrix_world
    inv = mw.inverted()
    up = (inv.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
    lo = z_floor + 0.05
    H = [[lo] * (n + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        x = x0 + (x1 - x0) * i / n
        for j in range(n + 1):
            y = y0 + (y1 - y0) * j / n
            hit, loc, _nrm, _idx = master.ray_cast(inv @ Vector((x, y, z_floor - 1.0)),
                                                   up, depsgraph=dg)
            if hit:
                H[i][j] = max((mw @ loc).z, lo)
    G = [[max(H[a][b]
              for a in range(max(i - 1, 0), min(i + 1, n) + 1)
              for b in range(max(j - 1, 0), min(j + 1, n) + 1))
          for j in range(n + 1)] for i in range(n + 1)]
    bm = bmesh.new()
    zb = z_floor - 0.5
    top = [[bm.verts.new((x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * j / n, G[i][j]))
            for j in range(n + 1)] for i in range(n + 1)]
    bot = [[bm.verts.new((x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * j / n, zb))
            for j in range(n + 1)] for i in range(n + 1)]
    for i in range(n):
        for j in range(n):
            bm.faces.new((top[i][j], top[i + 1][j], top[i + 1][j + 1], top[i][j + 1]))
            bm.faces.new((bot[i][j], bot[i][j + 1], bot[i + 1][j + 1], bot[i + 1][j]))
    for i in range(n):
        bm.faces.new((top[i][0], bot[i][0], bot[i + 1][0], top[i + 1][0]))
        bm.faces.new((top[i][n], top[i + 1][n], bot[i + 1][n], bot[i][n]))
    for j in range(n):
        bm.faces.new((top[0][j], top[0][j + 1], bot[0][j + 1], bot[0][j]))
        bm.faces.new((top[n][j], bot[n][j], bot[n][j + 1], top[n][j + 1]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return util.new_mesh_object("MF_wunder", bm, coll)


def unite_plinth(positive, plinth, coll):
    """Boolean-UNITE the sawtooth plinth into the high-poly positive so it becomes ONE
    manifold solid. Returns True on a verified clean union.

    A plain mesh join (the old way) leaves the plinth as a second closed shell nested
    inside the object — it prints fine, but resin slicers' hollowing (offset-surface)
    tools then treat the plinth as solid material and only hollow the model shell. The
    union is validated (one island, manifold, spans plinth bottom to model top, no lost
    volume); if it fails — messy sculpts can defeat even the EXACT solver — the mesh is
    restored and the old overlapping join is used so the build still succeeds, and the
    caller reports it. ``plinth`` itself is never consumed."""
    bak = positive.data.copy()
    plinth_bottom = util.world_bbox(plinth)[0].z
    top_before = util.world_bbox(positive)[1].z
    vol_before = volume.mesh_volume(positive)
    # The fast solver leaves two solids that only TOUCH on a face (a core
    # standing flat on its plinth) as two islands; EXACT merges them.
    for solver in ('MANIFOLD', 'EXACT'):
        pp = util.duplicate_object(plinth, "MF_pp", coll)
        ok = False
        try:
            util.boolean(positive, pp, 'UNION', solver=solver)
            mn2, mx2 = util.world_bbox(positive)
            ok = (util.island_count(positive) == 1
                  and util.nonmanifold_count(positive) == 0
                  and mn2.z <= plinth_bottom + 0.3      # silent no-op union misses the base
                  and mx2.z >= top_before - 0.3
                  and volume.mesh_volume(positive) >= vol_before * 0.98)
        except Exception:
            ok = False
        finally:
            util.remove_object(pp)
        if ok:
            if bak.users == 0:
                bpy.data.meshes.remove(bak)
            return True
        cur = positive.data
        positive.data = bak.copy()
        if cur.users == 0:
            bpy.data.meshes.remove(cur)
    cur = positive.data
    positive.data = bak
    if cur.users == 0:
        bpy.data.meshes.remove(cur)
    util.join_meshes(positive, util.duplicate_object(plinth, "MF_pp", coll))
    return False


def _inside_xy(obj, x, y, z):
    """Is the point (x, y, z) inside the solid? Vertical ray parity from below:
    an odd number of surface crossings under ``z`` means inside at that height."""
    mn = util.world_bbox(obj)[0]
    inv = obj.matrix_world.inverted()
    d = (inv.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
    o = inv @ Vector((x, y, mn.z - 1.0))
    hits = 0
    for _ in range(64):
        hit, loc, _n, _i = obj.ray_cast(o, d)
        if not hit:
            break
        if (obj.matrix_world @ loc).z >= z:
            break
        hits += 1
        o = loc + d * 1e-3
    return hits % 2 == 1


def add_section_keys(sections, seams, peg_r, clear, coll):
    """Integral alignment keys across each glue seam of the sectioned positive:
    raised PEGS printed on the lower section's face seat straight into matching
    SOCKETS (grown by the fit clearance) bored into the next section — stack,
    glue, done. No loose hardware, and nothing ever touches the OUTSIDE: the
    cast surface stays exactly the model's.

    Keys sit well inside the cross-section (in-solid parity at the peg's root
    AND its tip height, plus a wall margin), up to three per seam; a slim seam
    gets fewer or none (a plain glued face still works). Per-seam best-effort:
    a seam whose booleans fail is simply left plain. Returns the key count."""
    PEG = 6.0                                    # protrusion past the seam face
    total = 0
    for z in seams:
        lower = upper = None
        for s in sections:
            smn, smx = util.world_bbox(s)
            if abs(smx.z - z) < 1.0:
                lower = s
            if abs(smn.z - z) < 1.0:
                upper = s
        if lower is None or upper is None:
            continue
        try:
            loop = _cross_section_loop(lower, z - 0.6)
            if not loop or len(loop) < 3:
                continue
            n = len(loop)
            cx = sum(q[0] for q in loop) / n
            cy = sum(q[1] for q in loop) / n
            cands = [(cx + (loop[(k * n) // 3][0] - cx) * 0.5,
                      cy + (loop[(k * n) // 3][1] - cy) * 0.5) for k in range(3)]
            cands.append((cx, cy))
            need = peg_r + clear + 1.6
            placed = []
            for (px, py) in cands:
                if len(placed) >= 3:
                    break
                if any(math.hypot(px - ax, py - ay) < peg_r * 5.0
                       for (ax, ay) in placed):
                    continue
                if min(math.hypot(px - qx, py - qy) for (qx, qy) in loop) < need:
                    continue
                if not _inside_xy(lower, px, py, z - 1.0):
                    continue
                if not _inside_xy(upper, px, py, z + PEG + 0.6):
                    continue
                placed.append((px, py))
            if not placed:
                continue
            # Snapshot both sections: if a key boolean degrades a previously
            # VALID section (an already-messy one is exempt), restore and leave
            # this seam as a plain glued face.
            lo_was = util.part_is_valid(lower)[0]
            up_was = util.part_is_valid(upper)[0]
            lbak = lower.data.copy()
            ubak = upper.data.copy()
            pegs = sockets = None
            for (px, py) in placed:
                body = util.add_cone("MF_key", Vector((px, py, z + 1.25)),
                                     peg_r, peg_r, 6.5, 'Z', coll)   # z-2 .. z+4.5
                tip = util.add_cone("MF_key", Vector((px, py, z + 5.15)),
                                    peg_r, peg_r * 0.6, 1.5, 'Z', coll)
                util.boolean(body, tip, 'UNION')                     # ONE solid peg
                util.remove_object(tip)
                if pegs is None:
                    pegs = body
                else:
                    util.join_meshes(pegs, body)   # disjoint shells: fine operand
                sock = util.add_cone("MF_key", Vector((px, py, z + 2.65)),
                                     peg_r + clear, peg_r + clear, 7.3, 'Z',
                                     coll)                           # z-1 .. z+6.3
                if sockets is None:
                    sockets = sock
                else:
                    util.join_meshes(sockets, sock)
            util.boolean(upper, sockets, 'DIFFERENCE')
            util.remove_object(sockets)
            util.boolean(lower, pegs, 'UNION')
            util.remove_object(pegs)
            if ((lo_was and not util.part_is_valid(lower)[0])
                    or (up_was and not util.part_is_valid(upper)[0])):
                for obj, bak in ((lower, lbak), (upper, ubak)):
                    cur = obj.data
                    obj.data = bak
                    if cur.users == 0:
                        bpy.data.meshes.remove(cur)
            else:
                total += len(placed)
                for bak in (lbak, ubak):
                    if bak.users == 0:
                        bpy.data.meshes.remove(bak)
        except Exception:
            for o in list(coll.objects):
                if o.name.startswith("MF_key"):
                    util.remove_object(o)
    return total


def build_stamp_pan(relief, props, coll):
    """Printed mold for a silicone INK STAMP: a rectangular pan whose floor has
    the design ENGRAVED into it (the negative). Pour silicone ``tray_depth``
    deep, cure, peel - the slab carries the design raised by ``stamp_relief``
    and mirrored, so stamped imprints read correctly.

    ``relief`` is the design as a solid (already scaled/mirrored/centred at the
    origin in XY, spanning z -overshoot..+overshoot). Returns ``(pan, info)``;
    the relief is left where it is for the caller to keep as the reference
    positive."""
    depth = max(getattr(props, "stamp_relief", 2.0), 0.3)
    border = max(getattr(props, "tray_margin", 6.0), 2.0)
    wall = max(getattr(props, "tray_wall", 2.5), 1.2)
    slab = max(getattr(props, "tray_depth", 5.0), 1.5)
    floor = max(getattr(props, "tray_floor", 3.0), depth + 1.5)
    free = 2.0                                     # freeboard above the pour

    rmn, rmx = util.world_bbox(relief)
    in_w = (rmx.x - rmn.x) + 2.0 * border
    in_l = (rmx.y - rmn.y) + 2.0 * border
    z0 = 0.0                                       # floor top / stamp face plane
    pan = util.add_box("MF_Mold_A",
                       Vector((0.0, 0.0, (slab + free - floor) * 0.5)),
                       Vector((in_w + 2.0 * wall, in_l + 2.0 * wall,
                               floor + slab + free)), coll)
    cavity = util.add_box("MF_cav", Vector((0.0, 0.0, (slab + free) * 0.5 + 0.5)),
                          Vector((in_w, in_l, slab + free + 1.0)), coll)
    util.boolean(pan, cavity, 'DIFFERENCE')
    util.remove_object(cavity)
    vol_before = volume.mesh_volume(pan)

    # Engrave: sink the relief so its TOP pokes ``depth`` below the floor top
    # (overshooting up into the open cavity, which costs nothing).
    cut = util.duplicate_object(relief, "MF_engrave", coll)
    cmn, cmx = util.world_bbox(cut)
    cut.data.transform(Matrix.Translation((0.0, 0.0, (z0 - depth) - cmn.z)))
    util.boolean(pan, cut, 'DIFFERENCE')
    util.remove_object(cut)
    recess = max(vol_before - volume.mesh_volume(pan), 0.0)
    silicone = in_w * in_l * slab + recess
    return pan, {"silicone_volume": silicone, "recess_volume": recess,
                 "inner": (in_w, in_l), "floor_top": z0}




def _sdf_offset(obj, distance, voxel):
    """Offset the closed solid ``obj`` by ``distance`` (negative = inward) on a
    signed-distance grid: geometry nodes Mesh to SDF Grid, then Grid to Mesh
    at isovalue ``distance``. The distance field is exact to within a voxel
    everywhere and knows nothing of creases, so the result is the true
    parallel surface - what a Solidify offset only manages on a smooth shape
    (at every convex crease tighter than the distance its inner sheet folds
    over itself). Applied in place; raises if the nodes are missing."""
    band = int(math.ceil(abs(distance) / voxel)) + 2
    ng = bpy.data.node_groups.new("mf_sdf_offset", 'GeometryNodeTree')
    try:
        ng.interface.new_socket("Geometry", in_out='INPUT',
                                socket_type='NodeSocketGeometry')
        ng.interface.new_socket("Geometry", in_out='OUTPUT',
                                socket_type='NodeSocketGeometry')
        n_in = ng.nodes.new("NodeGroupInput")
        n_out = ng.nodes.new("NodeGroupOutput")
        n_sdf = ng.nodes.new("GeometryNodeMeshToSDFGrid")
        n_mesh = ng.nodes.new("GeometryNodeGridToMesh")
        n_sdf.inputs["Voxel Size"].default_value = voxel
        n_sdf.inputs["Band Width"].default_value = band
        n_mesh.inputs["Threshold"].default_value = distance
        n_mesh.inputs["Adaptivity"].default_value = 0.0
        ng.links.new(n_in.outputs[0], n_sdf.inputs["Mesh"])
        ng.links.new(n_sdf.outputs[0], n_mesh.inputs["Grid"])
        ng.links.new(n_mesh.outputs[0], n_out.inputs[0])
        mod = obj.modifiers.new("mf_sdf", 'NODES')
        mod.node_group = ng
        util.apply_all_modifiers(obj)
    finally:
        bpy.data.node_groups.remove(ng)


def core_rounding(distance):
    """Radius the eroded core's ridges are rounded to: a third of the wall,
    between 1 and 3 mm. An inward offset fillets every concave crease of the
    skin but keeps every convex one (a scale edge, a vein, a ridge) as a
    sharp crease on the core; rounding removes those - they print as fragile
    blades and tear the silicone around them."""
    return min(max(distance / 3.0, 1.0), 3.0)


def erode_solid(obj, distance, coll, voxel=1.0, rounding=None):
    """Shrink a closed solid INWARD by ``distance`` everywhere - a true offset,
    the mirror of the cavity dilation - with its ridges rounded. Returns the
    voxel size the core was built at (truthy) on a usable result; 0.0 on
    failure, with the mesh left untouched.

    Cut on a signed-distance grid (``_sdf_offset``): every point deeper than
    ``distance`` from the skin is core, nothing else is. A bumpy sculpt has
    hundreds of creases tighter than a 5 or 10 mm wall; the Solidify offset
    used before folded over itself at each of them and the remesh that tried
    to tidy the folds returned a lumpy, stepped blob with a shredded tip (the
    "terrible core" of 0.40.0). The distance field has no folds to tidy.

    Rounding is a morphological opening: erode by ``distance`` plus the
    rounding radius (``core_rounding`` unless given), then grow back by that
    radius. Every convex crease comes out as a fillet of that radius, fins
    thinner than twice it vanish, and the core never comes closer to the
    skin than ``distance`` - the opening only ever removes material.

    Resolution: about a twelfth of the wall, never coarser than the detail
    voxel, never finer than 0.35 mm or a 450th of the model's longest side
    (a 300 mm toy stays well under a million faces) - fine enough that the
    core keeps the sculpt's shape, coarse enough to stay quick. Regions thinner than twice
    the distance end up with no core, which is physically right (a thin fin
    is all soft); fingers that pinch off are dropped."""
    bak = obj.data.copy()
    try:
        if not hasattr(bpy.types, "GeometryNodeMeshToSDFGrid"):
            raise RuntimeError("this Blender has no SDF grid nodes")
        mn, mx = util.world_bbox(obj)
        longest = max(mx.x - mn.x, mx.y - mn.y, mx.z - mn.z, 1.0)
        v = max(min(distance / 12.0, voxel), 0.35, longest / 450.0)
        if util.nonmanifold_count(obj) > 0:
            # the grid needs a closed skin to tell inside from outside; a
            # sculpt off a scanner or slicer rarely has one
            meshprep.voxel_remesh(obj, v)
        r = core_rounding(distance) if rounding is None else max(rounding, 0.0)
        src = obj.data.copy()
        _sdf_offset(obj, -(distance + r), v)
        if not obj.data.polygons and r > 0.0:
            # too slim for the extra bite: settle for the bare erosion
            cur = obj.data
            obj.data = src
            bpy.data.meshes.remove(cur)
            r = 0.0
            _sdf_offset(obj, -distance, v)
        if src.users == 0:
            bpy.data.meshes.remove(src)
        if not obj.data.polygons:
            raise RuntimeError("erosion ate the whole model")
        if r > 0.0:
            _sdf_offset(obj, r, v)
        util.keep_largest_island(obj)
        if util.nonmanifold_count(obj) > 0:
            meshprep.voxel_remesh(obj, v)
        if volume.mesh_volume(obj) <= 1e-6:
            raise RuntimeError("erosion ate the whole model")
    except Exception:
        obj.modifiers.clear()
        obj.data = bak
        return 0.0
    if bak.users == 0:
        bpy.data.meshes.remove(bak)
    return v


def _skirt_cut(bottom_z, wall):
    """Height of the section a core's straight skirt is extruded from: above
    the eroded bottom's rounded edge, where the core's side is steep. Cut
    through the rounding, where the surface is nearly flat, the section comes
    out wavy and the skirt carries vertical flutes."""
    return bottom_z + core_rounding(wall) + max(wall * 0.35, 1.0)


def _skirt_to_base(obj, cut_z, floor_z):
    """Replace everything of the closed solid ``obj`` below the plane
    ``cut_z`` with a straight skirt: its cross-section at ``cut_z`` extruded
    down to ``floor_z``. Returns True when the section existed and the mesh
    came out manifold; the mesh is left untouched otherwise."""
    if floor_z >= cut_z:
        return False
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    try:
        res = bmesh.ops.bisect_plane(
            bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], dist=1e-4,
            plane_co=(0.0, 0.0, cut_z), plane_no=(0.0, 0.0, 1.0),
            clear_inner=True)
        cut_edges = [g for g in res["geom_cut"] if isinstance(g, bmesh.types.BMEdge)]
        if not cut_edges:
            return False
        # drop the rim's walls from the cut loops (the loops stay open), move
        # the new loops down to the floor and cap them
        ext = bmesh.ops.extrude_edge_only(bm, edges=cut_edges)
        new_verts = [g for g in ext["geom"] if isinstance(g, bmesh.types.BMVert)]
        for v in new_verts:
            v.co.z = floor_z
        rim = [g for g in ext["geom"]
               if isinstance(g, bmesh.types.BMEdge) and g.is_boundary]
        if not rim or not bmesh.ops.edgeloop_fill(bm, edges=rim).get("faces"):
            return False
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        if any(not e.is_manifold for e in bm.edges):
            return False
        bm.to_mesh(obj.data)
        obj.data.update()
    finally:
        bm.free()
    return True


def build_dual_kit(dual_src, dual_plinth, props, coll):
    """Dual-density (firm core / soft shell) kit, Locking-Base style - the way
    dual toys are actually cast: the firm core is cast FIRST in its own mold,
    then seated in the MAIN mold and the soft outer poured around it.

    ``Core_Master`` = the model ERODED inward by Soft Wall - a true offset, so
    the soft layer is the same thickness everywhere: on the inside of a bend
    as on the outside, on top as on the flanks (a scaled copy never is: on a
    curved sculpt it hugs one side and stands off the other). United with a
    SOCKET CROSS carved from the same sawtooth plinth as the positive: a bare
    + of bars whose sawtooth lips drop into the shells' existing socket
    grooves. Mold it in a second MoldForge run and cast it in FIRM silicone.
    The soft pour is INVERTED: fill the open base, click the cured core in -
    the lips clamp in the grooves and the displaced soft burps out through
    the cross's open quadrants, bonding to the core as it cures.

    Deliberately NOT MF_-prefixed: it survives regeneration and passes the
    master-name guard for its own second run. ``dual_src`` (full-detail model
    copy) is consumed; ``dual_plinth`` is consumed by the caller."""
    mn, mx = util.world_bbox(dual_src)
    width = min(mx.x - mn.x, mx.y - mn.y)
    # never erode past the model's own half-width: nothing would be left
    wall = max(min(getattr(props, "core_wall", 5.0), width * 0.4), 0.5)
    core = dual_src
    core.name = "Core_Master"
    base_z = mn.z

    core_voxel = erode_solid(core, wall, coll, getattr(props, "detail_voxel", 1.0))
    if core_voxel:
        # The erosion raised the flat bottom by ``wall`` too. Replace the
        # lowest band with a straight skirt down to the base plane: the core's
        # cross-section just above its eroded bottom, extruded down to where
        # the model stood - so the plinth's weld band (which pokes up into
        # the model) still reaches into the core and unions with it.
        # (Snapping the band's vertices flat, the old way, piled the dense
        # grid mesh's rows onto one plane as overlapping faces, and both
        # boolean solvers then returned garbage for the plinth union.)
        cmn_e = util.world_bbox(core)[0]
        plinth_top = util.world_bbox(dual_plinth)[1].z
        floor_z = base_z if plinth_top >= base_z + 0.5 else base_z - 0.6
        cut = _skirt_cut(cmn_e.z, wall)
        if not _skirt_to_base(core, cut, floor_z):
            meshprep.voxel_remesh(core, core_voxel)
    else:
        # Fallback (a model too thin to erode): the old uniform shrink about
        # the base centre, height compensated so the top gap matches.
        sc = max(1.0 - 2.0 * wall / max(width, 1e-6), 0.3)
        cx, cy = (mn.x + mx.x) * 0.5, (mn.y + mx.y) * 0.5
        h = max(mx.z - mn.z, 1e-6)
        scz = max(min(1.0 - wall / h, 1.0), 0.4)
        core.data.transform(
            Matrix.Translation((cx, cy, mn.z))
            @ Matrix.Diagonal((sc, sc, scz, 1.0))
            @ Matrix.Translation((-cx, -cy, -mn.z)))
    carve_socket_cross(dual_plinth, props, coll)
    unite_plinth(core, dual_plinth, coll)
    util.fix_folded_faces(core)
    return core


def carve_socket_cross(plinth, props, coll):
    """Carve a full sawtooth plinth down to the SOCKET CROSS: just a + of two
    full-height bars whose tips keep the plinth's own sawtooth edge - no
    disc, nothing else. The lips are literally the plinth's teeth, so they
    drop into the shells' socket grooves with the same tolerance and the
    bolted halves clamp them. The four open quadrants are the point: the
    soft pour is done INVERTED (fill the open base, then click the part in)
    and the displaced silicone burps out freely through them. Carves
    ``plinth`` in place."""
    mn, mx = util.world_bbox(plinth)
    cxp, cyp = (mn.x + mx.x) * 0.5, (mn.y + mx.y) * 0.5
    fx, fy, h = mx.x - mn.x, mx.y - mn.y, mx.z - mn.z
    bw = max(min(0.28 * min(fx, fy), 12.0), 6.0)
    keep = None
    for size in (Vector((fx + 4.0, bw, h + 2.0)),
                 Vector((bw, fy + 4.0, h + 2.0))):
        bar = util.add_box("MF_crossbar",
                           Vector((cxp, cyp, (mn.z + mx.z) * 0.5)), size, coll)
        if keep is None:
            keep = bar
        else:
            util.boolean(keep, bar, 'UNION')
            util.remove_object(bar)
    util.boolean(plinth, keep, 'INTERSECT')
    util.remove_object(keep)


PLUG_MIN_WALL = 3.0     # mm of toy that must surround the plug column everywhere
PLUG_BELL_ZONE = 12.0   # mm above the plinth top taken up by the bell and neck


def _inside_solid(obj, pt, dg=None):
    """Is world-space ``pt`` inside the closed mesh ``obj``? Ray parity along a
    skew direction (never along an axis, so it cannot run inside a face), which
    holds whichever way the faces happen to point - a plinth or a repaired
    sculpt can carry patches of flipped normals that fool a normal test."""
    dg = dg or bpy.context.evaluated_depsgraph_get()
    mw = obj.matrix_world
    inv = mw.inverted()
    d = (inv.to_3x3() @ Vector((0.317, 0.531, 0.786))).normalized()
    o = inv @ Vector(pt)
    hits = 0
    for _ in range(64):
        hit, loc, _n, _i = obj.ray_cast(o, d, depsgraph=dg)
        if not hit:
            break
        hits += 1
        o = loc + d * 1e-3
    return hits % 2 == 1


def plug_clearance(model, x, y, z0, z1, samples=10, rays=16):
    """How much toy surrounds a vertical plug column on the line (x, y) between
    z0 (just above the base) and z1 (just under the dome): the smallest
    horizontal distance from the line to the toy's side wall over that height,
    measured with ``rays`` horizontal ray casts per sample, and the material
    above the dome counted the same way. NEGATIVE where the line is outside
    the toy (so a column there breaks out through the side). None if the toy
    cannot be measured.

    Horizontal rays, not the nearest surface point: the positive carries its
    base plinth, whose bottom face sits a few millimetres under the lowest
    samples and would otherwise read as the "wall"."""
    dg = bpy.context.evaluated_depsgraph_get()
    mw = model.matrix_world
    inv = mw.inverted()
    rot = inv.to_3x3()
    worst = None
    for k in range(samples):
        z = z0 + (z1 - z0) * k / max(samples - 1, 1)
        pt = Vector((x, y, z))
        if not _inside_solid(model, pt, dg):         # outside the toy here
            try:
                ok, loc, _nrm, _idx = model.closest_point_on_mesh(inv @ pt, depsgraph=dg)
            except TypeError:
                ok, loc, _nrm, _idx = model.closest_point_on_mesh(inv @ pt)
            return -((pt - (mw @ loc)).length if ok else 1.0)
        lat = None
        for i in range(rays):
            a = 2.0 * math.pi * i / rays
            d = (rot @ Vector((math.cos(a), math.sin(a), 0.0))).normalized()
            hit, hloc, _n, _i = model.ray_cast(inv @ pt, d, depsgraph=dg)
            if hit:
                dist = ((mw @ hloc) - pt).length
                lat = dist if lat is None else min(lat, dist)
        if lat is None:
            return None
        worst = lat if worst is None else min(worst, lat)
    # material above the dome: an upward ray from the top sample, counted like
    # a wall so one number covers the whole column
    top = Vector((x, y, z1))
    up = (rot @ Vector((0.0, 0.0, 1.0))).normalized()
    hit, hloc, _n, _i = model.ray_cast(inv @ top, up, depsgraph=dg)
    if hit:
        worst = min(worst, ((mw @ hloc) - top).length)
    return worst


def _bell_fits(plinth, x, y, bell_r, z, points=8):
    """True if the suction bell's rim, centred at (x, y), lies on the plinth."""
    dg = bpy.context.evaluated_depsgraph_get()
    for i in range(points):
        a = 2.0 * math.pi * i / points
        if not _inside_solid(plinth, Vector((x + bell_r * math.cos(a),
                                             y + bell_r * math.sin(a), z)), dg):
            return False
    return True


def best_plug_spot(model, plinth, base_z, radius, height, bell_r):
    """Where on the socket cross the plug column sits deepest inside the toy.

    The column is vertical and the whole former stands on the cross, so the
    candidate spots are the two arms of the cross: the line y = centre across
    the base and the line x = centre, wherever the suction bell still sits
    fully on the plinth. Each candidate is scored by ``plug_clearance`` over
    the column's full height and the best one wins; the centre is tried first,
    so a symmetric toy keeps its plug in the middle. On a curved or leaning toy
    the middle of the base can be a thin valley - the plug there breaks out
    through the side - while a few centimetres along one arm the toy is solid
    for the plug's full 92 mm. Returns ``(x, y, clearance)``, or None when the
    toy cannot be measured."""
    mn, mx = util.world_bbox(plinth)
    cx, cy = (mn.x + mx.x) * 0.5, (mn.y + mx.y) * 0.5
    zb = (mn.z + mx.z) * 0.5
    # The column is scored from above the suction bell: the bell and the neck
    # sit in the plinth and the toy's base skirt, where a lobed or recessed
    # underside would otherwise read as "outside" and refuse a plug that is
    # fine. From there up to just under the dome.
    z0, z1 = base_z + PLUG_BELL_ZONE, base_z + height - 3.0
    n = 40

    def scan(points):
        best = None
        for (x, y) in points:
            if not _bell_fits(plinth, x, y, bell_r + 1.0, zb):
                continue
            c = plug_clearance(model, x, y, z0, z1)
            if c is not None and (best is None or c > best[2]):
                best = (x, y, c)
        return best

    pts = [(cx, cy)]
    pts += [(mn.x + (mx.x - mn.x) * i / n, cy) for i in range(n + 1)]
    pts += [(cx, mn.y + (mx.y - mn.y) * i / n) for i in range(n + 1)]
    best = scan(pts)
    if best is None:
        return None
    bx, by, _c = best
    step = max(mx.x - mn.x, mx.y - mn.y) / n * 0.5
    fine = []
    if abs(by - cy) < 1e-6:
        fine += [(bx + dx, cy) for dx in (-step, -step * 0.5, step * 0.5, step)]
    if abs(bx - cx) < 1e-6:
        fine += [(cx, by + dy) for dy in (-step, -step * 0.5, step * 0.5, step)]
    better = scan(fine)
    if better is not None and better[2] > best[2]:
        best = better
    return best


def build_anchor_plug(plug_plinth, model_top_z, props, coll, model=None):
    """Printed Vac-U-Lock-style anchor former (``MF_Mold_Plug``): a ribbed
    former standing on the same socket cross as the dual core - the bolted
    shells clamp its sawtooth lips in their grooves. The bundled mesh is the
    user's combined plug + SUCTION BELL: cast inverted (fill, click it in -
    the bell traps an air cushion that keeps the silicone out of it), and the
    demolded base carries the Vac-U-Lock channel with a suction-cup bell
    around it. ALWAYS the original authored size - a scaled plug fits nothing,
    so a mold too small for it gets no plug at all.

    With ``model`` given, the column is placed by ``best_plug_spot``: along
    the cross, where a vertical column stays deepest inside the toy for its
    whole height, instead of the base centre (a curved toy's centre can be a
    thin valley the plug breaks out of). Returns ``(plug, note)``; the plug is
    None when the mold is too small or no spot keeps the column inside the
    toy with PLUG_MIN_WALL around it, and the note says why. Consumes
    ``plug_plinth`` (it becomes the part)."""
    mn, mx = util.world_bbox(plug_plinth)
    fx, fy = mx.x - mn.x, mx.y - mn.y
    margin = getattr(props, "lock_margin", 4.0)
    foot = max(min(fx, fy) - 2.0 * margin, 6.0)      # the model footprint
    base_z = mx.z
    if (model_top_z - base_z < _VAC_STL_NATIVE_H + 1.0
            or min(fx, fy) < _VAC_STL_NATIVE_CUP + 2.0
            or foot < _VAC_STL_NATIVE_D + 4.0):
        util.remove_object(plug_plinth)
        return None, ("this mold is smaller than the original-size plug (it "
                      "needs a cavity about 92 mm tall and a base wide enough "
                      "for the 55 mm bell)")
    d = _VAC_STL_NATIVE_D
    ln = _VAC_STL_NATIVE_H
    cx, cy = (mn.x + mx.x) * 0.5, (mn.y + mx.y) * 0.5
    px, py, wall = cx, cy, None
    if model is not None:
        spot = best_plug_spot(model, plug_plinth, base_z, d * 0.5, ln,
                              _VAC_STL_NATIVE_CUP * 0.5)
        if spot is None:
            # The box was wide enough but the real outline is not: paws, a tail
            # or a waist leave no spot where the 55 mm bell sits fully on the
            # plinth. Silently centring it would put the bell off the base.
            util.remove_object(plug_plinth)
            return None, ("no spot on the base where the 55 mm suction bell sits "
                          "fully on the plinth - the base outline is too narrow or "
                          "too irregular for the plug (widen the model's base, or "
                          "skip the plug)")
        if spot is not None:
            px, py, clearance = spot
            wall = clearance - d * 0.5
            if wall < PLUG_MIN_WALL:
                util.remove_object(plug_plinth)
                return None, (f"no spot on the base cross keeps the plug inside "
                              f"the toy for its full 92 mm (best has "
                              f"{max(wall, 0.0):.1f} mm of toy around it, needs "
                              f"{PLUG_MIN_WALL:.0f}) - tilt the model so more of "
                              f"it stands over the base, or skip the plug")
    carve_socket_cross(plug_plinth, props, coll)
    plug = plug_plinth
    plug.name = "MF_Mold_Plug"
    column = _vac_u_lock_column(d, ln, base_z, coll, px, py)
    util.boolean(plug, column, 'UNION')
    util.remove_object(column)
    util.fix_folded_faces(plug)
    plug["mf_plug_offset"] = [px - cx, py - cy]
    plug["mf_plug_wall"] = -1.0 if wall is None else wall
    off = math.hypot(px - cx, py - cy)
    if wall is None:
        note = ""
    elif off < 1.0:
        note = f"plug at the base centre, {wall:.0f} mm of toy around it"
    else:
        note = (f"plug set {off:.0f} mm off centre along the base cross, where "
                f"the toy is thickest ({wall:.0f} mm of toy around it)")
    return plug, note


def build_anchor_plug_open(model, lay, open_z, props, coll):
    """The Vac-U-Lock former for an OPEN bottom (with or without the key plate).

    Same bundled plug + suction bell as on a Locking Base, at its original size,
    but carried the Open-bottom way: a ring hub under the bell's rim and the
    four legs of ``lay`` resting on the shells' bottom rim, hooks up the outer
    wall, fastened like the suction-cup former. The ring leaves the bell's
    hollow underside open (a disc would seal it into a resin trap). Cast
    inverted: fill the open base, seat the former, fasten it; the toy's base
    cures with the attachment channel inside a suction bell.

    The column goes where it stays deepest inside the toy for its whole height
    (``best_plug_spot`` over the model's own base outline), and the 55 mm bell
    must sit inside that outline. Returns ``(plug, note)``; the plug is None
    when the toy is too small or no spot keeps the column inside it, and the
    note says why."""
    mmn, mmx = util.world_bbox(model)
    z0 = lay.z0
    foot = _footprint_prism(model, 0.0, z0 - 2.0, z0, props, coll, "MF_plugfoot")
    fmn, fmx = util.world_bbox(foot)
    fx, fy = fmx.x - fmn.x, fmx.y - fmn.y
    bell_r = _VAC_STL_NATIVE_CUP * 0.5
    if (mmx.z - open_z < _VAC_STL_NATIVE_H + 1.0
            or min(fx, fy) < _VAC_STL_NATIVE_CUP + 2.0):
        util.remove_object(foot)
        return None, ("this toy is smaller than the original-size plug (it needs "
                      "about 92 mm of height above the opening and a base wide "
                      "enough for the 55 mm bell)")
    d, ln = _VAC_STL_NATIVE_D, _VAC_STL_NATIVE_H
    spot = best_plug_spot(model, foot, open_z, d * 0.5, ln, bell_r)
    util.remove_object(foot)
    if spot is None:
        return None, ("no spot on the base where the 55 mm suction bell sits "
                      "inside the toy's base outline - the base is too narrow or "
                      "too irregular for the plug (widen the model's base, or "
                      "skip the plug)")
    px, py, clearance = spot
    wall = clearance - d * 0.5
    if wall < PLUG_MIN_WALL:
        return None, (f"no spot on the base keeps the plug inside the toy for its "
                      f"full 92 mm (best has {max(wall, 0.0):.1f} mm of toy around "
                      f"it, needs {PLUG_MIN_WALL:.0f}) - tilt the model so more of "
                      f"it stands over the base, or skip the plug")
    pt = FORMER_PLATE_T
    leg_w = lay.leg_w
    # The ring's inner edge lies inside the bell's wall where the bell stands on
    # it (the bell is a thin cone, hollow from below, about 25.6 to 26.8 mm out
    # at the ring's top), so the bell's footprint never splits the ring's top
    # into an inside and an outside piece, and the hollow stays open below.
    # The legs start just clear of the bell and overlap the ring's outer part.
    clear_r = bell_r + 0.5 + leg_w * 0.5
    plug = _ring("MF_Mold_Plug", px, py, bell_r - 1.3, clear_r + 2.0,
                 z0 - pt, z0, coll)
    starts = []
    for (a, _wall, _ok) in lay.legs:
        dv = Vector((math.cos(a), math.sin(a)))
        rel = Vector((lay.cx - px, lay.cy - py))
        b = rel.dot(dv)
        disc = b * b - (rel.length_squared - clear_r * clear_r)
        starts.append(-b + math.sqrt(max(disc, 0.0)))   # where the leg leaves the bell
    _add_legs(plug, lay, coll, r_starts=starts)
    # bell rim sunk 0.5 into the ring, whose top is the rim plane
    column = _vac_u_lock_column(d, ln, z0, coll, px, py)
    util.boolean(plug, column, 'UNION')
    util.remove_object(column)
    util.fix_folded_faces(plug)
    cx, cy = lay.cx, lay.cy
    plug["mf_plug_offset"] = [px - cx, py - cy]
    plug["mf_plug_wall"] = wall
    off = math.hypot(px - cx, py - cy)
    if off < 1.0:
        note = f"plug at the base centre, {wall:.0f} mm of toy around it"
    else:
        note = (f"plug set {off:.0f} mm off the base centre, where the toy is "
                f"thickest ({wall:.0f} mm of toy around it)")
    return plug, note


def build_dual_kit_open(dual_src, lay, props, coll):
    """Dual-density kit for an OPEN bottom: the same eroded, ridge-rounded core
    as on a Locking Base (``erode_solid``), carried by four legs on the shells'
    bottom rim instead of a socket cross. The core's lowest band becomes a
    straight skirt down into the legs, the legs end in hooks that centre it on
    the outer wall, and the open spaces between the legs let the displaced
    soft silicone burp out when it is pressed in.

    ``Core_Master`` is molded in a SECOND MoldForge run (Bottom: Flat) and cast
    in FIRM silicone, legs and all; then the main mold is filled inverted with
    SOFT and the core seated on the rim. Not MF_-prefixed: it survives
    regeneration and passes the master-name guard for its own run. Consumes
    ``dual_src``."""
    mn, mx = util.world_bbox(dual_src)
    width = min(mx.x - mn.x, mx.y - mn.y)
    wall = max(min(getattr(props, "core_wall", 5.0), width * 0.4), 0.5)
    core = dual_src
    core.name = "Core_Master"
    floor_z = lay.z0 - FORMER_PLATE_T * 0.5         # halfway into the legs
    core_voxel = erode_solid(core, wall, coll, getattr(props, "detail_voxel", 1.0))
    if core_voxel:
        cmn_e = util.world_bbox(core)[0]
        cut = _skirt_cut(cmn_e.z, wall)
    else:
        # Too thin to erode: the uniform shrink about the base centre, height
        # compensated so the top gap matches.
        sc = max(1.0 - 2.0 * wall / max(width, 1e-6), 0.3)
        cx, cy = (mn.x + mx.x) * 0.5, (mn.y + mx.y) * 0.5
        h = max(mx.z - mn.z, 1e-6)
        scz = max(min(1.0 - wall / h, 1.0), 0.4)
        core.data.transform(
            Matrix.Translation((cx, cy, mn.z))
            @ Matrix.Diagonal((sc, sc, scz, 1.0))
            @ Matrix.Translation((-cx, -cy, -mn.z)))
        core.data.update()
        cut = mn.z + 1.0
    if not _skirt_to_base(core, cut, floor_z):
        # No clean section to extrude: a plain stem from the core's bottom
        # slice down into the legs keeps the kit one piece.
        if core_voxel and util.nonmanifold_count(core) > 0:
            meshprep.voxel_remesh(core, core_voxel)
        stem = _footprint_prism(core, 0.0, floor_z, util.world_bbox(core)[0].z + 1.0,
                                props, coll, "MF_corestem")
        util.boolean(core, stem, 'UNION')
        util.remove_object(stem)
    _add_legs(core, lay, coll, fasten=False)
    util.fix_folded_faces(core)
    return core


# The user's combined Vac-U-Lock + suction-cup former (their VUL-PLUG.stl),
# bundled AT ITS AUTHORED SIZE - base at z=0, centred, nothing rescaled.
# Bottom-up: suction bell (Ø54.6), short neck, ridged plug, 91.8 mm total.
_VAC_STL_NATIVE_D = 26.79      # widest plug tier, as the user modeled it
_VAC_STL_NATIVE_CUP = 54.59    # suction-bell rim
_VAC_STL_NATIVE_H = 91.83


def _vac_u_lock_column(d, ln, base_z, coll, x=0.0, y=0.0):
    """The plug column: the bundled real Vac-U-Lock mesh, scaled uniformly so
    its widest tier equals ``d`` and capped so it still fits the cavity
    (``ln``); base sunk 0.5 into the cross for the union weld. Falls back to
    a parametric two-tier silhouette if the asset file is missing."""
    path = os.path.join(os.path.dirname(__file__), "..", "assets",
                        "vac_u_lock.stl")
    obj = None
    if os.path.isfile(path):
        try:
            before = set(bpy.data.objects)
            bpy.ops.wm.stl_import(filepath=path)
            fresh = [o for o in bpy.data.objects if o not in before]
            if fresh:
                obj = fresh[0]
                for extra in fresh[1:]:
                    util.remove_object(extra)
        except Exception:
            obj = None
    if obj is not None:
        for c in list(obj.users_collection):
            c.objects.unlink(obj)
        coll.objects.link(obj)
        obj.name = "MF_plugcolumn"
        s = min(d / _VAC_STL_NATIVE_D, ln / _VAC_STL_NATIVE_H)
        obj.data.transform(Matrix.Translation((x, y, base_z - 0.5))
                           @ Matrix.Diagonal((s, s, s, 1.0)))
        return obj
    # fallback: parametric neck + flaring tier + bullet dome
    r = min(d, ln / _VAC_STL_NATIVE_H * _VAC_STL_NATIVE_D) * 0.5
    ln = min(ln, _VAC_STL_NATIVE_H * (r * 2.0) / _VAC_STL_NATIVE_D)
    col = util.add_cone("MF_plugcolumn",
                        Vector((x, y, base_z + 0.11 * ln - 0.25)),
                        r * 0.60, r * 0.60, 0.22 * ln + 0.5, 'Z', coll)
    band = util.add_cone("MF_plugband",
                         Vector((x, y, base_z + 0.35 * ln)),
                         r * 0.70, r * 0.96, 0.30 * ln, 'Z', coll)
    util.boolean(col, band, 'UNION')
    util.remove_object(band)
    dome_r = r * 0.95
    z_eq = base_z + ln - dome_r
    bullet = util.add_cone("MF_plugbullet",
                           Vector((x, y, (base_z + 0.48 * ln + z_eq) * 0.5)),
                           r * 0.80, dome_r, max(z_eq - (base_z + 0.48 * ln), 1.0),
                           'Z', coll)
    util.boolean(col, bullet, 'UNION')
    util.remove_object(bullet)
    dome = util.add_sphere("MF_plugdome", Vector((x, y, z_eq)),
                           dome_r, coll, subdiv=3)
    util.boolean(col, dome, 'UNION')
    util.remove_object(dome)
    return col




def _tray_hug_prism(master, offset, z0, z1, props, coll):
    """A vertical prism whose cross-section is the object's footprint expanded by
    ``offset`` (with rounded corners), spanning z0..z1. Returns (solid, area).

    The footprint-plus-offset is exactly the cross-section of the object's 3D
    dilation at mid-height (projection commutes with the Minkowski offset), so we
    slice a thin cross-section off the dilation and stretch it vertically into a
    clean vertical-walled prism — no 2D outline maths needed."""
    d = _dilate_solid(master, offset, "MF_thug", props, coll)
    mn, mx = util.world_bbox(d)
    midz = (mn.z + mx.z) * 0.5
    t = max((mx.z - mn.z) * 0.2, 0.2)               # thin slice, vertical walls there
    big = (mx - mn).length * 2.0 + 10.0
    slab = util.add_box("MF_thugs", Vector((0.0, 0.0, midz)), Vector((big, big, t)), coll)
    util.boolean(d, slab, 'INTERSECT')
    util.remove_object(slab)
    util.remove_small_islands(d)
    area = volume.mesh_volume(d) / t                 # prism volume / its height
    d.data.transform(Matrix.Translation((0.0, 0.0, -midz)))            # centre on origin
    d.data.transform(Matrix.Diagonal((1.0, 1.0, (z1 - z0) / t, 1.0)))  # stretch to height
    d.data.transform(Matrix.Translation((0.0, 0.0, (z0 + z1) * 0.5)))  # move to the band
    d.data.update()
    return d, area


def build_tray(master, props, coll):
    """One-part open tray (pan) for flat & relief objects.

    ``master`` arrives already oriented (detail face up, +Z) and centred at the
    origin. Builds an open-top pan — floor + walls — around the object's footprint,
    then applies the chosen mode:

    * EMBED  — UNION the object into the floor (a master to pour silicone over).
    * FRAME  — nothing embedded (drop a real object in).

    The outline is either a rectangle (RECT) or a rounded shape that hugs the
    object (HUG — less silicone/plastic), with a safe fallback to RECT. No split,
    wings, funnel or undercut handling: a flat object's flexible silicone releases
    straight up. Returns ``(pan, info)`` with the pour/cast/plastic volumes.
    """
    mn, mx = util.world_bbox(master)
    fx, fy = mx.x - mn.x, mx.y - mn.y
    obj_bot, obj_top = mn.z, mx.z
    obj_h = max(obj_top - obj_bot, 1e-4)

    wall = max(props.tray_wall, 0.4)
    floor = max(props.tray_floor, 0.4)
    margin = max(props.tray_margin, 0.0)
    pour = max(props.tray_depth, 0.0)
    mode = getattr(props, "tray_mode", 'EMBED')
    outline = getattr(props, "tray_outline", 'RECT')

    overlap = min(floor * 0.5, max(obj_h * 0.3, C.TRAY_WELD_OVERLAP))
    cavity_floor_top = obj_bot + (overlap if mode == 'EMBED' else 0.0)
    floor_bottom = cavity_floor_top - floor
    rim_z = max(obj_top + pour, cavity_floor_top + 0.5)
    cut_top = rim_z + max(fx, fy, obj_h) + pour + floor + 10.0   # open the top

    pan = None
    cav_area = None
    if outline == 'HUG':
        try:
            pan, _a_out = _tray_hug_prism(master, margin + wall, floor_bottom, rim_z,
                                          props, coll)
            cutter, cav_area = _tray_hug_prism(master, margin, cavity_floor_top, cut_top,
                                               props, coll)
            util.boolean(pan, cutter, 'DIFFERENCE')
            util.remove_object(cutter)
            util.remove_small_islands(pan)
            if not util.part_is_valid(pan)[0]:       # fall back to a plain rectangle
                util.remove_object(pan)
                pan = None
        except Exception:
            for o in list(coll.objects):
                if o.name.startswith(("MF_thug", "MF_tray")):
                    util.remove_object(o)
            pan = None

    if pan is None:                                  # RECT (default, and HUG fallback)
        ihx, ihy = fx * 0.5 + margin, fy * 0.5 + margin
        ohx, ohy = ihx + wall, ihy + wall
        cav_area = (2.0 * ihx) * (2.0 * ihy)
        pan = util.add_box("MF_tray", Vector((0.0, 0.0, (floor_bottom + rim_z) * 0.5)),
                           Vector((2.0 * ohx, 2.0 * ohy, rim_z - floor_bottom)), coll)
        cutter = util.add_box(
            "MF_trayc", Vector((0.0, 0.0, (cavity_floor_top + cut_top) * 0.5)),
            Vector((2.0 * ihx, 2.0 * ihy, cut_top - cavity_floor_top)), coll)
        util.boolean(pan, cutter, 'DIFFERENCE')
        util.remove_object(cutter)

    cavity_vol = cav_area * max(rim_z - cavity_floor_top, 0.0)

    if mode == 'EMBED':
        emb = util.duplicate_object(master, "MF_traypos", coll)
        util.boolean(pan, emb, 'UNION')
        util.remove_object(emb)
        obj_vol = volume.mesh_volume(master)
        silicone_vol = max(cavity_vol - obj_vol, 0.0)
        cast_vol = obj_vol
    else:  # FRAME
        silicone_vol = cavity_vol
        cast_vol = 0.0

    util.remove_small_islands(pan)
    pan.name = "MF_Mold_A"
    info = {
        "silicone_volume": silicone_vol,
        "cast_volume": cast_vol,
        "plastic_volume": volume.mesh_volume(pan),
    }
    return pan, info


def _add_skin_keys(inner, gap, shell, coll):
    """Glove-mold registration: small domes raised on the silicone skin's outer
    surface (unioned onto ``inner`` before the jacket is differenced, so the
    jacket automatically gets matching pockets). Four bumps at compass points
    around mid-height, placed by ray-casting the skin surface; the protrusion is
    capped so a pocket never pierces the jacket wall. Flexible silicone pops out
    of the pockets on demold."""
    mn, mx = util.world_bbox(inner)
    zc = (mn.z + mx.z) * 0.5
    cx, cy = (mn.x + mx.x) * 0.5, (mn.y + mx.y) * 0.5
    r = max(min(gap * 0.8, shell * 0.9), 0.8)
    reach = (mx - mn).length + 10.0
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        d = Vector((dx, dy, 0.0))
        hit, loc, _n, _i = inner.ray_cast(Vector((cx, cy, zc)) + d * reach, -d)
        if not hit:
            continue
        c = Vector(loc) - d * (r * 0.4)        # 0.6 r proud of the skin surface
        bump = util.add_sphere("MF_skinkey", c, r, coll)
        util.boolean(inner, bump, 'UNION')
        util.remove_object(bump)


def _build_skin_preview(inner, master, coll):
    """The thin silicone skin solid (``inner - model``) kept as ``MF_Skin`` for the
    user to inspect. Best-effort: a glitch here must never break the printed shell,
    so any failure just yields no preview."""
    try:
        skin = util.duplicate_object(inner, "MF_Skin", coll)
        util.boolean(skin, master, 'DIFFERENCE')
        util.remove_small_islands(skin)
        if not skin.data.polygons:
            util.remove_object(skin)
            return None
        return skin
    except Exception:
        for o in list(coll.objects):
            if o.name.startswith("MF_Skin"):
                util.remove_object(o)
        return None


def flatten_base(mold, props, coll, max_cut=None):
    """Slice everything below the cut height off the bottom, leaving a flat face
    the closed mold can stand and print on.

    ``max_cut`` caps how deep the cut may go (the pour box passes this so the
    flat base never eats through the thin bottom wall into the cavity).
    """
    cut = props.flat_base_cut
    if max_cut is not None:
        cut = min(cut, max_cut)
    if cut <= 0.0:
        return

    mn, mx = util.world_bbox(mold)
    base_z = min(mn.z + cut, mx.z - 0.1)
    if base_z <= mn.z:
        return  # nothing to remove

    size = mx - mn
    big = max(size.x, size.y, size.z) * 2.0 + 10.0
    bottom = mn.z - big
    center = Vector((
        (mn.x + mx.x) * 0.5,
        (mn.y + mx.y) * 0.5,
        (base_z + bottom) * 0.5,
    ))
    cutter = util.add_box("MF_basecut", center, Vector((big, big, base_z - bottom)), coll)
    util.boolean(mold, cutter, 'DIFFERENCE')
    util.remove_object(cutter)


def cut_below_z(mold, z, coll):
    """Remove everything below absolute height ``z`` (used for an open bottom:
    cut at the master's base so the cavity is open and the master can sit on the
    build plate). The wall mesh stays watertight."""
    mn, mx = util.world_bbox(mold)
    if z <= mn.z + 1e-6:
        return
    z = min(z, mx.z - 1e-4)
    size = mx - mn
    big = max(size.x, size.y, size.z) * 2.0 + 10.0
    bottom = mn.z - big
    center = Vector((
        (mn.x + mx.x) * 0.5,
        (mn.y + mx.y) * 0.5,
        (z + bottom) * 0.5,
    ))
    cutter = util.add_box("MF_basecut", center, Vector((big, big, z - bottom)), coll)
    util.boolean(mold, cutter, 'DIFFERENCE')
    if util.world_bbox(mold)[0].z < z - 0.05:
        # The cut did not take. The fast solver refuses some inputs SILENTLY (a
        # self-intersection left by an earlier union, a coplanar bottom) and leaves
        # the mesh untouched - which shipped shells with a 2 mm fin hanging under
        # the socket mouth along the seam. Retry with the tolerant solver.
        util.boolean(mold, cutter, 'DIFFERENCE', solver='EXACT')
    util.remove_object(cutter)


def _spherical_cap(name, cx, cy, z0, r_cap, depth, coll, segs=144, rings=56):
    """A HIGH-POLY closed spherical-cap solid: footprint radius ``r_cap`` on the plane
    ``z0``, apex ``depth`` above it, built as a smooth stack of rings (like the locking
    plinth) — no icosphere faceting, no boolean clip seam."""
    rs = (r_cap * r_cap + depth * depth) / (2.0 * depth)   # sphere radius through rim+apex
    zc = z0 + depth - rs                                   # sphere centre height
    bm = bmesh.new()
    ring_rows = []
    for i in range(rings):
        z = z0 + depth * i / rings
        dz = z - zc
        r = math.sqrt(max(rs * rs - dz * dz, 1e-12))
        ring_rows.append([bm.verts.new((cx + r * math.cos(2.0 * math.pi * j / segs),
                                        cy + r * math.sin(2.0 * math.pi * j / segs), z))
                          for j in range(segs)])
    apex = bm.verts.new((cx, cy, z0 + depth))
    for i in range(rings - 1):
        for j in range(segs):
            jn = (j + 1) % segs
            bm.faces.new((ring_rows[i][j], ring_rows[i][jn],
                          ring_rows[i + 1][jn], ring_rows[i + 1][j]))
    for j in range(segs):
        jn = (j + 1) % segs
        bm.faces.new((ring_rows[-1][j], ring_rows[-1][jn], apex))
    base = bm.verts.new((cx, cy, z0))
    for j in range(segs):
        jn = (j + 1) % segs
        bm.faces.new((ring_rows[0][jn], ring_rows[0][j], base))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return util.new_mesh_object(name, bm, coll)


# --- Formers that seat on the shells' bottom rim ---------------------------- #
# The suction-cup former, and on an Open bottom the Vac-U-Lock plug and the dual
# core too, hang from four diagonal legs that rest on the shells' bottom rim, each
# leg ending in a hook up the outer wall. The rim is measured ONCE per mold and
# shared, so the shells get a single set of fastening grooves whatever is built.

FORMER_PLATE_T = 3.0     # leg / backing-plate thickness
FORMER_TAB_T = 2.4       # hook thickness
FORMER_HOOK_H = 3.5      # hook height above the rim
FORMER_LEG_W = 8.0       # leg width of the plug and core carriers
FORMER_BEAD_R = 0.75     # snap bead on each hook; its groove is cut into the shells


def _base_centre_xy(obj, z):
    """Area centroid of ``obj``'s cross-section at height ``z`` - the middle of
    the base outline, which on a leaning toy is not its bounding-box centre -
    or None when there is no clean section there."""
    loop = _cross_section_loop(obj, z)
    if not loop or len(loop) < 3:
        return None
    area = cx = cy = 0.0
    n = len(loop)
    for i in range(n):
        x0, y0 = loop[i]
        x1, y1 = loop[(i + 1) % n]
        cr = x0 * y1 - x1 * y0
        area += cr
        cx += (x0 + x1) * cr
        cy += (y0 + y1) * cr
    if abs(area) < 1e-9:
        return None
    return cx / (3.0 * area), cy / (3.0 * area)


def former_centre(model, base_z):
    """Where a former centres: the middle of the model's outline just above
    ``base_z`` (the cast opening), falling back to the bounding-box centre."""
    c = _base_centre_xy(model, base_z + 1.0)
    if c is None:
        mn, mx = util.world_bbox(model)
        c = ((mn.x + mx.x) * 0.5, (mn.y + mx.y) * 0.5)
    return c


def rim_layout(mold, cx, cy, base_z, props, leg_w=FORMER_LEG_W):
    """How a former's four legs meet the shells' bottom rim, measured off the
    whole mold (before the split): the rim height, and per diagonal leg the
    outer wall's reach and whether a fastening groove can be cut there.

    Everything is measured in the mold's BOTTOM BAND only (within ~3 mm of the
    rim): a mold that flares out just above its base (a bulging model over a
    short skirt) must not stretch the legs to the bulge - a single probe 4 mm up
    once did exactly that, leaving metre-long legs with floating hooks. Each
    leg's reach is the LOCAL bottom-edge wall (max of two low probes, so a
    slightly leaning skirt still clears), clamped to the band's true footprint
    from the mold's own vertices. The legs run on the DIAGONALS so they clear
    the clamp wings.

    A key plate's ring tongue hangs under the shells' rim, narrower than the
    wall: where the lowest probe reads a step in, the fastening height moves up
    to where the full wall starts, so a pin or bead always bites real wall. The
    mold sits at the origin here (identity matrix), so ray casts use world
    coordinates directly."""
    mn, mx = util.world_bbox(mold)
    z0 = mn.z                                       # the shells' bottom rim
    band_top = z0 + 3.0
    rim_max = 0.0
    for v in mold.data.vertices:
        if v.co.z <= band_top:
            rim_max = max(rim_max, math.hypot(v.co.x - cx, v.co.y - cy))
    if rim_max <= 0.0:
        rim_max = max(mx.x - mn.x, mx.y - mn.y) * 0.5
    probe_hi = z0 + max(min(2.4, (base_z - z0) - 0.5 if base_z > z0 + 1.0 else 2.4), 1.2)
    reach = (mx - mn).length + 10.0

    def wall_at(d, pz):
        hit, loc, _n, _i = mold.ray_cast(Vector((cx, cy, pz)) + d * reach, -d)
        return math.hypot(loc.x - cx, loc.y - cy) if hit else None

    legs = []
    wall_start = z0
    for k in range(4):
        a = math.pi * 0.25 + k * math.pi * 0.5
        d = Vector((math.cos(a), math.sin(a), 0.0))
        lo, hi = wall_at(d, z0 + 0.8), wall_at(d, probe_hi)
        hits = [h for h in (lo, hi) if h is not None]
        wall = max(hits) if hits else None
        snap_ok = wall is not None and wall <= rim_max   # a real, unclamped wall hit
        if wall is None:
            wall = rim_max
        wall = min(wall, rim_max)                # never past the band's real footprint
        if lo is not None and hi is not None and lo < hi - 0.3:
            z = z0 + 0.8
            while z < z0 + 8.0:
                z += 0.25
                h = wall_at(d, z)
                if h is not None and h >= hi - 0.15:
                    break
            wall_start = max(wall_start, z)
        legs.append((a, wall, snap_ok))
    bead_z = max(z0 + 1.5, wall_start + 1.5)
    return types.SimpleNamespace(
        z0=z0, cx=cx, cy=cy, legs=legs, rim_max=rim_max, leg_w=leg_w,
        clear=max(getattr(props, "fit_clearance", 0.2), 0.0),
        lock=getattr(props, "cup_lock", 'PIN'),
        bead_z=bead_z, hook_top=max(z0 + FORMER_HOOK_H, bead_z + 2.0))


def _leg_solid(lay, a, wall, snap_ok, leg_w, r_start, fasten, coll):
    """One leg with its hook as a SINGLE clean solid: the side profile - the leg
    along the bottom, the hook up the shells' outer wall, and the fastening
    feature (PIN half-channel in the hook face, SNAP bead on it, BAND lip on
    its back) - extruded across the leg's width. Unioning a hook box onto a
    leg box instead leaves faces the two share (bottom, sides, end) that the
    fast boolean stitched into zero-area spikes: inverted triangles in the
    export and black patches in a render."""
    z0, clear = lay.z0, lay.clear
    pt, tt = FORMER_PLATE_T, FORMER_TAB_T
    r_in = wall + clear                      # hook's inner face
    r_end = r_in + tt                        # hook's outer face = the leg's end
    top = lay.hook_top
    prof = [(r_start, z0 - pt), (r_end, z0 - pt)]
    band = fasten and lay.lock == 'BAND'
    if band:
        prof += [(r_end, top - 1.6), (r_end + 2.2, top - 1.6), (r_end + 2.2, top)]
    else:
        prof += [(r_end, top)]
    prof += [(r_in, top)]
    if fasten and snap_ok and lay.lock == 'SNAP':
        # bead: a half-round rib standing out of the hook's inner face
        br = FORMER_BEAD_R
        prof.append((r_in, lay.bead_z + br))
        for k in range(1, 12):
            ang = math.pi * 0.5 + math.pi * k / 12.0
            prof.append((r_in + br * math.cos(ang), lay.bead_z + br * math.sin(ang)))
        prof.append((r_in, lay.bead_z - br))
    elif fasten and snap_ok and lay.lock == 'PIN':
        # half-channel: the pin's circle (centred 0.55 out) cut into the face
        cr, cc = 1.3, wall + 0.55
        h = math.sqrt(max(cr * cr - (r_in - cc) ** 2, 0.0))
        a0 = math.atan2(h, r_in - cc)
        prof.append((r_in, lay.bead_z + h))
        for k in range(1, 16):
            ang = a0 - (2.0 * a0) * k / 16.0
            prof.append((cc + cr * math.cos(ang), lay.bead_z + cr * math.sin(ang)))
        prof.append((r_in, lay.bead_z - h))
    prof += [(r_in, z0), (r_start, z0)]
    d = Vector((math.cos(a), math.sin(a), 0.0))
    t = Vector((-d.y, d.x, 0.0))
    c = Vector((lay.cx, lay.cy, 0.0))
    bm = bmesh.new()
    sides = []
    for off in (-0.5 * leg_w, 0.5 * leg_w):
        sides.append([bm.verts.new(c + d * r + t * off + Vector((0.0, 0.0, z)))
                      for (r, z) in prof])
    n = len(prof)
    bm.faces.new(sides[0])
    bm.faces.new(list(reversed(sides[1])))
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((sides[0][i], sides[1][i], sides[1][j], sides[0][j]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return util.new_mesh_object("MF_cupleg", bm, coll)


def _add_legs(part, lay, coll, leg_w=None, fasten=True, r_start=None, r_starts=None):
    """Weld the four diagonal legs onto ``part``: each runs from inside the
    former (``r_start`` from its centre; by default just clear of the other
    legs, so no two legs overlap) out under the rim and ends in a hook that
    hugs the outer wall with Fit Clearance, so the former registers centred
    on the mold. Each leg is one clean solid (``_leg_solid``).

    FASTENING: the pour FLOATS a former (it displaces silicone and buoyancy
    pushes it off the mold), so resting legs aren't enough - it must be held
    DOWN. Three ways, picked by material (the shells' side is cut by
    ``cut_rim_grooves``):
     PIN  - resin-safe, zero flex: a tangential half-channel through the hook's
            inner face lines up with a groove in the shell wall; the former
            slides on freely and a ~2 mm pin (skewer / 1.75 mm filament / nail)
            slides in, sitting half in the hook, half in the wall. Pure shear.
     SNAP - a printed bead clicks into the shell groove; needs a filament that
            flexes (PLA/PETG). Brittle resin hooks would crack.
     BAND - an outward lip on each hook catches rubber bands; nothing is cut
            into the shells at all."""
    leg_w = lay.leg_w if leg_w is None else leg_w
    if r_start is None:
        r_start = leg_w * 0.5 + 0.6          # four legs at 90 degrees never meet
    for k, (a, wall, snap_ok) in enumerate(lay.legs):
        rs = r_starts[k] if r_starts is not None else r_start
        leg = _leg_solid(lay, a, wall, snap_ok, leg_w, rs, fasten, coll)
        util.boolean(part, leg, 'UNION')
        util.remove_object(leg)


def cut_rim_grooves(mold, lay, coll, leg_w=None):
    """The shells' half of the fastening, cut once for every former that seats
    on this rim: the SNAP bead grooves or the PIN grooves (BAND cuts nothing).
    ``leg_w`` is the widest leg that will seat here."""
    if lay.lock == 'BAND':
        return
    leg_w = lay.leg_w if leg_w is None else leg_w
    cx, cy, clear = lay.cx, lay.cy, lay.clear
    for (a, wall, snap_ok) in lay.legs:
        if not snap_ok:
            continue
        d = Vector((math.cos(a), math.sin(a), 0.0))
        if lay.lock == 'SNAP':
            cut = util.add_cone("MF_cupgroove",
                                Vector((cx, cy, lay.bead_z)) + d * (wall + clear),
                                FORMER_BEAD_R + clear, FORMER_BEAD_R + clear,
                                leg_w * 0.9 + 2.0, 'X', coll, segments=24,
                                rot_z=a + math.pi * 0.5)
        else:
            cut = util.add_cone("MF_cupchan",
                                Vector((cx, cy, lay.bead_z)) + d * (wall + 0.55),
                                1.3, 1.3, leg_w * 0.9 + 2.0, 'X', coll, segments=24,
                                rot_z=a + math.pi * 0.5)
        util.boolean(mold, cut, 'DIFFERENCE')
        util.remove_object(cut)


def _revolve(name, cx, cy, prof, coll, segs=144):
    """A closed solid of revolution about the vertical axis through (cx, cy).
    ``prof`` is the outline as ``(r, z)`` points, from a point ON the axis (r=0)
    round to another point on the axis; repeated points are skipped."""
    pts = []
    for (r, z) in prof:
        if not pts or abs(r - pts[-1][0]) > 1e-6 or abs(z - pts[-1][1]) > 1e-6:
            pts.append((r, z))
    bm = bmesh.new()
    bottom = bm.verts.new((cx, cy, pts[0][1]))
    top = bm.verts.new((cx, cy, pts[-1][1]))
    rings = []
    for (r, z) in pts[1:-1]:
        rings.append([bm.verts.new((cx + r * math.cos(2.0 * math.pi * j / segs),
                                    cy + r * math.sin(2.0 * math.pi * j / segs), z))
                      for j in range(segs)])
    for j in range(segs):
        jn = (j + 1) % segs
        bm.faces.new((rings[0][jn], rings[0][j], bottom))
        for i in range(len(rings) - 1):
            bm.faces.new((rings[i][j], rings[i][jn], rings[i + 1][jn], rings[i + 1][j]))
        bm.faces.new((rings[-1][j], rings[-1][jn], top))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return util.new_mesh_object(name, bm, coll)


def _ring(name, cx, cy, r_in, r_out, z_lo, z_hi, coll, segs=144):
    """A flat ring (washer) built directly as quads - never a cylinder minus a
    cylinder, whose cap would be one face with a hole in it (the fast boolean
    then leaves slit faces wherever something stands on it)."""
    bm = bmesh.new()
    rings = []
    for (r, z) in ((r_in, z_lo), (r_out, z_lo), (r_out, z_hi), (r_in, z_hi)):
        rings.append([bm.verts.new((cx + r * math.cos(2.0 * math.pi * j / segs),
                                    cy + r * math.sin(2.0 * math.pi * j / segs), z))
                      for j in range(segs)])
    for i in range(4):
        a, b = rings[i], rings[(i + 1) % 4]
        for j in range(segs):
            jn = (j + 1) % segs
            bm.faces.new((a[j], a[jn], b[jn], b[j]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return util.new_mesh_object(name, bm, coll)


def build_suction_cup(mold, master, props, coll, base_z=None, lay=None):
    """Printed suction-cup FORMER (``MF_Mold_Cup``) for a mold with an open bottom —
    Open Bottom (with or without the key plate) or a Locking Base (whose bottom sits
    Base Height LOWER; everything here is measured off the mold itself, so the
    changed height is handled automatically).

    A smooth HIGH-POLY dome (Cup Diameter x Cup Depth, a ring-built spherical cap) sits
    on a backing plate with four diagonal legs (``rim_layout`` / ``_add_legs``); the
    legs rest on the mold's bottom rim and each ends in an upright tab that hugs the
    outer wall with Fit Clearance, so the former registers centred on the mold.
    Workflow: cast with the mold inverted (open base up), fill, seat the former so the
    dome presses into the pour, and the material cures around it — leaving a
    suction-cup bell in the cast's base. Pop the former out after cure.

    ``base_z`` is the cast opening's plane (default: the model's base; a key plate's
    top when the plate is on). With ``lay`` given, the legs follow that shared rim
    layout and the shells' fastening grooves are left to the caller
    (``cut_rim_grooves``, once for every former); the leg width used is stored on
    the part as ``mf_leg_w``.

    The bottom OPENING is measured by ray-casting the mold's own inner wall just above
    the bottom (so an Open Bottom's cavity, a Locking Base's wider sawtooth socket, or a
    direct mold all give the right fit)."""
    mn, mx = util.world_bbox(mold)
    z0 = mn.z                                       # the shells' bottom rim
    mmn, mmx = util.world_bbox(master)
    depth = max(getattr(props, "cup_depth", 8.0), 1.0)
    if base_z is None:
        base_z = mmn.z                              # the MODEL's base = the cast opening
                                                    # plane. With a Locking Base this sits
                                                    # Base Height ABOVE the shells' bottom
                                                    # (the socket below holds the base, not
                                                    # the cast) — the dome must press into
                                                    # the pour HERE, not at the rim.
    if lay is not None:
        cx, cy = lay.cx, lay.cy
    else:
        cx, cy = former_centre(master, base_z)

    # The cast opening: the model's own base footprint, measured by raying the MASTER
    # from inside just above its base (and partway up the dome's rise). This sizes the
    # AUTO cup only — an explicit Cup Diameter may be wider (the bell then truncates at
    # the opening, which is often exactly what you want).
    open_r = None
    for pz in (base_z + 0.8, base_z + min(depth * 0.6, 6.0)):
        for k in range(16):
            a = 2.0 * math.pi * k / 16.0
            d = Vector((math.cos(a), math.sin(a), 0.0))
            hit, loc, _n, _i = master.ray_cast(Vector((cx, cy, pz)), d)
            if hit:
                rr = math.hypot(loc.x - cx, loc.y - cy)
                open_r = rr if open_r is None else min(open_r, rr)
    if open_r is None:                              # fallback: model bbox footprint
        open_r = min(mmx.x - mmn.x, mmx.y - mmn.y) * 0.5
    open_r = max(open_r, 2.0)

    clear = max(getattr(props, "fit_clearance", 0.2), 0.0)

    # The INSERTION limit — the one hard cap on any cup size: the former slides in
    # through the shells' bottom, so nothing may be wider than the narrowest interior
    # it passes (a Locking Base's sawtooth socket, or the cavity just above an open
    # rim). Measured off the mold's inner wall. Widen Base Margin for a bigger socket
    # (and so a bigger possible bell).
    ins_r = None
    if base_z > z0 + 0.5:
        ins_probes = (z0 + 0.8, (z0 + base_z) * 0.5, base_z - 0.5)
    else:
        ins_probes = (base_z + 0.8, base_z + min(depth * 0.6, 6.0))
    for pz in ins_probes:
        for k in range(16):
            a = 2.0 * math.pi * k / 16.0
            d = Vector((math.cos(a), math.sin(a), 0.0))
            hit, loc, _n, _i = mold.ray_cast(Vector((cx, cy, pz)), d)
            if hit:
                rr = math.hypot(loc.x - cx, loc.y - cy)
                ins_r = rr if ins_r is None else min(ins_r, rr)
    if ins_r is None:
        ins_r = open_r + 4.0

    if getattr(props, "cup_diameter", 0.0) > 0.0:
        # Explicit size: use it as typed, capped only by what physically inserts.
        r_cup = min(props.cup_diameter * 0.5, ins_r - clear - 0.5)
    else:
        # Auto: ~70% of the cast opening, still within the insertion limit.
        r_cup = min(open_r * 0.7, ins_r - clear - 0.5)
    r_cup = max(r_cup, 2.0)
    plate_t = FORMER_PLATE_T
    leg_w = max(r_cup * 0.5, 6.0)

    # ONE solid of revolution: the backing plate at the rim, a riser column when
    # the cast opening sits above the rim (a Locking Base's socket, the key
    # plate's top), and the high-poly dome pressing ``depth`` into the pour AT
    # THE OPENING'S PLANE. Built as one lathe profile, not unioned: the dome's
    # base and the face under it lie on one plane, and whether a boolean merges
    # two solids that only touch there comes down to floating-point noise (it
    # once left the dome a separate piece). The riser's top is the flat
    # shoulder that presses flush against the opening's rim; it is kept inside
    # the socket so the former always seats.
    base_z = max(base_z, z0)
    riser_r = r_cup                                 # no riser on an open rim
    prof = [(0.0, z0 - plate_t), (r_cup + 3.0, z0 - plate_t), (r_cup + 3.0, z0)]
    if base_z > z0 + 0.05:
        riser_r = max(min(r_cup + 3.0, ins_r - clear - 0.5), r_cup)
        prof += [(riser_r, z0), (riser_r, base_z)]
    rs = (r_cup * r_cup + depth * depth) / (2.0 * depth)   # sphere through rim + apex
    zc = base_z + depth - rs
    rings = 56
    for i in range(rings):
        z = base_z + depth * i / rings
        prof.append((math.sqrt(max(rs * rs - (z - zc) ** 2, 1e-12)), z))
    prof.append((0.0, base_z + depth))
    cup = _revolve("MF_Mold_Cup", cx, cy, prof, coll)

    own = lay is None
    if own:
        lay = rim_layout(mold, cx, cy, base_z, props, leg_w)
    _add_legs(cup, lay, coll, leg_w=leg_w)
    if own:
        cut_rim_grooves(mold, lay, coll, leg_w)
    cup["mf_leg_w"] = leg_w

    # HOLLOW from the underside: the dome becomes a constant-thickness shell and the
    # riser a tube, opened through the plate — same outer surface, far less filament.
    # Two safety rules keep the part one solid: the tube core is sized to the DOME
    # footprint (never the wider riser, which would undercut and sever the dome), and
    # the dome's inner void is only carved when it genuinely opens into the tube (or
    # through the plate) — a bulb dome whose void would be sealed stays solid.
    hollow_wall = 2.4
    core_r = min(riser_r, r_cup) - hollow_wall
    rs_dome = (r_cup * r_cup + depth * depth) / (2.0 * depth)
    cavity = None
    if core_r > 1.6:
        cavity = util.add_cone("MF_cuphollow",
                               Vector((cx, cy, (z0 - plate_t - 2.0 + base_z + 0.05) * 0.5)),
                               core_r, core_r, base_z + 0.05 - (z0 - plate_t - 2.0),
                               'Z', coll, segments=96)
    sphere_bottom = (base_z + depth - rs_dome) - (rs_dome - hollow_wall)
    sphere_ok = rs_dome - hollow_wall > 1.6 and depth > hollow_wall + 0.8
    if sphere_ok and cavity is not None and sphere_bottom < base_z - 0.8:
        # Shallow dome over the tube: carve the inner offset sphere, clipped a little
        # BELOW the dome base so the two voids overlap (no coplanar boolean seam).
        inner = util.add_sphere("MF_cupinner",
                                Vector((cx, cy, base_z + depth - rs_dome)),
                                rs_dome - hollow_wall, coll, subdiv=4)
        clipbox = util.add_box("MF_cupclip2",
                               Vector((cx, cy, base_z - 0.6 + rs_dome + 1.0)),
                               Vector((4.0 * rs_dome, 4.0 * rs_dome, 2.0 * rs_dome + 2.0)),
                               coll)
        util.boolean(inner, clipbox, 'INTERSECT')     # keep only z >= base_z - 0.6
        util.remove_object(clipbox)
        util.boolean(cavity, inner, 'UNION')
        util.remove_object(inner)
    elif sphere_ok and cavity is None and sphere_bottom < z0 - plate_t - 0.2:
        # No tube, but the inner sphere itself reaches through the plate: open hollow.
        cavity = util.add_sphere("MF_cupinner",
                                 Vector((cx, cy, base_z + depth - rs_dome)),
                                 rs_dome - hollow_wall, coll, subdiv=4)
    if cavity is not None:
        util.boolean(cup, cavity, 'DIFFERENCE')
        util.remove_object(cavity)
    util.fix_folded_faces(cup)
    return cup


def _rim_band(master, r_in, r_out, z0, z1, props, coll):
    """A band following the model's profile: the solid between dilations ``r_in``
    and ``r_out``, clipped to heights [z0, z1]. The chin / groove / ring of the
    detachable base are all such bands, so they mate along the whole perimeter
    whatever the model's footprint shape."""
    band = _dilate_solid(master, r_out, "MF_bb", props, coll)
    inner = _dilate_solid(master, max(r_in, 0.05), "MF_bi", props, coll)
    util.boolean(band, inner, 'DIFFERENCE')
    util.remove_object(inner)
    mn, mx = util.world_bbox(band)
    big = (mx - mn).length * 2.0 + 10.0
    clip = util.add_box("MF_bs", Vector(((mn.x + mx.x) * 0.5, (mn.y + mx.y) * 0.5,
                                         (z0 + z1) * 0.5)),
                        Vector((big, big, z1 - z0)), coll)
    util.boolean(band, clip, 'INTERSECT')
    util.remove_object(clip)
    return band


def plate_levels(props, model_min_z):
    """The key plate's two heights: its top (the pocket line the master stands
    in) and the shells' rim that rests on the chin collar above it."""
    gap = getattr(props, "silicone_gap", props.wall_thickness)
    pocket = max(min(gap * 0.4, 2.5), 1.0)
    cut_z = model_min_z + pocket
    return cut_z, cut_z + 1.6


def _band_slice(obj, z0, z1, coll, name):
    """A copy of ``obj`` clipped to the heights z0..z1, or None if empty."""
    sl = util.duplicate_object(obj, name, coll)
    mn, mx = util.world_bbox(sl)
    big = (mx - mn).length * 2.0 + 10.0
    box = util.add_box(name + "_b", Vector(((mn.x + mx.x) * 0.5, (mn.y + mx.y) * 0.5,
                                            (z0 + z1) * 0.5)),
                       Vector((big, big, z1 - z0)), coll)
    util.boolean(sl, box, 'INTERSECT')
    util.remove_object(box)
    if not sl.data.polygons:
        util.remove_object(sl)
        return None
    return sl


def _pocket_cutter(positive, fit, z0, z1, coll):
    """The full-detail positive's slice between z0 and z1, grown by ``fit`` on a
    fine distance grid: the pocket it cuts follows the printed master to a tenth
    of a millimetre. None when it can't be built (the caller falls back)."""
    sl = _band_slice(positive, z0, z1, coll, "MF_ppk")
    if sl is None:
        return None
    if fit <= 0.02:
        return sl
    try:
        mn, mx = util.world_bbox(sl)
        span = max(mx.x - mn.x, mx.y - mn.y, 1.0)
        v = max(fit * 0.5, span / 1500.0, 0.05)
        if util.nonmanifold_count(sl) > 0:
            meshprep.voxel_remesh(sl, v)
        _sdf_offset(sl, fit, v)
        if not sl.data.polygons:
            raise RuntimeError("empty pocket")
        return sl
    except Exception:
        util.remove_object(sl)
        return None


def cradle_base(base, positive, coll):
    """A Locking Base kept as its own part: its top band reaches into the model
    (the weld band of a united base). Carve the positive out of it so the base's
    top cradles the master's underside instead of overlapping it. Falls back to
    cutting the base flat at the model's lowest point when the carve does not
    come out as one clean solid."""
    pmn = util.world_bbox(positive)[0]
    bak = base.data.copy()
    vol0 = volume.mesh_volume(base)
    ok = False
    try:
        util.boolean(base, positive, 'DIFFERENCE')
        ok = (util.part_is_valid(base)[0]
              and volume.mesh_volume(base) < vol0 - 1e-6
              and util.world_bbox(base)[0].z < pmn.z - 0.5)
    except Exception:
        ok = False
    if ok:
        if bak.users == 0:
            bpy.data.meshes.remove(bak)
        return True
    cur = base.data
    base.data = bak
    if cur.users == 0:
        bpy.data.meshes.remove(cur)
    bmn, bmx = util.world_bbox(base)
    big = (bmx - bmn).length * 2.0 + 10.0
    box = util.add_box("MF_cradlecut", Vector(((bmn.x + bmx.x) * 0.5, (bmn.y + bmx.y) * 0.5,
                                               pmn.z + big * 0.5)),
                       Vector((big, big, big)), coll)
    util.boolean(base, box, 'DIFFERENCE')
    util.remove_object(box)
    return False


def add_base_plate(mold, master, props, coll, min_floor=0.0, pocket_src=None):
    """Detachable bottom: cut the mold open just above the master's base and close
    it with a separate one-piece printed plate (``MF_Mold_Base``).

    ``pocket_src`` is the full-detail positive: the pocket the master drops into is
    cut from it (``master`` may be a coarse remeshed proxy of a model that was not
    watertight, and a pocket from that let the printed master poke into the plate).

    ``min_floor`` is plate material the caller needs UNDER the master (a
    countersunk screw); the plate is thickened to give it. The plate's top (the
    cast opening's plane) is stored on it as ``mf_plate_top``.

    The plate registers BOTH mating parts. The master's bottom slice is
    differenced out of the plate top — a pocket the model drops into, aligning
    the positive and sealing the silicone around its base. The shell registers
    via a tongue-and-groove: a low collar ("chin") rises from the plate under the
    shell's rim with a groove sunk along its crest, and a matching ring tongue on
    the shell's rim drops into it — self-aligning around the whole perimeter and
    a labyrinth seal for the pour. Added before the split, so every half — or
    radial wedge — keeps its arc of the tongue. Returns the plate object."""
    mmn, _mmx = util.world_bbox(master)
    jacket = props.box_style == 'POUR_BOX'
    gap = getattr(props, "silicone_gap", props.wall_thickness)
    shell = props.shell_wall if jacket else props.wall_thickness
    wall_in = gap if jacket else 0.0           # rim band starts here (from the model)
    mid = wall_in + shell * 0.5                # rim wall centreline

    fit = max(getattr(props, "fit_clearance", 0.2), 0.0)   # clearance per mating face
    pocket = max(min(gap * 0.4, 2.5), 1.0)     # same as plate_levels()
    ch = 1.6                                   # chin height
    rw = max(min(shell * 0.5, 1.6), 0.8)       # ring tongue width
    gw = rw + 2.0 * fit                        # groove: fit wider than the tongue PER SIDE
    gd = ch + fit + 0.3                        # groove depth below the chin crest
    rh = gd - fit                              # tongue length (fit axial clearance)
    cut_z = mmn.z + pocket                     # plate top / pocket line
    rim_z = cut_z + ch                         # shell rim rests on the chin crest
    cut_below_z(mold, rim_z, coll)

    mn, mx = util.world_bbox(mold)
    margin = 3.0
    # under the master's lowest point the pocket leaves ``t - pocket - fit``
    t = max(shell * 1.5, 3.0, pocket + fit + min_floor)
    cx, cy = (mn.x + mx.x) * 0.5, (mn.y + mx.y) * 0.5
    # Contoured plate: the model's base footprint grown just past the shell rim,
    # as a flat slab (flat top/bottom, sides hugging the model's outline) — not a
    # square slab. Built from a model dilation clipped to the plate's thin z-band.
    plate = _dilate_solid(master, wall_in + shell + margin, "MF_Mold_Base", props, coll)
    big = (mx - mn).length * 2.0 + 10.0
    clip = util.add_box("MF_pclip", Vector((cx, cy, cut_z - t * 0.5)),
                        Vector((big, big, t)), coll)
    util.boolean(plate, clip, 'INTERSECT')
    util.remove_object(clip)
    util.remove_small_islands(plate)
    # Model-bottom registration pocket, opened by ``fit`` per face so the printed
    # model actually drops in (an exact cut would print too tight).
    pocket_cut = None
    if pocket_src is not None:
        pocket_cut = _pocket_cutter(pocket_src, fit, util.world_bbox(plate)[0].z - 1.0,
                                    cut_z + fit + 1.0, coll)
    if pocket_cut is None:
        pocket_cut = (_dilate_solid(master, fit, "MF_ppk", props, coll) if fit > 0.05
                      else util.duplicate_object(master, "MF_ppk", coll))
    util.boolean(plate, pocket_cut, 'DIFFERENCE')
    util.remove_object(pocket_cut)

    # Chin collar on the plate (0.2 overlap into the slab for a clean weld).
    chin = _rim_band(master, wall_in - 1.2, wall_in + shell + 1.2,
                     cut_z - 0.2, rim_z, props, coll)
    if chin.data.polygons:
        util.boolean(plate, chin, 'UNION')
    util.remove_object(chin)

    # Groove sunk along the chin's crest (cuts a little into the slab too).
    groove = _rim_band(master, mid - gw * 0.5, mid + gw * 0.5,
                       rim_z - gd, rim_z + 5.0, props, coll)
    if groove.data.polygons:
        util.boolean(plate, groove, 'DIFFERENCE')
    util.remove_object(groove)

    # Matching ring tongue under the shell's rim (1.0 overlap up for the weld).
    ring = _rim_band(master, mid - rw * 0.5, mid + rw * 0.5,
                     rim_z - rh, rim_z + 1.0, props, coll)
    if ring.data.polygons:
        util.boolean(mold, ring, 'UNION')
    util.remove_object(ring)
    plate["mf_plate_top"] = cut_z
    return plate


# --- Screw the master to the key plate ---------------------------------------- #

SCREW_MIN_WALL = 2.0     # mm of master that must surround the heat-set insert


def screw_floor(props):
    """Plate material the screw needs under the master: the countersink cone plus
    1.5 mm of straight hole for the head to bear on, or 2.5 mm without one."""
    hole = max(getattr(props, "screw_diameter", 3.4), 0.5)
    if getattr(props, "screw_countersink", True):
        head = max(getattr(props, "screw_head_diameter", 6.5), hole)
        return (head - hole) * 0.5 + 1.5
    return 2.5


def _underside_z(obj, x, y):
    """Height of ``obj``'s lowest surface over the point (x, y), or None."""
    mn, _mx = util.world_bbox(obj)
    hit, loc, _n, _i = obj.ray_cast(Vector((x, y, mn.z - 5.0)), Vector((0.0, 0.0, 1.0)))
    return loc.z if hit else None


def plate_screw_spot(positive, props, top_z, centre=None):
    """Where the screw goes: the middle of the master's base outline when the
    insert hole has SCREW_MIN_WALL of master all round it there; otherwise the
    spot nearest that middle that has it. The master's underside must be low
    enough there for the plate to reach it (at most ``top_z``, the plate top).

    Returns ``(x, y, z_bottom, clearance)`` - the clearance from the hole's axis
    to the master's skin - or ``(None, reason)``."""
    ins_r = max(getattr(props, "insert_diameter", 4.0), 0.5) * 0.5
    depth = max(getattr(props, "insert_depth", 6.5), 0.5)
    need = ins_r + SCREW_MIN_WALL
    mn, mx = util.world_bbox(positive)
    cx, cy = centre if centre is not None else former_centre(positive, mn.z)

    def score(x, y):
        zb = _underside_z(positive, x, y)
        if zb is None or zb > top_z - 0.2:     # off the base, or over a recess
            return None
        c = plug_clearance(positive, x, y, zb + 0.3, zb + depth, samples=5, rays=16)
        return None if c is None else (x, y, zb, c)

    best = score(cx, cy)
    if best is not None and best[3] >= need:
        return best
    # Scan the base outline for the nearest spot with enough wall.
    loop = _cross_section_loop(positive, mn.z + 1.0)
    if loop:
        x0, x1 = min(q[0] for q in loop), max(q[0] for q in loop)
        y0, y1 = min(q[1] for q in loop), max(q[1] for q in loop)
    else:
        x0, x1, y0, y1 = mn.x, mx.x, mn.y, mx.y
    n = 14
    good, top = None, best
    for i in range(n + 1):
        for j in range(n + 1):
            x = x0 + (x1 - x0) * i / n
            y = y0 + (y1 - y0) * j / n
            s = score(x, y)
            if s is None:
                continue
            if top is None or s[3] > top[3]:
                top = s
            if s[3] >= need:
                dist = math.hypot(x - cx, y - cy)
                if good is None or dist < good[0]:
                    good = (dist, s)
    if good is not None:
        return good[1]
    have = 0.0 if top is None else max(top[3] - ins_r, 0.0)
    return None, (f"no spot on the base has {SCREW_MIN_WALL:.0f} mm of master around "
                  f"a {2 * ins_r:.1f} mm insert hole {depth:.1f} mm deep (best has "
                  f"{have:.1f} mm) - glue the master to the plate instead")


def add_plate_screw(plate, positive, props, coll):
    """Screw the printed master to the key plate instead of gluing it: a hole for
    the screw through the plate (countersunk from below, so the plate still sits
    flat on the table) and a blind hole up into the master's underside for a
    heat-set threaded insert, on one axis at ``plate_screw_spot``.

    The plate must already be thick enough (``add_base_plate`` with
    ``min_floor=screw_floor(props)``). Each cut is checked by a ray up the axis
    and retried with the exact solver; a master the insert hole will not cut
    into cleanly is left untouched. Returns a dict: ``ok``, ``note``, and on
    success the spot ``x``, ``y`` and the ``wall`` around the insert."""
    top_z = plate.get("mf_plate_top", util.world_bbox(plate)[1].z)
    cx, cy = former_centre(positive, util.world_bbox(positive)[0].z)
    spot = plate_screw_spot(positive, props, top_z, centre=(cx, cy))
    if spot[0] is None:
        return {"ok": False, "note": "screw skipped - " + spot[1]}
    x, y, zb, clearance = spot
    hole_r = max(getattr(props, "screw_diameter", 3.4), 0.5) * 0.5
    ins_r = max(getattr(props, "insert_diameter", 4.0), 0.5) * 0.5
    depth = max(getattr(props, "insert_depth", 6.5), 0.5)
    z_bot = util.world_bbox(plate)[0].z

    # The plate: screw hole from below the plate up past the master's underside,
    # and a 90 degree countersink cone opening at the plate's bottom face.
    hole = util.add_cone("MF_screwhole",
                         Vector((x, y, (z_bot - 1.0 + zb + 1.0) * 0.5)),
                         hole_r, hole_r, (zb + 1.0) - (z_bot - 1.0), 'Z', coll,
                         segments=48)
    head_d = 0.0
    if getattr(props, "screw_countersink", True):
        head_r = max(getattr(props, "screw_head_diameter", 6.5) * 0.5, hole_r + 0.1)
        head_d = 2.0 * head_r
        cs = head_r - hole_r
        sink = util.add_cone("MF_screwsink",
                             Vector((x, y, z_bot - 0.5 + (cs + 0.5) * 0.5)),
                             head_r + 0.5, hole_r, cs + 0.5, 'Z', coll, segments=48)
        util.boolean(hole, sink, 'UNION')
        util.remove_object(sink)
    util.boolean(plate, hole, 'DIFFERENCE')
    through = _underside_z(plate, x, y)
    if through is not None and through < zb - 0.5:
        util.boolean(plate, hole, 'DIFFERENCE', solver='EXACT')
    util.remove_object(hole)

    # The master: a blind hole for the insert, from its underside up.
    ins = util.add_cone("MF_inserthole",
                        Vector((x, y, zb - 1.0 + (depth + 1.0) * 0.5)),
                        ins_r, ins_r, depth + 1.0, 'Z', coll, segments=48)
    bak = positive.data.copy()

    def drilled():
        z_in = _underside_z(positive, x, y)
        return z_in is not None and abs(z_in - (zb + depth)) < 0.5

    util.boolean(positive, ins, 'DIFFERENCE')
    ok = drilled()
    if not ok:
        cur = positive.data
        positive.data = bak.copy()
        if cur.users == 0:
            bpy.data.meshes.remove(cur)
        util.boolean(positive, ins, 'DIFFERENCE', solver='EXACT')
        ok = drilled()
    util.remove_object(ins)
    if not ok:
        cur = positive.data
        positive.data = bak
        if cur.users == 0:
            bpy.data.meshes.remove(cur)
        return {"ok": False, "note": "the plate has its screw hole but the insert "
                                     "hole would not cut cleanly into this mesh - "
                                     "drill it by hand at the plate's hole"}
    if bak.users == 0:
        bpy.data.meshes.remove(bak)

    off = math.hypot(x - cx, y - cy)
    sink_txt = f", countersunk {head_d:.1f}" if head_d else ""
    where = ("at the middle of the base" if off < 1.0
             else f"{off:.0f} mm off the base centre, where the master is thick enough")
    note = (f"screw {where}: {2 * hole_r:.1f} mm hole through the plate{sink_txt}, "
            f"{2 * ins_r:.1f} x {depth:.1f} mm insert hole in the positive "
            f"({clearance - ins_r:.1f} mm of wall around it)")
    return {"ok": True, "note": note, "x": x, "y": y, "wall": clearance - ins_r}


def add_flange(mold, coll, width, thickness, bolt_radius):
    """Add a flat mounting flange (skirt) around the mold's base with bolt holes
    — the part that clamps the two halves to a board, MoldBoxer-style. Must be
    called after the base is flattened; the skirt is flush with the flat bottom."""
    mn, mx = util.world_bbox(mold)
    cx, cy = (mn.x + mx.x) * 0.5, (mn.y + mx.y) * 0.5
    sx = (mx.x - mn.x) + 2.0 * width
    sy = (mx.y - mn.y) + 2.0 * width
    z0 = mn.z

    skirt = util.add_box("MF_flange", Vector((cx, cy, z0 + thickness * 0.5)),
                         Vector((sx, sy, thickness)), coll)
    util.boolean(mold, skirt, 'UNION')
    util.remove_object(skirt)

    # Bolt holes in the skirt corners (outside the mold body, split evenly).
    inset = width * 0.5
    hx = sx * 0.5 - inset
    hy = sy * 0.5 - inset
    for sgx in (1.0, -1.0):
        for sgy in (1.0, -1.0):
            c = Vector((cx + sgx * hx, cy + sgy * hy, z0 + thickness * 0.5))
            hole = util.add_cone("MF_bolt", c, bolt_radius, bolt_radius,
                                 thickness * 4.0, 'Z', coll)
            util.boolean(mold, hole, 'DIFFERENCE')
            util.remove_object(hole)


def wing_note(props, text):
    """Record something the user should know about the clamp wings; the
    operator lists these after Generate."""
    notes = getattr(props, "wing_report", None)
    if notes is None:
        try:
            props.wing_report = notes = []
        except AttributeError:
            return
    if text not in notes:
        notes.append(text)


def add_wings(mold, axis, coll, master, outer_offset, width, thickness,
              bolt_radius, props, block=False, funnels=None, cavity=None, under=None,
              parting=None):
    """Full-height clamp flange along the parting seam, with bolt holes.

    For a profile-hugging mold we build a continuous lip that follows the body's
    silhouette at the parting plane — a thin slice of the shell just *outside* the
    mold wall ("rind"), grown out by ``width`` — so it stays welded to the body
    from top to bottom whatever the model's shape. For a block mold (flat sides) a
    plain flat ear on each side is enough.

    Wings are best-effort: the mold mesh is snapshotted first, and if anything
    would leave a floating piece (or errors out) the snapshot is restored, so
    clamp wings can never break an otherwise-good mold. Added before the split, so
    each half keeps its share of the flange and holes.

    Returns the bolt heights actually drilled (possibly empty) so the split can
    place wing alignment keys between them — or ``None`` when the wings rolled
    back, so no keys are attempted on wings that don't exist."""
    backup = mold.data.copy()
    try:
        mn, mx = util.world_bbox(mold)
        ai = {'X': 0, 'Y': 1}[axis]
        h = next(i for i in range(3) if i != ai and i != 2)
        center = (mn + mx) * 0.5
        cap = (mx[ai] - mn[ai]) * 0.4
        off = max(min(getattr(props, "split_offset", 0.0), cap), -cap)

        if parting is not None:
            off = parting.off
        if block:
            _flat_wings(mold, ai, h, center, mn, mx, width, thickness, coll)
        else:
            _profile_flange(mold, master, ai, h, center, mn, mx,
                            outer_offset, width, thickness, props, coll, funnels, off,
                            cavity, under, parting)
        return _drill_wing_bolts(mold, ai, h, center, mn, mx, width, thickness,
                                 bolt_radius, axis, coll, _effective_bolts(props), off,
                                 parting)
    except Exception:
        cur = mold.data
        mold.data = backup
        backup = None
        if cur.users == 0:
            bpy.data.meshes.remove(cur)
        for o in list(coll.objects):
            if o.name.startswith(("MF_wo", "MF_ww", "MF_wslab", "MF_wbolt",
                                  "MF_wing", "MF_wtest", "MF_wf", "MF_wbase",
                                  "MF_wband")):
                util.remove_object(o)
        wing_note(props, "the clamp wings could not be built on this model and were "
                         "left off")
        return None
    finally:
        if backup is not None:
            bpy.data.meshes.remove(backup)


def _wing_rind(master, outer_offset, width, props, coll, funnels=None, under=None):
    """Solid stock the clamp flanges are cut from: a shell just OUTSIDE the mold
    wall (dilate(master, offset+width) minus dilate(master, offset-eps)) with the
    funnel rinds added.

    Built from a COARSE copy of the model when the mesh is heavy: the flange is a
    bolt tab, not the cast surface, so it doesn't need fine detail — and dilating a
    heavy mesh twice here OOM-killed the build. The dilation repair is also clamped
    to the flange resolution so it can't re-inflate the coarse copy."""
    base = util.duplicate_object(master, "MF_wbase", coll)
    flange_voxel = max(outer_offset * 0.6, width * 0.25, 1.2)   # finer -> cleaner edges
    if len(base.data.polygons) > C.FLANGE_COARSEN_FACES:
        meshprep.voxel_remesh(base, flange_voxel)
    cprops = types.SimpleNamespace(**vars(props))
    cprops.detail_voxel = max(getattr(props, "detail_voxel", 1.0), flange_voxel)
    # Reach the rind WELL inside the body, not just skim its surface: both styles
    # carve the cavity LAST, so a deep wing can't fill it, and a deep inner avoids the
    # near-coincident boolean faces (sliver/ragged-edge artefacts) you get when the
    # flange's inner surface lands right on the body's outer surface.
    inner_d = max(outer_offset * C.WING_INNER, 0.05)
    eps = max(outer_offset - inner_d, 0.5)
    inner = _dilate_solid(base, inner_d, "MF_wo", cprops, coll)
    rind = _dilate_solid(base, outer_offset + width, "MF_ww", cprops, coll)
    util.remove_object(base)
    util.boolean(rind, inner, 'DIFFERENCE')          # shell from deep inside out to the lip
    util.remove_object(inner)
    for funnel in (funnels or ()):
        _union_funnel_rind(rind, funnel, eps, width, coll)
    # A Locking Base's socket pocket (see socket_pocket_cutter): the rind reaches
    # down under a hollow underside and would bridge the socket mouth as a bar.
    if under is not None and rind.data.polygons:
        util.boolean(rind, under, 'DIFFERENCE')
    return rind


def _profile_flange(mold, master, ai, h, center, mn, mx, outer_offset, width,
                    thickness, props, coll, funnels=None, seam_off=0.0, cavity=None,
                    under=None, parting=None):
    """Side flanges along the parting seam that hug the CONTOUR and run up the funnel.

    Built as a thin shell just OUTSIDE the mold wall (a 'rind' = dilate(master,
    offset+width) minus dilate(master, offset-eps)), plus the funnel rinds, then
    clipped to a thin slab on the parting plane — leaving a solid, contoured lip
    across the seam. This is the same construction the radial wings use, and unlike
    the old 'smear the whole mold sideways' it works whether the mold is still solid
    (pour box) or already hollow (direct mold): smearing a hollow shell left only
    thin, ragged fins (broken wings)."""
    big = (mx - mn).length * 2.0 + 10.0
    mmn, mmx = util.world_bbox(mold)
    # The slab starts a hair ABOVE the mold's bottom, never below it: the rind is
    # dilated offset+width from the model, so under the base it reaches well past
    # the mold bottom, and a slab that overhung the bottom welded a fin under the
    # shell along the whole seam. The final bottom cut used to remove it - and on a
    # machine where that cut silently fails, the fin shipped as a bar across the
    # socket mouth. No overhang, nothing to remove.
    z_lo = mmn.z + 0.3
    z_hi = mmx.z + 2.0
    zc = (z_lo + z_hi) * 0.5
    zsz = z_hi - z_lo

    rind = _wing_rind(master, outer_offset, width, props, coll, funnels, under)

    # One thin slab across the parting plane, spanning the body + flange on both
    # h sides, so a contoured lip is left on each half of the seam. With a
    # contoured parting the slab follows that SAME surface (``parting``), so
    # the split halves every wing at every height.
    if parting is not None:
        from . import split as _split
        clip = _split.seam_band(parting, thickness, z_lo, z_hi, coll)
    else:
        c = center.copy(); c[ai] = center[ai] + seam_off; c[h] = center[h]; c[2] = zc
        size = Vector((0.0, 0.0, 0.0))
        size[ai] = thickness                              # thin across the parting plane
        size[h] = big                                     # full width on both sides
        size[2] = zsz
        clip = util.add_box("MF_wslab", c, size, coll)
    util.boolean(rind, clip, 'INTERSECT')
    util.remove_object(clip)
    # The rind is dilated from the MODEL, so under a flat base it also reaches
    # down by offset+width - across a Locking Base's socket band, where the
    # clipped slab becomes a bar bridging the socket mouth along the seam. The
    # cavity cutter (gap + socket) is carved later and normally eats it, but a
    # heavy or awkward mesh can leave it standing: trim the rind by that cutter
    # NOW, so wing material can never sit inside the cavity or the socket.
    if cavity is not None and rind.data.polygons:
        util.boolean(rind, cavity, 'DIFFERENCE')
    util.remove_small_islands(rind)
    if rind.data.polygons and _overlaps(rind, mold, coll):
        util.boolean(mold, rind, 'UNION')
        util.remove_small_islands(mold)               # drop any boolean sliver fragments
    util.remove_object(rind)


def add_radial_wings(mold, coll, master, outer_offset, width, thickness,
                     bolt_radius, props, n, funnels=None, under=None):
    """Clamp flanges for a radial multi-part mold: one profile-hugging flange along
    each of the ``n`` radial seams, with bolt holes running tangentially
    (perpendicular to the seam plane) so neighbouring wedges bolt together.

    Same rind construction as the two-part wings — a thin shell just outside the
    mold wall, grown out by ``width``, including the funnel rinds — but clipped to a
    rotated slab along each seam direction instead of two slabs across one seam.
    Added before the radial split, which cuts every flange in half lengthwise,
    leaving matching bolted lips on both sides of each seam. Best-effort: snapshot
    + restore, so wings can never break an otherwise-good mold.

    Returns the centre the wings were built around so the radial split can cut with
    the *same* centre: the wings shift the mold's bounding box, so re-deriving the
    centre after they are added would slide the off-axis seams off their wings."""
    mn, mx = util.world_bbox(mold)
    center = (mn + mx) * 0.5
    backup = mold.data.copy()
    try:
        big = (mx - mn).length * 2.0 + 10.0
        zc = (mn.z + 0.3 + mx.z + 2.0) * 0.5      # a hair above the bottom, never below
        zsz = (mx.z + 2.0) - (mn.z + 0.3)

        rind = _wing_rind(master, outer_offset, width, props, coll, funnels, under)

        for k in range(n):
            theta = 2.0 * math.pi * k / n
            d = Vector((math.cos(theta), math.sin(theta), 0.0))
            wing = util.duplicate_object(rind, "MF_wing", coll)
            # One-sided slab from the centre outward along the seam direction.
            c = Vector((center.x, center.y, zc)) + d * (big * 0.5)
            clip = util.add_box("MF_wslab", c, Vector((big, thickness, zsz)), coll,
                                rot_z=theta)
            util.boolean(wing, clip, 'INTERSECT')
            util.remove_object(clip)
            if wing.data.polygons and _overlaps(wing, mold, coll):
                util.boolean(mold, wing, 'UNION')
            util.remove_object(wing)
        util.remove_object(rind)
        util.remove_small_islands(mold)               # drop any boolean sliver fragments

        props.radial_bolt_zs = _drill_radial_bolts(
            mold, center, mn, mx, width, thickness, bolt_radius, coll, n,
            _effective_bolts(props))
    except Exception:
        cur = mold.data
        mold.data = backup
        backup = None
        if cur.users == 0:
            bpy.data.meshes.remove(cur)
        for o in list(coll.objects):
            if o.name.startswith(("MF_wo", "MF_ww", "MF_wslab", "MF_wbolt",
                                  "MF_wing", "MF_wtest", "MF_wf")):
                util.remove_object(o)
        wing_note(props, "the clamp wings could not be built on this model and were "
                         "left off")
    finally:
        if backup is not None:
            bpy.data.meshes.remove(backup)
    return center


def cut_band_notches(mold, model, axis, props, coll, parting=None, offset=0.0,
                     bolt_zs=None, spacing=30.0, skip_side=None):
    """Notches across the clamp wings' outer edges, every ``spacing`` mm up the
    seam on both sides, for rubber bands: a band stretched round the mold sits
    in a notch on each wing and cannot slide off. Only where a wing really
    stands out, never on a bolt hole. Returns the number cut."""
    from . import split as _split
    width = getattr(props, "wing_width", 8.0)
    depth = min(2.5, width - 2.5)
    if depth < 1.0:
        return 0
    thick = getattr(props, "wing_thickness", 4.0) + 2.0
    notch_h = 4.0
    ai = {'X': 0, 'Y': 1}[axis]
    h = next(i for i in range(3) if i != ai and i != 2)
    mn, mx = util.world_bbox(mold)
    centre = (mn + mx) * 0.5
    cap = (mx[ai] - mn[ai]) * 0.4
    seam_flat = centre[ai] + (parting.off if parting is not None else max(
        min(getattr(props, "split_offset", 0.0), cap), -cap))
    reach = (mx - mn).length + 10.0
    zs = _notch_heights(model, spacing, notch_h, getattr(props, "bolt_radius", 1.5),
                        bolt_zs)
    cut = 0
    for sgn in (1.0, -1.0):
        if skip_side is not None and sgn * skip_side > 0:
            continue                      # the bottom-fill channel's side: the band
                                          # rests on the pipe there
        for z in zs:
            tipw = _split.wing_tip(mold, parting, centre, z, sgn, reach, seam_flat, ai, h)
            tipm = _split.wing_tip(model, parting, centre, z, sgn, reach, seam_flat, ai, h)
            if tipw is None or tipm is None:
                continue
            tip, seam = tipw
            if (tip - tipm[0]) * sgn < offset + 0.6 * width:
                continue                              # no wing standing out here
            c = centre.copy()
            c[h] = tip - sgn * (depth - 1.0) * 0.5
            c[ai] = seam
            c[2] = z
            size = Vector((0.0, 0.0, 0.0))
            size[h] = depth + 1.0
            size[ai] = thick
            size[2] = notch_h
            box = util.add_box("MF_wnotch", c, size, coll)
            util.boolean(mold, box, 'DIFFERENCE')
            util.remove_object(box)
            cut += 1
    return cut


def _notch_heights(model, spacing, notch_h, bolt_r, bolt_zs):
    """Heights for band notches: every ``spacing`` mm up the model, each moved
    up past a bolt hole it would land on."""
    mmn, mmx = util.world_bbox(model)
    zs = []
    z = mmn.z + 8.0
    while z < mmx.z - 6.0:
        if bolt_zs:
            zb = min(bolt_zs, key=lambda b: abs(b - z))
            if abs(zb - z) < notch_h * 0.5 + bolt_r + 1.0:
                z = zb + notch_h * 0.5 + bolt_r + 1.0
        zs.append(z)
        z += spacing
    return zs


def cut_radial_band_notches(mold, model, props, coll, center, n, offset=0.0,
                            bolt_zs=None, spacing=30.0, skip=()):
    """Band notches for a radial split: across the outer edge of each seam's
    flange (seam ``k`` runs out along angle 2 pi k / n from ``center``), every
    ``spacing`` mm, wherever the flange really stands out, never on a bolt
    hole. ``skip`` lists seams to leave alone (the bottom-fill channel's).
    Returns the number cut."""
    width = getattr(props, "wing_width", 8.0)
    depth = min(2.5, width - 2.5)
    if depth < 1.0:
        return 0
    thick = getattr(props, "wing_thickness", 4.0) + 2.0
    notch_h = 4.0
    mn, mx = util.world_bbox(mold)
    far = (mx - mn).length + 10.0
    zs = _notch_heights(model, spacing, notch_h, getattr(props, "bolt_radius", 1.5),
                        bolt_zs)
    cut = 0
    for k in range(n):
        if k in skip:
            continue
        theta = 2.0 * math.pi * k / n
        d = Vector((math.cos(theta), math.sin(theta), 0.0))
        for z in zs:
            o = Vector((center.x, center.y, z)) + d * far
            hw, lw, _n1, _i1 = mold.ray_cast(o, -d)
            hm, lm, _n2, _i2 = model.ray_cast(o, -d)
            if not (hw and hm):
                continue
            tip = (lw - Vector((center.x, center.y, lw.z))).dot(d)
            tipm = (lm - Vector((center.x, center.y, lm.z))).dot(d)
            if tip - tipm < offset + 0.6 * width:
                continue                              # no flange standing out here
            c = Vector((center.x, center.y, z)) + d * (tip - (depth - 1.0) * 0.5)
            box = util.add_box("MF_wnotch", c, Vector((depth + 1.0, thick, notch_h)),
                               coll, rot_z=theta)
            util.boolean(mold, box, 'DIFFERENCE')
            util.remove_object(box)
            cut += 1
    return cut


def wing_coverage(halves, model, axis, offset, width, thickness, parting=None,
                  seam_flat=None, samples=14):
    """How much of the seam carries a clamp wing on BOTH halves. At heights over
    the model, on each side of the seam, each half's reach along the seam - a
    ray a quarter of the wing thickness into that half, on the parting surface -
    is compared with the model's: a wing reaches about offset + width past it, a
    bare shell only offset. ``halves`` = (the +axis half, the -axis half).
    Returns ``(fraction_ok, n_checked)``."""
    from . import split as _split
    ai = {'X': 0, 'Y': 1}[axis]
    h = next(i for i in range(3) if i != ai and i != 2)
    mn, mx = util.world_bbox(model)
    centre = (mn + mx) * 0.5
    if seam_flat is None:
        seam_flat = centre[ai]
    allmn = Vector(tuple(min(util.world_bbox(q)[0][i] for q in halves) for i in range(3)))
    allmx = Vector(tuple(max(util.world_bbox(q)[1][i] for q in halves) for i in range(3)))
    reach = (allmx - allmn).length + 10.0
    q4 = max(thickness * 0.25, 0.3)
    need = offset + 0.5 * width
    lo = mn.z + offset + 3.0
    hi = mx.z - 3.0
    if hi <= lo:
        return 1.0, 0
    good = total = 0
    for k in range(samples):
        z = lo + (hi - lo) * k / max(samples - 1, 1)
        for sgn in (1.0, -1.0):
            m = _split.wing_tip(model, parting, centre, z, sgn, reach, seam_flat, ai, h)
            if m is None:
                continue
            total += 1
            ok = True
            for half, bias in ((halves[0], q4), (halves[1], -q4)):
                # read the surface where the wing should be, just past the model
                r = _split.wing_tip(half, parting, centre, z, sgn, reach, seam_flat,
                                    ai, h, bias=bias, h0=m[0] + sgn * need)
                if r is None or (r[0] - m[0]) * sgn < need:
                    ok = False
            good += ok
    return (good / total if total else 1.0), total


def _drill_radial_bolts(mold, center, mn, mx, width, thickness, bolt_radius,
                        coll, n, bolt_count=None):
    """Bolt holes down each seam flange. Each hole runs tangentially — perpendicular
    to the seam plane — centred on the seam, so after the radial split the two
    neighbouring wedges carry matching half-channels and a bolt pulls them together.
    ``bolt_count`` is per seam: ``None`` = automatic by height, 0 = no holes.
    Returns the bolt heights."""
    if bolt_count == 0:
        return []                                  # bolts explicitly disabled
    if width < 2.2 * bolt_radius:
        return []                                  # too narrow to fit a hole safely
    height = mx.z - mn.z
    nb = bolt_count or max(1, int(round(height / (max(width, bolt_radius) * 5.0))))
    big = (mx - mn).length * 2.0 + 10.0
    zs = [mn.z + height * (j + 1) / (nb + 1) for j in range(nb)]
    for k in range(n):
        theta = 2.0 * math.pi * k / n
        d = Vector((math.cos(theta), math.sin(theta), 0.0))
        for j in range(nb):
            z = mn.z + height * (j + 1) / (nb + 1)
            o = Vector((center.x, center.y, z)) + d * big
            hit, loc, _nrm, _idx = mold.ray_cast(o, -d)
            if not hit:
                continue
            c = Vector(loc) - d * (bolt_radius + width * 0.3)
            c.z = z
            hole = util.add_cone("MF_wbolt", c, bolt_radius, bolt_radius,
                                 thickness + 2.4, 'X', coll,   # just pierce the flange
                                 rot_z=theta + math.pi * 0.5)
            util.boolean(mold, hole, 'DIFFERENCE')
            util.remove_object(hole)
    return zs


def add_horizontal_flange(mold, coll, master, outer_offset, width, thickness,
                          bolt_radius, props, hz, hole_angles):
    """Bolted flange ring around the body at the horizontal seam height ``hz`` —
    the mating lip for a Horizontal Split. Built like the clamp wings (a thin
    profile-hugging rind just outside the wall, grown out by ``width``) but
    clipped to a horizontal band, so the lip follows the body all the way round.

    Vertical holes (sized by Bolt Diameter — fit threaded inserts in the lower
    lip and screw down through the upper) are drilled through the ring at
    ``hole_angles``, which the caller picks BETWEEN the vertical seams so a hole
    is never split in half. Best-effort: snapshot + restore, so the ring can
    never break an otherwise-good mold."""
    backup = mold.data.copy()
    try:
        mn, mx = util.world_bbox(mold)
        center = (mn + mx) * 0.5
        big = (mx - mn).length * 2.0 + 10.0
        eps = min(0.5, 0.5 * outer_offset)
        # Slightly different radii than the vertical wings' rind: where the ring
        # crosses a wing the two would otherwise have perfectly coincident
        # surfaces — degenerate input that makes the union shed garbage slivers.
        inner = _dilate_solid(master, max(outer_offset - eps * 0.85, 0.05),
                              "MF_wo", props, coll)
        rind = _dilate_solid(master, outer_offset + width * 0.97, "MF_ww", props, coll)
        util.boolean(rind, inner, 'DIFFERENCE')   # shell hugging just outside the wall
        util.remove_object(inner)
        clip = util.add_box("MF_wslab", Vector((center.x, center.y, hz)),
                            Vector((big, big, thickness)), coll)
        util.boolean(rind, clip, 'INTERSECT')
        util.remove_object(clip)
        if rind.data.polygons and _overlaps(rind, mold, coll):
            util.boolean(mold, rind, 'UNION')
        util.remove_object(rind)

        reach = (mx - mn).length + 10.0
        for ang in hole_angles:
            d = Vector((math.cos(ang), math.sin(ang), 0.0))
            origin = Vector((center.x, center.y, hz)) + d * reach
            hit, loc, _n, _i = mold.ray_cast(origin, -d)
            if not hit:
                continue
            c = Vector(loc) - d * (bolt_radius + width * 0.3)
            hole = util.add_cone("MF_wbolt", Vector((c.x, c.y, hz)),
                                 bolt_radius, bolt_radius, thickness + 2.4, 'Z', coll)   # just pierce the ring
            util.boolean(mold, hole, 'DIFFERENCE')
            util.remove_object(hole)
    except Exception:
        cur = mold.data
        mold.data = backup
        backup = None
        if cur.users == 0:
            bpy.data.meshes.remove(cur)
        for o in list(coll.objects):
            if o.name.startswith(("MF_wo", "MF_ww", "MF_wslab", "MF_wbolt")):
                util.remove_object(o)
    finally:
        if backup is not None:
            bpy.data.meshes.remove(backup)


def _union_funnel_rind(rind, f, eps, width, coll):
    """Add a shell hugging the funnel spout's exterior to the wing rind, so the side
    flanges continue all the way up the funnel with no gap. It matches the spout's
    two-piece shape — a STRAIGHT neck then a flared cup — because a single straight
    cone (neck -> mouth) diverges from the real surface over the neck and leaves an
    empty wedge between the wing and the funnel. Sits just outside the spout (eps
    overlap for a clean weld) and out to ``width``."""
    cx, cy = f["x"], f["y"]
    base_z, apex_z = f["base_z"], f["apex_z"]
    t_z = min(max(f.get("throat_top", base_z), base_z + 0.5), apex_z - 0.5)
    n_out, m_out = f["neck_out"], f["mouth_out"]

    def shell(z0, z1, r0_in, r1_in, r0_out, r1_out):
        if z1 - z0 <= 0.1:
            return
        c = Vector((cx, cy, (z0 + z1) * 0.5))
        s_in = util.add_cone("MF_wfi", c, r0_in, r1_in, z1 - z0, 'Z', coll)
        s_out = util.add_cone("MF_wfo", c, r0_out, r1_out, z1 - z0, 'Z', coll)
        util.boolean(s_out, s_in, 'DIFFERENCE')
        util.remove_object(s_in)
        if s_out.data.polygons:
            util.boolean(rind, s_out, 'UNION')
        util.remove_object(s_out)

    shell(base_z, t_z, n_out - eps, n_out - eps, n_out + width, n_out + width)  # neck
    shell(t_z, apex_z, n_out - eps, m_out - eps, n_out + width, m_out + width)  # cup



def _overlaps(wing, mold, coll):
    """True if ``wing`` intersects ``mold`` — so a union welds them rather than
    leaving the wing as a disconnected floating piece."""
    probe = util.duplicate_object(wing, "MF_wtest", coll)
    util.boolean(probe, mold, 'INTERSECT')
    hit = bool(probe.data.polygons)
    util.remove_object(probe)
    return hit


def _flat_wings(mold, ai, h, center, mn, mx, width, thickness, coll):
    """Flat full-height ears for a flat-sided block mold."""
    for sgn in (1.0, -1.0):
        edge = mx[h] if sgn > 0 else mn[h]
        inner = edge - sgn * max(thickness, width * 0.5)
        outer = edge + sgn * width
        c = center.copy(); c[h] = (inner + outer) * 0.5; c[ai] = center[ai]
        size = Vector((0.0, 0.0, 0.0))
        size[h] = abs(outer - inner); size[ai] = thickness; size[2] = mx.z - mn.z
        wing = util.add_box("MF_wing", c, size, coll)
        util.boolean(mold, wing, 'UNION')
        util.remove_object(wing)


def _drill_wing_bolts(mold, ai, h, center, mn, mx, width, thickness,
                      bolt_radius, axis, coll, bolt_count=None, seam_off=0.0,
                      parting=None):
    """Bolt holes down each side, seated just inside the flange's outer edge and
    running along the split axis so a bolt pulls the two halves together.
    ``bolt_count`` per side: ``None`` = automatic by height, 0 = no holes.

    Returns the list of heights actually drilled — the wing alignment keys are
    placed midway BETWEEN these, so a key can never land on a bolt hole."""
    if bolt_count == 0:
        return []                                  # bolts explicitly disabled
    if width < 2.2 * bolt_radius:
        return []                                  # too narrow to fit a hole safely
    height = mx.z - mn.z
    n = bolt_count or max(1, int(round(height / (max(width, bolt_radius) * 5.0))))
    big = (mx - mn).length * 2.0 + 10.0
    drilled = []
    from . import split as _split
    for sgn in (1.0, -1.0):
        for k in range(n):
            z = mn.z + height * (k + 1) / (n + 1)
            # the wing's edge, on the parting surface (contoured or flat)
            found = _split.wing_tip(mold, parting, center, z, sgn, big,
                                    center[ai] + seam_off, ai, h)
            if found is None:
                continue
            tip, seam = found
            c = center.copy(); c[ai] = seam; c[2] = z
            c[h] = tip - sgn * (bolt_radius + width * 0.3)
            if parting is not None:
                s_here = _split.parting_at(parting, c[h], z)
                if s_here is not None:
                    c[ai] = s_here
            hole = util.add_cone("MF_wbolt", c, bolt_radius, bolt_radius,
                                 thickness + 2.4, axis, coll)   # just pierce the flange
            util.boolean(mold, hole, 'DIFFERENCE')
            util.remove_object(hole)
            drilled.append(z)
    return sorted(set(drilled))
