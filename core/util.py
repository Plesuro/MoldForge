"""Low-level Blender helpers: object/mesh creation, modifier baking, booleans.

Everything here avoids ``bpy.ops`` so it runs reliably in background mode.
"""

import math

import bpy
import bmesh
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

COLLECTION_NAME = "MoldForge"
MARKER_COLLECTION = "MoldForge Markers"


def marker_points(prefix):
    """World positions of the user's marker empties (``MF_VentMark`` /
    ``MF_PourMark``) in the markers collection. Markers are ordinary objects the
    user moves/duplicates/deletes; they live OUTSIDE the output collection so
    regeneration never wipes them."""
    coll = bpy.data.collections.get(MARKER_COLLECTION)
    if coll is None:
        return []
    out = []
    for o in coll.objects:
        if not o.name.startswith(prefix):
            continue
        # matrix_basis, not matrix_world: the world matrix lags until a depsgraph
        # pass (and never updates if the collection isn't in the view layer) —
        # the basis always reflects the location the user set.
        if o.parent is None:
            out.append(o.matrix_basis.translation.copy())
        else:
            out.append((o.parent.matrix_world @ o.matrix_parent_inverse
                        @ o.matrix_basis).translation.copy())
    return out


def ensure_collection(name=COLLECTION_NAME):
    """Return the MoldForge collection, guaranteed linked to the current scene
    (a collection by that name may already exist orphaned or in another scene)."""
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
    scene_root = bpy.context.scene.collection
    if coll is not scene_root and coll not in scene_root.children_recursive:
        try:
            scene_root.children.link(coll)
        except RuntimeError:
            pass
    return coll


def apply_viewport_color(obj, mat_name, rgba):
    """Give the object a recognizable solid-view colour (best-effort — display
    only, never allowed to fail a build)."""
    try:
        mat = bpy.data.materials.get(mat_name)
        if mat is None:
            mat = bpy.data.materials.new(mat_name)
            mat.diffuse_color = rgba
        obj.data.materials.clear()
        obj.data.materials.append(mat)
    except Exception:
        pass


def has_self_intersections(obj):
    """True if the mesh contains interpenetrating (non-adjacent) triangles — the
    fold-overs a Solidify offset leaves across deep creases. Such a mesh can be a
    single watertight manifold island, so the island/manifold checks never see it,
    but the flaps print as slits and serrated edges on the mold surface."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    try:
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
        bm.faces.ensure_lookup_table()
        tree = BVHTree.FromBMesh(bm, epsilon=0.0)
        for i, j in tree.overlap(tree):
            if i >= j:
                continue
            fi = {v.index for v in bm.faces[i].verts}
            fj = {v.index for v in bm.faces[j].verts}
            if not (fi & fj):          # neighbours always touch; skip them
                return True
        return False
    finally:
        bm.free()


def fill_enclosed_voids(obj):
    """Delete enclosed interior cavity shells — face islands whose signed volume
    is negative (normals facing the void). Turns a hollow Solidify result into
    the exact outer solid with full surface detail, where a voxel remesh would
    smooth it. Returns how many void shells were removed."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    try:
        comps = _face_components(bm)
        if len(comps) <= 1:
            return 0
        doomed = []
        removed = 0
        for comp in comps:
            vol = 0.0
            for f in comp:
                vs = f.verts
                for i in range(1, len(vs) - 1):   # fan; exact for closed islands
                    vol += vs[0].co.dot(vs[i].co.cross(vs[i + 1].co)) / 6.0
            if vol < 0.0:
                doomed.extend(comp)
                removed += 1
        if doomed:
            bmesh.ops.delete(bm, geom=doomed, context='FACES')
            bm.to_mesh(obj.data)
            obj.data.update()
        return removed
    finally:
        bm.free()


def island_count(obj):
    """Number of disconnected mesh pieces."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    try:
        seen = set()
        count = 0
        for f in bm.faces:
            if f in seen:
                continue
            count += 1
            stack = [f]
            while stack:
                cf = stack.pop()
                if cf in seen:
                    continue
                seen.add(cf)
                for e in cf.edges:
                    for lf in e.link_faces:
                        if lf not in seen:
                            stack.append(lf)
        return count
    finally:
        bm.free()


def _face_components(bm):
    """Group a bmesh's faces into edge-connected components (lists of faces)."""
    seen = set()
    comps = []
    for f in bm.faces:
        if f in seen:
            continue
        stack = [f]
        comp = []
        while stack:
            cf = stack.pop()
            if cf in seen:
                continue
            seen.add(cf)
            comp.append(cf)
            for e in cf.edges:
                for lf in e.link_faces:
                    if lf not in seen:
                        stack.append(lf)
        comps.append(comp)
    return comps


def fill_voids(obj):
    """Delete the inner walls of hollows sealed inside a solid - the face
    islands that enclose negative volume (their normals face into the hollow)
    - so the hollow is filled. A cavity cutter with such a hollow carves a
    mold around it and leaves the plastic inside floating in the silicone
    space (a model with a hollow underside on a Locking Base: the pocket
    between the plinth's top and the underside). Returns the number filled."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    try:
        comps = _face_components(bm)
        if len(comps) <= 1:
            return 0
        mw = obj.matrix_world
        voids = []
        for comp in comps:
            sv = 0.0
            for f in comp:
                co = [mw @ lp.vert.co for lp in f.loops]
                for i in range(1, len(co) - 1):
                    sv += co[0].dot(co[i].cross(co[i + 1])) / 6.0
            if sv < 0.0:
                voids.append(comp)
        if not voids or len(voids) == len(comps):
            return 0                        # nothing sealed in, or inside out
        faces = [f for comp in voids for f in comp]
        verts = {v for f in faces for v in f.verts}
        bmesh.ops.delete(bm, geom=faces, context='FACES_ONLY')
        loose_e = [e for e in bm.edges if not e.link_faces]
        if loose_e:
            bmesh.ops.delete(bm, geom=loose_e, context='EDGES')
        loose_v = [v for v in verts if v.is_valid and not v.link_edges]
        if loose_v:
            bmesh.ops.delete(bm, geom=loose_v, context='VERTS')
        bm.to_mesh(obj.data)
        obj.data.update()
        return len(voids)
    finally:
        bm.free()


def remove_small_islands(obj, face_frac=0.04, size_frac=0.15):
    """Delete tiny disconnected face-islands — the few-face slivers a Solidify or
    boolean throws off a concave surface — that are BOTH low-poly (< ``face_frac`` of
    the biggest island) AND spatially tiny (bbox diagonal < ``size_frac`` of the whole
    mesh). Requiring both means a legitimately low-poly but large piece (e.g. a block
    mold's outer box) is always kept, while real geometry — always one connected
    piece — is untouched. A watertight mesh stays watertight (each dropped island was
    its own closed shell). Returns the number of islands removed."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    try:
        comps = _face_components(bm)
        if len(comps) <= 1:
            return 0

        def diag(verts):
            xs = [v.co.x for v in verts]
            ys = [v.co.y for v in verts]
            zs = [v.co.z for v in verts]
            return math.sqrt((max(xs) - min(xs)) ** 2
                             + (max(ys) - min(ys)) ** 2
                             + (max(zs) - min(zs)) ** 2)

        overall = diag(bm.verts) or 1.0
        max_faces = max(len(c) for c in comps)
        face_thr = max(1, int(max_faces * face_frac))
        size_thr = overall * size_frac

        doomed = [c for c in comps
                  if len(c) < face_thr
                  and diag({v for f in c for v in f.verts}) < size_thr]
        if not doomed:
            return 0
        bmesh.ops.delete(bm, geom=[f for c in doomed for f in c], context='FACES')
        bm.to_mesh(obj.data)
        obj.data.update()
        return len(doomed)
    finally:
        bm.free()


def island_report(obj, top=4):
    """Short human summary of a mesh's disconnected face-islands by size, e.g.
    '3 pieces: 88%, 7%, 5%'. Used to diagnose a 'disconnected/floating pieces'
    failure — many tiny pieces means slivers, a few big ones means a real undercut."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    try:
        sizes = sorted((len(c) for c in _face_components(bm)), reverse=True)
        total = sum(sizes) or 1
        shown = ", ".join(f"{round(100 * s / total)}%" for s in sizes[:top])
        extra = "" if len(sizes) <= top else f", +{len(sizes) - top} more"
        return f"{len(sizes)} pieces: {shown}{extra}"
    finally:
        bm.free()


def nonmanifold_count(obj):
    """Number of non-manifold edges — even a few make the fast MANIFOLD boolean
    solver refuse to run ('Cannot execute, non-manifold inputs')."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    try:
        return sum(1 for e in bm.edges if not e.is_manifold)
    finally:
        bm.free()


def has_nonmanifold(obj):
    """True if the mesh has ANY non-manifold edge — early-exit, so it's cheaper than
    counting them. Used to route a boolean to the robust EXACT solver."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    try:
        return any(not e.is_manifold for e in bm.edges)
    finally:
        bm.free()


def largest_island_fraction(obj):
    """Fraction (by face count) belonging to the biggest face-island — ~1.0 means a
    single solid plus a minor fragment, ~0.5 means the half split roughly in two."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    try:
        comps = _face_components(bm)
        total = sum(len(c) for c in comps) or 1
        return (max((len(c) for c in comps), default=0)) / total
    finally:
        bm.free()


def flip_normals(obj):
    """Reverse every face so an inside-out solid reads as positive volume."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def split_islands(obj, coll, min_vol_frac=0.02, min_diag=8.0):
    """Separate a part that came out in several pieces. Pieces that are real
    (at least ``min_vol_frac`` of the largest piece's volume and ``min_diag``
    across) become parts of their own - the largest stays in ``obj``, the others
    are new objects named ``obj.name + "2"``, ``"3"``... - and slivers are
    dropped. Returns ``(pieces, dropped)``: the list of part objects (``obj``
    first) and how many slivers were removed."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    comps = _face_components(bm)
    if len(comps) <= 1:
        bm.free()
        return [obj], 0

    def stats(c):
        vs = {v for f in c for v in f.verts}
        xs = [v.co.x for v in vs]; ys = [v.co.y for v in vs]; zs = [v.co.z for v in vs]
        diag = math.sqrt((max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2
                         + (max(zs) - min(zs)) ** 2)
        vol = 0.0
        for f in c:
            vv = [lp.vert.co for lp in f.loops]
            for k in range(1, len(vv) - 1):
                vol += vv[0].dot(vv[k].cross(vv[k + 1])) / 6.0
        return abs(vol), diag

    info = sorted(((stats(c), i) for i, c in enumerate(comps)),
                  key=lambda t: -t[0][0])
    big_vol = info[0][0][0] or 1e-9
    keep, drop = [], []
    for (vol, diag), i in info:
        if i == info[0][1] or (vol >= min_vol_frac * big_vol and diag >= min_diag):
            keep.append(i)
        else:
            drop.append(i)
    bm.free()
    pieces = [obj]
    src = obj.data.copy()
    for rank, i in enumerate(keep):
        bmk = bmesh.new()
        bmk.from_mesh(src)
        comps_k = _face_components(bmk)
        # the component order is deterministic for the same mesh
        doomed = [f for j, c in enumerate(comps_k) if j != i for f in c]
        bmesh.ops.delete(bmk, geom=doomed, context='FACES')
        if rank == 0:
            bmk.to_mesh(obj.data)
            obj.data.update()
        else:
            piece = new_mesh_object(f"{obj.name}{rank + 1}", bmk, coll)
            piece.matrix_world = obj.matrix_world.copy()
            pieces.append(piece)
            continue
        bmk.free()
    bpy.data.meshes.remove(src)
    return pieces, len(drop)


def keep_largest_island(obj):
    """Delete every face-island except the biggest, and return the fraction (by
    face count) that was discarded. A last-resort cleanup for a half left with a
    minor severed fragment (e.g. an open-bottom rim) — only call it when the
    discarded fraction will be small."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    try:
        comps = _face_components(bm)
        if len(comps) <= 1:
            return 0.0
        comps.sort(key=len, reverse=True)
        total = sum(len(c) for c in comps) or 1
        discarded = sum(len(c) for c in comps[1:])
        bmesh.ops.delete(bm, geom=[f for c in comps[1:] for f in c], context='FACES')
        bm.to_mesh(obj.data)
        obj.data.update()
        return discarded / total
    finally:
        bm.free()


def folded_faces(obj):
    """Indices of polygons whose own triangulation folds back on itself: at least
    one of Blender's triangles for the face points against the face's normal.
    The fast boolean solver leaves such faces where it merges coplanar pieces
    into one polygon with a zero-width slit (a leg top split in two by a wall
    standing on it, a hook face split by a pin channel). The mesh is still
    closed, but those triangles render black and export as inverted facets."""
    me = obj.data
    me.calc_loop_triangles()
    bad = set()
    for lt in me.loop_triangles:
        if lt.area > 1e-5 and lt.normal.dot(me.polygons[lt.polygon_index].normal) < 0.0:
            bad.add(lt.polygon_index)
    return sorted(bad)


def _zigzag_edges(bm, face):
    """Edges whose collapse untangles ``face``: where the outline's edges i and
    i+2 cross, the middle edge i+1 runs backwards (a zig-zag knot a fraction of
    a millimetre across, left by a boolean). Planar faces only."""
    import mathutils.geometry as mg
    loops = list(face.loops)
    m = len(loops)
    if m < 5:
        return []
    n = face.normal
    u = n.orthogonal().normalized()
    v = n.cross(u)
    p2 = [Vector((lp.vert.co.dot(u), lp.vert.co.dot(v))) for lp in loops]
    out = []
    for i in range(m):
        a1, a2 = p2[i], p2[(i + 1) % m]
        b1, b2 = p2[(i + 2) % m], p2[(i + 3) % m]
        if mg.intersect_line_line_2d(a1, a2, b1, b2) is not None:
            out.append(loops[(i + 1) % m].edge)
    return out


def fix_folded_faces(obj, dist=1e-4):
    """Repair ``folded_faces``, in two stages, each kept only if it leaves the
    mesh a valid solid with fewer folds (else the mesh is restored):

    1. Zero-area polygons that run out and back along the same line (a slit or
       spike left where the boolean merged coplanar pieces): dissolving
       degenerate geometry within ``dist`` welds them shut.
    2. Large flat faces whose outline crosses itself in a tiny zig-zag (edges i
       and i+2 crossing, the middle edge running backwards): Blender's
       triangulation of the WHOLE face then folds, a black patch of tens of
       square millimetres from a knot a tenth of a millimetre wide. Collapsing
       the backwards edge unties it.

    3. Whatever is left: those faces are split into triangles (the shape is
       unchanged) and each triangle that still faces backwards - a sliver
       whose shortest edge is under 0.3 mm, left where a contoured seam meets
       a wing or the socket - is collapsed onto that edge. A backwards needle
       thinner than 0.02 mm is left as it is: it has no area to show.

    Returns the folded faces left."""
    bad = folded_faces(obj)
    if not bad:
        return 0
    for stage in (1, 2, 3):
        bak = obj.data.copy()
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        try:
            if stage == 1:
                bmesh.ops.dissolve_degenerate(bm, dist=dist, edges=bm.edges[:])
            elif stage == 3:
                bm.faces.ensure_lookup_table()
                bm.normal_update()
                faces = [bm.faces[fi] for fi in bad if fi < len(bm.faces)]
                normal = {f: f.normal.copy() for f in faces}
                out = bmesh.ops.triangulate(bm, faces=faces, quad_method='BEAUTY',
                                            ngon_method='BEAUTY')
                fmap = out.get("face_map", {})
                bm.normal_update()
                tris = [(t, normal.get(fmap.get(t, t))) for t in out["faces"]]

                def backwards():
                    # a needle thinner than 0.02 mm is degenerate, not a fold:
                    # nothing to see and nothing for a slicer to flip
                    out_ = []
                    for t, n0 in tris:
                        if not t.is_valid or n0 is None or t.normal.dot(n0) >= 0.0:
                            continue
                        longest = max(e.calc_length() for e in t.edges)
                        if 2.0 * t.calc_area() / max(longest, 1e-9) >= 0.02:
                            out_.append(t)
                    return out_
                # (none at all: only Blender's fill of the whole face folded)
                edges = []
                for t in backwards():
                    e = min(t.edges, key=lambda e_: e_.calc_length())
                    if e.calc_length() >= 0.3:
                        edges = None          # a real fold, not a sliver: leave it
                        break
                    edges.append(e)
                if edges is None:
                    bpy.data.meshes.remove(bak)
                    continue
                if edges:
                    edges = list({e.index: e for e in edges if e.is_valid}.values())
                    bmesh.ops.collapse(bm, edges=edges, uvs=False)
                    bm.normal_update()
                    if backwards():
                        bpy.data.meshes.remove(bak)
                        continue
                    loose = [e for e in bm.edges if e.is_wire]
                    if loose:
                        bmesh.ops.delete(bm, geom=loose, context='EDGES')
                    lv = [v for v in bm.verts if not v.link_edges]
                    if lv:
                        bmesh.ops.delete(bm, geom=lv, context='VERTS')
                # (no dissolve here: welding the needle triangles opens the
                # mesh where they sit between two real faces)
            else:
                bm.faces.ensure_lookup_table()
                bm.normal_update()
                edges = []
                for fi in bad:
                    if fi < len(bm.faces):
                        edges += _zigzag_edges(bm, bm.faces[fi])
                edges = list({e.index: e for e in edges if e.is_valid}.values())
                if not edges:
                    bpy.data.meshes.remove(bak)
                    continue
                bmesh.ops.collapse(bm, edges=edges, uvs=False)
                bmesh.ops.dissolve_degenerate(bm, dist=dist, edges=bm.edges[:])
                loose = [e for e in bm.edges if e.is_wire]
                if loose:                     # a collapse can leave a stray edge
                    bmesh.ops.delete(bm, geom=loose, context='EDGES')
                lv = [v for v in bm.verts if not v.link_edges]
                if lv:
                    bmesh.ops.delete(bm, geom=lv, context='VERTS')
            bm.to_mesh(obj.data)
            obj.data.update()
        finally:
            bm.free()
        left = folded_faces(obj)
        if len(left) < len(bad) and part_is_valid(obj)[0]:
            if bak.users == 0:
                bpy.data.meshes.remove(bak)
            bad = left
            if not bad:
                return 0
            continue
        cur = obj.data
        obj.data = bak
        if cur.users == 0:
            bpy.data.meshes.remove(cur)
    return len(bad)


def part_is_valid(obj):
    """A finished mold part must be a finite, watertight, single solid.

    Returns (ok, reason). Used to turn silent boolean/geometry corruption
    (shards, non-manifold output, NaN) into a clear error instead of a broken STL.
    """
    me = obj.data
    if not me.polygons:
        return False, "is empty"
    for v in me.vertices:
        co = v.co
        if not (math.isfinite(co.x) and math.isfinite(co.y) and math.isfinite(co.z)):
            return False, "has non-finite (NaN/inf) coordinates"

    bm = bmesh.new()
    bm.from_mesh(me)
    try:
        if any(not e.is_manifold for e in bm.edges):
            return False, "is not watertight (non-manifold edges)"
        seen = set()
        components = 0
        for f in bm.faces:
            if f in seen:
                continue
            components += 1
            if components > 1:
                break
            stack = [f]
            while stack:
                cf = stack.pop()
                if cf in seen:
                    continue
                seen.add(cf)
                for e in cf.edges:
                    for lf in e.link_faces:
                        if lf not in seen:
                            stack.append(lf)
    finally:
        bm.free()
    if components != 1:
        return False, "has disconnected/floating pieces"
    return True, ""


def undercut_fraction(obj, axis, res=16):
    """Fraction of the model's silhouette that a two-part mold pulling along ``axis``
    would trap (an undercut). Fire rays along the pull axis through a grid: a shape
    that releases cleanly is crossed at most twice (front + back), so any ray that
    crosses the surface more than twice sits over a trapped pocket. Cheap draft check
    run before printing."""
    ai = {'X': 0, 'Y': 1, 'Z': 2}[axis]
    u, v = [k for k in range(3) if k != ai]
    mn, mx = world_bbox(obj)
    span = (mx - mn).length + 10.0
    mw = obj.matrix_world
    inv = mw.inverted()
    nd = Vector((0.0, 0.0, 0.0)); nd[ai] = -1.0
    dl = (inv.to_3x3() @ nd).normalized()

    total = trapped = 0
    for i in range(res):
        for j in range(res):
            uu = mn[u] + (mx[u] - mn[u]) * (i + 0.5) / res
            vv = mn[v] + (mx[v] - mn[v]) * (j + 0.5) / res
            cur = mx[ai] + span
            crossings = 0
            for _ in range(24):
                wp = Vector((0.0, 0.0, 0.0)); wp[u] = uu; wp[v] = vv; wp[ai] = cur
                hit, loc, _n, _idx = obj.ray_cast(inv @ wp, dl)
                if not hit:
                    break
                crossings += 1
                cur = (mw @ loc)[ai] - 1e-3
            if crossings:
                total += 1
                if crossings > 2:
                    trapped += 1
    return (trapped / total) if total else 0.0


def new_mesh_object(name, bm, coll):
    """Consume a bmesh into a new object linked to ``coll``."""
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name, me)
    coll.objects.link(obj)
    return obj


def duplicate_object(obj, name, coll):
    me = obj.data.copy()
    dup = bpy.data.objects.new(name, me)
    dup.matrix_world = obj.matrix_world.copy()
    coll.objects.link(dup)
    return dup


def remove_object(obj):
    me = obj.data if obj.type == 'MESH' else None
    bpy.data.objects.remove(obj, do_unlink=True)
    if me is not None and me.users == 0:
        bpy.data.meshes.remove(me)


def join_meshes(target, src):
    """Append ``src``'s geometry into ``target`` (brought into ``target``'s local space)
    and remove ``src``. A plain mesh merge — no boolean — so it never fails and keeps
    full detail; overlapping closed solids print and nest fine."""
    bm = bmesh.new()
    bm.from_mesh(target.data)
    tmp = src.data.copy()
    tmp.transform(target.matrix_world.inverted() @ src.matrix_world)
    bm.from_mesh(tmp)
    bpy.data.meshes.remove(tmp)
    bm.to_mesh(target.data)
    bm.free()
    target.data.update()
    remove_object(src)


def apply_all_modifiers(obj):
    """Bake the evaluated mesh back onto the object (applies every modifier)."""
    deps = bpy.context.evaluated_depsgraph_get()
    eval_obj = obj.evaluated_get(deps)
    new_me = bpy.data.meshes.new_from_object(
        eval_obj, preserve_all_data_layers=False, depsgraph=deps
    )
    old_me = obj.data
    obj.modifiers.clear()
    obj.data = new_me
    if old_me.users == 0:
        bpy.data.meshes.remove(old_me)


def world_bbox(obj):
    """World-space axis-aligned bounds.

    Computed from mesh vertices so it is always current — ``object.bound_box``
    can lag a mesh that was just edited in-place without a depsgraph update.
    """
    mw = obj.matrix_world
    me = obj.data if obj.type == 'MESH' else None
    if me is not None and len(me.vertices):
        mn = (mw @ me.vertices[0].co).copy()
        mx = mn.copy()
        for v in me.vertices:
            w = mw @ v.co
            mn.x = min(mn.x, w.x); mn.y = min(mn.y, w.y); mn.z = min(mn.z, w.z)
            mx.x = max(mx.x, w.x); mx.y = max(mx.y, w.y); mx.z = max(mx.z, w.z)
        return mn, mx

    corners = [mw @ Vector(c) for c in obj.bound_box]
    mn = Vector((min(c.x for c in corners),
                 min(c.y for c in corners),
                 min(c.z for c in corners)))
    mx = Vector((max(c.x for c in corners),
                 max(c.y for c in corners),
                 max(c.z for c in corners)))
    return mn, mx


def boolean(obj, cutter, operation='DIFFERENCE', solver='MANIFOLD'):
    """Add a boolean modifier referencing ``cutter`` and bake it in.

    MANIFOLD is fast and robust through MoldForge's chain of watertight cuts, but it
    *silently* refuses non-manifold input ("Cannot execute, non-manifold inputs") and
    leaves the mesh untouched — which used to produce broken molds. So whenever either
    input isn't manifold (e.g. a carved-from-scan cavity cutter), fall back to the
    slower but tolerant EXACT solver automatically.
    """
    if solver == 'MANIFOLD' and (has_nonmanifold(obj) or has_nonmanifold(cutter)):
        solver = 'EXACT'
    mod = obj.modifiers.new("mf_bool", 'BOOLEAN')
    mod.operation = operation
    try:
        mod.solver = solver
    except (TypeError, AttributeError):
        mod.solver = 'EXACT'
    mod.object = cutter
    apply_all_modifiers(obj)


def _axis_rotation(axis):
    """Rotation that maps local +Z onto the given world axis."""
    if axis == 'X':
        return Matrix.Rotation(math.radians(90.0), 4, 'Y')
    if axis == 'Y':
        return Matrix.Rotation(math.radians(-90.0), 4, 'X')
    return Matrix.Identity(4)


def shave_thin_blades(obj, max_thick=0.4, min_span=3.0):
    """Remove paper-thin boolean shards: a vent bore that grazes a seam plane
    or a sloped outer wall leaves a sub-millimetre blade standing on the shell
    (a fragile needle right where a Locking Base opens at the bottom). Finds
    connected clusters of long-skinny faces that are (a) thinner than
    ``max_thick`` across their world bbox, (b) longer than ``min_span`` and
    (c) genuinely two-sided (opposed normals - a flat triangulated wall face
    is skinny too but must never be cut), then shaves each off with a small
    padded box DIFFERENCE. Returns how many blades were removed."""
    mw = obj.matrix_world
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    skinny = set()
    for f in bm.faces:
        a = f.calc_area()
        if a <= 1e-9:
            continue
        e = max(ed.calc_length() for ed in f.edges)
        if e > 1.2 and e * e / a > 60.0:
            skinny.add(f.index)
    boxes = []
    seen = set()
    for fi in skinny:
        if fi in seen:
            continue
        stack, comp = [fi], []
        while stack:
            k = stack.pop()
            if k in seen:
                continue
            seen.add(k)
            comp.append(k)
            for ed in bm.faces[k].edges:
                for lf in ed.link_faces:
                    if lf.index in skinny and lf.index not in seen:
                        stack.append(lf.index)
        if len(comp) < 3:
            continue
        pts = [mw @ v.co for k in comp for v in bm.faces[k].verts]
        dims = sorted((max(q.x for q in pts) - min(q.x for q in pts),
                       max(q.y for q in pts) - min(q.y for q in pts),
                       max(q.z for q in pts) - min(q.z for q in pts)))
        if dims[0] >= max_thick or dims[2] <= min_span:
            continue
        normals = [bm.faces[k].normal.copy() for k in comp[:200]]
        opposed = any(normals[i].dot(normals[j]) < -0.6
                      for i in range(len(normals))
                      for j in range(i + 1, len(normals)))
        if not opposed:
            continue
        lo = Vector((min(q.x for q in pts), min(q.y for q in pts),
                     min(q.z for q in pts)))
        hi = Vector((max(q.x for q in pts), max(q.y for q in pts),
                     max(q.z for q in pts)))
        boxes.append((lo, hi))
    bm.free()
    for lo, hi in boxes[:8]:
        cutter = add_box("MF_shave", (lo + hi) * 0.5,
                         Vector((hi.x - lo.x + 0.7, hi.y - lo.y + 0.7,
                                 hi.z - lo.z + 0.7)),
                         obj.users_collection[0] if obj.users_collection else None)
        boolean(obj, cutter, 'DIFFERENCE')
        remove_object(cutter)
    return min(len(boxes), 8)


def add_box(name, center, size, coll, rot_z=0.0):
    """Box of dimensions ``size`` (Vector) centered at ``center``, optionally
    rotated ``rot_z`` radians about its own vertical axis (for radial seams)."""
    bm = bmesh.new()
    mat = Matrix.Translation(center) @ Matrix.Rotation(rot_z, 4, 'Z') @ Matrix.Diagonal(
        Vector((size.x, size.y, size.z, 1.0))
    )
    bmesh.ops.create_cube(bm, size=1.0, matrix=mat)
    return new_mesh_object(name, bm, coll)


def add_tube(name, a, b, r, coll, segs=32):
    """Solid round bar of radius ``r`` between world points ``a`` and ``b``
    (external runner tubes/stubs)."""
    d = Vector(b) - Vector(a)
    length = max(d.length, 1e-4)
    mat = (Matrix.Translation((Vector(a) + Vector(b)) * 0.5)
           @ d.to_track_quat('Z', 'Y').to_matrix().to_4x4())
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=segs, radius1=r, radius2=r,
                          depth=length, matrix=mat)
    return new_mesh_object(name, bm, coll)


def add_sphere(name, center, radius, coll, subdiv=2):
    """Icosphere of ``radius`` centered at ``center`` (registration bumps etc.)."""
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=subdiv, radius=radius,
                               matrix=Matrix.Translation(center))
    return new_mesh_object(name, bm, coll)


def add_cone(name, center, radius1, radius2, depth, axis, coll, segments=48, rot_z=0.0):
    """Cone/cylinder along ``axis`` ('X'/'Y'/'Z'), optionally spun ``rot_z`` radians
    about Z first (so an 'X' cone can point along any horizontal direction).
    radius1 is the -axis end."""
    bm = bmesh.new()
    mat = Matrix.Translation(center) @ Matrix.Rotation(rot_z, 4, 'Z') @ _axis_rotation(axis)
    bmesh.ops.create_cone(
        bm, cap_ends=True, cap_tris=False, segments=segments,
        radius1=radius1, radius2=radius2, depth=depth, matrix=mat,
    )
    return new_mesh_object(name, bm, coll)
