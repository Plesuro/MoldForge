"""Low-level Blender helpers: object/mesh creation, modifier baking, booleans.

Everything here avoids ``bpy.ops`` so it runs reliably in background mode.
"""

import math

import bpy
import bmesh
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

COLLECTION_NAME = "MoldForge"


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


def add_box(name, center, size, coll, rot_z=0.0):
    """Box of dimensions ``size`` (Vector) centered at ``center``, optionally
    rotated ``rot_z`` radians about its own vertical axis (for radial seams)."""
    bm = bmesh.new()
    mat = Matrix.Translation(center) @ Matrix.Rotation(rot_z, 4, 'Z') @ Matrix.Diagonal(
        Vector((size.x, size.y, size.z, 1.0))
    )
    bmesh.ops.create_cube(bm, size=1.0, matrix=mat)
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
