"""Headless test + correctness suite for MoldForge.

Run:  blender --background --python moldforge/tests/test_headless.py

Beyond topology (watertight, single piece) this asserts the mold is correctly
*proportioned*: the mold-to-model size ratio must be sane and the SAME at 2-unit,
20-unit and 200-unit model scales (the bug that shipped was scale-dependent
sizing that cratered small models). Exits non-zero on any failure.
"""

import math
import os
import struct
import sys
import types

import bpy
import bmesh
from mathutils import Euler, Matrix, Vector

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(THIS_DIR))
sys.path.insert(0, REPO_ROOT)

import moldforge  # noqa: E402
from moldforge.core import pipeline, volume, split, util as mfutil  # noqa: E402

FAILURES = []


def check(condition, message):
    print(f"  [{'PASS' if condition else 'FAIL'}] {message}")
    if not condition:
        FAILURES.append(message)


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


# --- master builders ------------------------------------------------------ #

def make_master(name="Model", scale=(1.0, 0.8, 1.3), radius=10.0, subdiv=3):
    """A closed, asymmetric blob ~20 units across (the default test model)."""
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=subdiv, radius=radius)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me); bm.free()
    obj = bpy.data.objects.new(name, me)
    obj.data.transform(Matrix.Diagonal(Vector((*scale, 1.0))))
    bpy.context.scene.collection.objects.link(obj)
    return obj


def make_axisym_master(name="Model", radius=10.0, zscale=1.3):
    """A high-segment UV sphere, near-axisymmetric about Z: a correct radial split
    yields congruent wedges, so any wedge imbalance exposes a seam/centre bug."""
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=64, v_segments=32, radius=radius)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me); bm.free()
    obj = bpy.data.objects.new(name, me)
    obj.data.transform(Matrix.Diagonal(Vector((1.0, 1.0, zscale, 1.0))))
    bpy.context.scene.collection.objects.link(obj)
    return obj


def cube_master(size, name="Model"):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me); bm.free()
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def relief_master(name="Plaque", rot=None):
    """A flat, single-manifold relief: a 24x16x3 slab with a raised 10x6x2 bump on
    +Z (properly unioned). Optionally rotated, to exercise tray auto-orientation."""
    base = cube_master(1.0, name)
    base.data.transform(Matrix.Diagonal(Vector((24.0, 16.0, 3.0, 1.0))))
    bump = cube_master(1.0, "bump")
    bump.data.transform(Matrix.Translation(Vector((0.0, 0.0, 2.5)))
                        @ Matrix.Diagonal(Vector((10.0, 6.0, 2.0, 1.0))))
    mfutil.boolean(base, bump, 'UNION')
    mfutil.remove_object(bump)
    if rot is not None:
        base.data.transform(rot)
    base.data.update()
    return base


def default_props():
    return types.SimpleNamespace(
        box_style='SOLID',
        solid_shape='HUG',
        skin_keys=False,
        wall_thickness=3.0,
        shell_wall=2.0,
        sprue_radius=4.0,
        base_style='FOLLOW',
        base_flange=False,
        base_plate=False,
        flange_width=6.0,
        wings=False,
        wing_width=8.0,
        split_axis='AUTO',
        contoured=False,
        key_count=4,
        parts_count=2,
        sprue=True,
        funnel_height=12.0,
        sprue_count=1,
        sprue_place='TOP',
        vent_count=2,
        vent_radius=1.0,
        bolt_diameter=3.0,
        bolt_auto=True,
        bolt_count=0,
        heal=True,
        decimate=False,
        decimate_ratio=0.5,
        voxel_safe=False,
        voxel_size=1.0,
        export_after=False,
        export_dir="//",
        tray_mode='EMBED',
        tray_up='AUTO',
        tray_outline='RECT',
        tray_wall=2.5,
        tray_floor=3.0,
        tray_margin=6.0,
        tray_depth=5.0,
        last_cavity_volume=0.0,
        last_silicone_volume=0.0,
        last_plastic_volume=0.0,
    )


def _over(props, **kw):
    for k, v in kw.items():
        setattr(props, k, v)
    return props


def _rename(obj, name):
    obj.name = name
    return obj


# --- geometry helpers ----------------------------------------------------- #

def part_face_count(obj):
    return len(obj.data.polygons)


def nonmanifold_edges(obj):
    bm = bmesh.new(); bm.from_mesh(obj.data)
    n = sum(1 for e in bm.edges if not e.is_manifold)
    bm.free()
    return n


def island_count(obj):
    bm = bmesh.new(); bm.from_mesh(obj.data)
    seen = set(); count = 0
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
    bm.free()
    return count


def combined_bbox(objs):
    mns = []; mxs = []
    for o in objs:
        a, b = mfutil.world_bbox(o)
        mns.append(a); mxs.append(b)
    mn = Vector((min(m.x for m in mns), min(m.y for m in mns), min(m.z for m in mns)))
    mx = Vector((max(m.x for m in mxs), max(m.y for m in mxs), max(m.z for m in mxs)))
    return mn, mx


def max_dim(mn, mx):
    d = mx - mn
    return max(d.x, d.y, d.z)


def flat_bottom_area(obj):
    me = obj.data
    if not me.vertices:
        return 0.0
    zmin = min(v.co.z for v in me.vertices)
    return sum(p.area for p in me.polygons
               if all(abs(me.vertices[i].co.z - zmin) < 1e-3 for i in p.vertices))


def char_size(obj):
    mn, mx = mfutil.world_bbox(obj)
    d = mx - mn
    return (d.x + d.y + d.z) / 3.0


def x_crossings(obj, z):
    """X positions of every surface a -X ray through (100, 0, z) crosses —
    used to prove a mold half's cavity is open at the parting plane."""
    mw = obj.matrix_world
    inv = mw.inverted()
    d = (inv.to_3x3() @ Vector((-1.0, 0.0, 0.0))).normalized()
    cur = Vector((100.0, 0.0, z))
    out = []
    for _ in range(24):
        hit, loc, _n, _i = obj.ray_cast(inv @ cur, d)
        if not hit:
            break
        w = mw @ loc
        out.append(w.x)
        cur = w + Vector((-1e-3, 0.0, 0.0))
    return out


def verify_stl(path):
    """Framing AND geometry: all coords finite, at least one non-zero triangle."""
    size = os.path.getsize(path)
    finite = True
    nonzero_area = False
    with open(path, 'rb') as f:
        f.read(80)
        (count,) = struct.unpack('<I', f.read(4))
        framing_ok = (size == 84 + count * 50)
        for _ in range(count):
            chunk = f.read(50)
            if len(chunk) < 50:
                framing_ok = False
                break
            vals = struct.unpack('<12fH', chunk)
            coords = vals[3:12]
            if not all(math.isfinite(c) for c in coords):
                finite = False
            p0 = Vector(coords[0:3]); p1 = Vector(coords[3:6]); p2 = Vector(coords[6:9])
            if (p1 - p0).cross(p2 - p0).length > 1e-9:
                nonzero_area = True
    return count, size, (framing_ok and count > 0 and finite and nonzero_area)


# --- adversarial masters -------------------------------------------------- #

def make_nan_master():
    o = make_master("Model")
    o.data.vertices[0].co = Vector((float('nan'), 0.0, 0.0))
    return o


def make_nonmanifold_master():
    bm = bmesh.new()
    vs = [bm.verts.new(p) for p in
          [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0), (5, 5, 8), (5, -5, 8)]]
    bm.faces.new((vs[0], vs[1], vs[2])); bm.faces.new((vs[0], vs[2], vs[3]))
    bm.faces.new((vs[0], vs[1], vs[4])); bm.faces.new((vs[0], vs[1], vs[5]))
    me = bpy.data.meshes.new("Model"); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new("Model", me)
    bpy.context.scene.collection.objects.link(o)
    return o


def make_multi_island_master():
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=3, radius=6, matrix=Matrix.Translation((-9, 0, 0)))
    bmesh.ops.create_icosphere(bm, subdivisions=3, radius=6, matrix=Matrix.Translation((9, 0, 0)))
    me = bpy.data.meshes.new("Model"); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new("Model", me)
    bpy.context.scene.collection.objects.link(o)
    return o


def make_thin_fin_master():
    sb = bmesh.new(); bmesh.ops.create_icosphere(sb, subdivisions=4, radius=12)
    sme = bpy.data.meshes.new("Model"); sb.to_mesh(sme); sb.free()
    sph = bpy.data.objects.new("Model", sme)
    bpy.context.scene.collection.objects.link(sph)
    fb = bmesh.new()
    bmesh.ops.create_cube(fb, size=1.0,
                          matrix=Matrix.Translation((0, 0, 14)) @ Matrix.Diagonal(Vector((24, 1.5, 18, 1))))
    fme = bpy.data.meshes.new("Fin"); fb.to_mesh(fme); fb.free()
    fin = bpy.data.objects.new("Fin", fme)
    bpy.context.scene.collection.objects.link(fin)
    mod = sph.modifiers.new('u', 'BOOLEAN'); mod.operation = 'UNION'; mod.object = fin
    mfutil.apply_all_modifiers(sph); mfutil.remove_object(fin)
    return sph


def make_bird_master():
    """A smooth single solid with concave creases (sphere body + head + neck) —
    the kind of model where Solidify shards but the dilation should resolve it."""
    def sph(r, loc, sc=(1, 1, 1)):
        bm = bmesh.new(); bmesh.ops.create_icosphere(bm, subdivisions=4, radius=r)
        me = bpy.data.meshes.new("p"); bm.to_mesh(me); bm.free()
        o = bpy.data.objects.new("p", me)
        o.data.transform(Matrix.Translation(loc) @ Matrix.Diagonal(Vector((*sc, 1))))
        bpy.context.scene.collection.objects.link(o)
        return o
    body = sph(10, (0, 0, 0), (1.3, 1, 1)); head = sph(5, (11, 0, 8)); neck = sph(4, (7, 0, 5), (1, 1, 1.6))
    for x in (head, neck):
        md = body.modifiers.new('u', 'BOOLEAN'); md.operation = 'UNION'; md.object = x
        mfutil.apply_all_modifiers(body); mfutil.remove_object(x)
    body.name = "Model"
    return body


def make_bulbs_master():
    """Stacked bulbs with deep ring creases between them — a Solidify offset
    folds over itself across such creases (self-intersections that stay
    watertight + single-island, invisible to the other checks)."""
    def sph(r, z):
        bm = bmesh.new(); bmesh.ops.create_icosphere(bm, subdivisions=4, radius=r)
        me = bpy.data.meshes.new("p"); bm.to_mesh(me); bm.free()
        o = bpy.data.objects.new("p", me)
        o.data.transform(Matrix.Translation((0, 0, z)))
        bpy.context.scene.collection.objects.link(o)
        return o
    base = sph(10, 0)
    for r, z in ((8.0, 13.0), (8.8, 25.0), (5.5, 36.0)):
        s = sph(r, z)
        md = base.modifiers.new('u', 'BOOLEAN'); md.operation = 'UNION'; md.object = s
        mfutil.apply_all_modifiers(base); mfutil.remove_object(s)
    base.name = "Model"
    return base


def make_crescent_master():
    """A deep concave (horseshoe / C) solid: a sphere with a wide notch carved out.
    Solidify and the pour-box difference throw few-face slivers off the concavity;
    without sliver cleanup the split reports 'disconnected/floating pieces' and even
    the coarse-remesh retry can't fix it (the model stays concave)."""
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=20, radius=14)
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if v.co.x > 0 and abs(v.co.y) < 5],
                     context='VERTS')
    me = bpy.data.meshes.new("Model"); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new("Model", me)
    bpy.context.scene.collection.objects.link(o)
    return o


def expect_clean_error(title, make_fn, overrides=None):
    print(f"\n=== {title} (expect clean error) ===")
    reset_scene()
    m = make_fn()
    try:
        pipeline.build_mold_system(m, _over(default_props(), **(overrides or {})))
        check(False, f"{title}: SHOULD have raised, but produced output")
    except (ValueError, RuntimeError) as exc:
        check(True, f"{title}: clean error -> {str(exc)[:55]}")
    check(not [o for o in bpy.data.objects if o.name.startswith("MF_Mold_")],
          f"{title}: no broken parts left behind")


def expect_success(title, make_fn, overrides=None):
    """A detailed/thin model should still build valid halves (shards resolved in
    the dilation, or via the coarse auto-recover as a fallback)."""
    print(f"\n=== {title} (expect a valid mold) ===")
    reset_scene()
    m = make_fn()
    try:
        res = pipeline.build_mold_system(m, _over(default_props(), **(overrides or {})))
        check(all(mfutil.part_is_valid(p)[0] for p in res["parts"]),
              f"{title}: builds valid solid halves (remeshed={res.get('remeshed')})")
    except Exception as exc:
        check(False, f"{title}: should have built but raised: {str(exc)[:50]}")


# --- main scenario runner ------------------------------------------------- #

def scenario(title, mutate, expect_solid=True):
    print(f"\n=== {title} ===")
    reset_scene()
    master = make_master("Model")
    props = default_props()
    mutate(props)

    result = pipeline.build_mold_system(master, props)
    parts = result["parts"]
    positive = result["positive"]

    check(len(parts) == 2, "produced two mold halves")
    for i, part in enumerate(parts):
        check(part_face_count(part) > 0, f"half {i} has geometry")
        check(volume.mesh_volume(part) > 0.0, f"half {i} positive volume")
        check(nonmanifold_edges(part) == 0, f"half {i} watertight")
        check(island_count(part) == 1, f"half {i} single piece")

    # Proportion: the mold is a sane multiple of the model (not a crater/bulge).
    pmn, pmx = mfutil.world_bbox(positive)
    model_dim = max_dim(pmn, pmx)
    mmn, mmx = combined_bbox(parts)
    ratio = max_dim(mmn, mmx) / model_dim
    check(1.05 < ratio < 3.0, f"mold is a sane multiple of the model ({ratio:.2f}x)")
    result["mold_ratio"] = ratio

    check(result["cavity_volume"] > 0.0, "cavity volume positive")
    check(result["silicone_volume"] > 0.0, "silicone volume positive")

    out_dir = os.path.join("/tmp", "mf_out", title.replace(" ", "_"))
    written = mfutil_export(parts, out_dir)
    check(len(written) == 2, "exported two STL files")
    for path in written:
        count, size, ok = verify_stl(path)
        check(ok and count > 0, f"valid STL {os.path.basename(path)} ({count} tris)")

    result["part_faces"] = sum(part_face_count(o) for o in parts)
    result["bottom_area"] = sum(flat_bottom_area(o) for o in parts)
    return result


def mfutil_export(parts, out_dir):
    from moldforge.core import export as mf_export
    return mf_export.export_objects(parts, out_dir)


def main():
    print(f"Blender {bpy.app.version_string}")
    moldforge.register()
    check(hasattr(bpy.types.Scene, "moldforge"), "Scene.moldforge registered")

    adaptive = scenario("Adaptive shell", lambda p: None)
    block = scenario("Block style", lambda p: (
        setattr(p, "box_style", 'SOLID'), setattr(p, "solid_shape", 'BLOCK')))
    check(adaptive["silicone_volume"] < block["silicone_volume"],
          f"adaptive uses less silicone than block "
          f"({adaptive['silicone_volume']:.0f} < {block['silicone_volume']:.0f})")

    flat = scenario("Flat base (adaptive)", lambda p: setattr(p, "base_style", 'FLAT'))
    check(adaptive["bottom_area"] < 1.0, "rounded shell has ~no flat bottom")
    check(flat["bottom_area"] > 20.0, f"flat base creates a real flat ({flat['bottom_area']:.0f})")

    pour = scenario("Silicone pour box", lambda p: setattr(p, "box_style", 'POUR_BOX'))
    check(pour["plastic_volume"] > 0.0, f"pour box plastic positive ({pour['plastic_volume']:.0f})")
    scenario("Solid wall=gap+shell",
             lambda p: (setattr(p, "box_style", 'SOLID'), setattr(p, "wall_thickness", 5.0)))
    # The pour box is a thin printed shell — far less material than a solid chunk
    # the size of the mold (the block mold is that chunk).
    check(pour["plastic_volume"] < 0.7 * block["silicone_volume"],
          f"pour box is hollow ({pour['plastic_volume']:.0f} < 0.7×{block['silicone_volume']:.0f})")

    # Silicone to pour = the GAP around the model (dilate(model,gap) - model), not
    # the whole dilated solid. Regression: a clean sphere's gap must match analytics.
    print("\n=== Pour silicone = gap shell, not model+gap ===")
    reset_scene()
    sb = bmesh.new(); bmesh.ops.create_icosphere(sb, subdivisions=3, radius=20)
    sme = bpy.data.meshes.new("Model"); sb.to_mesh(sme); sb.free()
    sph = bpy.data.objects.new("Model", sme); bpy.context.scene.collection.objects.link(sph)
    rs = pipeline.build_mold_system(sph, _over(default_props(), box_style='POUR_BOX',
                                               base_style='FOLLOW', wings=False, sprue=False))
    shell_analytic = 4.0 / 3.0 * math.pi * ((20.0 + 3.0) ** 3 - 20.0 ** 3)
    sil = rs["silicone_volume"]
    check(sil < rs["cavity_volume"],
          f"silicone is the gap, not model+gap ({sil:.0f} < model {rs['cavity_volume']:.0f})")
    # Exact dilations (no remesh smoothing) must land within a few % of analytic
    # — the icosphere is slightly inside a true sphere, hence the small slack.
    check(0.90 * shell_analytic < sil < 1.08 * shell_analytic,
          f"silicone ~ analytic gap shell ({sil:.0f} vs ~{shell_analytic:.0f})")

    # Pour box + flat base must keep a closed floor (no silicone leak).
    pf = scenario("Pour box + flat base",
                  lambda p: (setattr(p, "box_style", 'POUR_BOX'), setattr(p, "base_style", 'FLAT')))
    gap = 3.0                                            # wall_thickness default
    box_bottom = min(mfutil.world_bbox(o)[0].z for o in pf["parts"])
    cavity_floor = mfutil.world_bbox(pf["positive"])[0].z - gap
    check(cavity_floor - box_bottom > 0.02 * gap,
          f"pour box flat base keeps a closed floor (thk {cavity_floor - box_bottom:.2f})")

    scenario("Decimate", lambda p: (setattr(p, "decimate", True), setattr(p, "decimate_ratio", 0.3)))

    scenario("Safe remesh (solid)", lambda p: (
        setattr(p, "box_style", 'SOLID'), setattr(p, "voxel_safe", True)))
    scenario("Safe remesh (pour box)", lambda p: (
        setattr(p, "box_style", 'POUR_BOX'), setattr(p, "voxel_safe", True)))

    # The positive NEVER loses detail to the cleanup remesh: the displayed and
    # exported master is a hi-poly snapshot taken BEFORE any voxel pass - for
    # EVERY mold type now, not just the Locking Base (a heavy or non-manifold
    # sculpt used to come back faceted on plain pour boxes).
    reset_scene()
    bmh = bmesh.new()
    bmesh.ops.create_icosphere(bmh, subdivisions=4, radius=10.0)
    hm = bpy.data.meshes.new("HiPoly"); bmh.to_mesh(hm); bmh.free()
    ho = bpy.data.objects.new("HiPoly", hm)
    bpy.context.scene.collection.objects.link(ho)
    hi_faces = len(ho.data.polygons)
    rs_pb = pipeline.build_mold_system(ho, _over(default_props(),
            box_style='POUR_BOX', base_style='FLAT', voxel_safe=True))
    pos_faces = part_face_count(rs_pb["positive"])
    check(pos_faces >= hi_faces * 0.9,
          f"pour-box positive keeps hi-poly detail under Safe Remesh "
          f"({pos_faces} faces vs master {hi_faces})")
    check(rs_pb.get("remeshed") is False,
          "pour box: proxy remesh not flagged (cast detail comes from the nested master)")
    reset_scene()
    bmh = bmesh.new()
    bmesh.ops.create_icosphere(bmh, subdivisions=4, radius=10.0)
    hm = bpy.data.meshes.new("HiPoly2"); bmh.to_mesh(hm); bmh.free()
    ho2 = bpy.data.objects.new("HiPoly2", hm)
    bpy.context.scene.collection.objects.link(ho2)
    rs_dm = pipeline.build_mold_system(ho2, _over(default_props(),
            box_style='SOLID', voxel_safe=True))
    check(part_face_count(rs_dm["positive"]) >= hi_faces * 0.9
          and rs_dm.get("remeshed") is True,
          "direct mold: positive still hi-poly, but the smoothed CAVITY is flagged")
    scenario("No keys, no vents", lambda p: (
        setattr(p, "key_count", 0), setattr(p, "vent_count", 0)))
    scenario("3 keys, split Y", lambda p: (
        setattr(p, "key_count", 3), setattr(p, "split_axis", 'Y')))
    scenario("1 key, block", lambda p: (
        setattr(p, "key_count", 1), setattr(p, "box_style", 'SOLID'),
        setattr(p, "solid_shape", 'BLOCK')))
    scenario("Heal off", lambda p: setattr(p, "heal", False))
    scenario("Contoured parting", lambda p: setattr(p, "contoured", True))

    # --- Absolute walls: a 3-unit wall adds ~6 to the footprint regardless of model size
    print("\n=== Absolute wall thickness ===")
    for size in (20.0, 60.0):
        reset_scene()
        res = pipeline.build_mold_system(cube_master(size),
                                         _over(default_props(), box_style='SOLID', solid_shape='BLOCK', wall_thickness=3.0))
        mn, mx = combined_bbox(res["parts"])
        added = (mx.x - mn.x) - size
        check(abs(added - 6.0) < 1.5, f"{size:.0f}u cube + 3u wall -> +{added:.1f} (expect ~6)")

    # --- Positioning: the mold lands on the master, not at the origin
    print("\n=== Mold lands on the master's location ===")
    reset_scene()
    m = make_master("Model")
    m.location = Vector((40.0, -25.0, 15.0))
    bpy.context.view_layer.update()   # refresh matrix_world after moving it
    # sprue off: the raised pour funnel legitimately adds height, which would skew a
    # pure centring check.
    res = pipeline.build_mold_system(m, _over(default_props(), sprue=False))
    bpy.context.view_layer.update()   # refresh matrix_world after the result was moved
    cmn, cmx = combined_bbox(res["parts"])
    mold_center = (cmn + cmx) * 0.5
    mmn, mmx = mfutil.world_bbox(m)
    master_center = (mmn + mmx) * 0.5
    off = (mold_center - master_center).length
    check(off < 0.15 * char_size(res["positive"]),
          f"mold is centered on the master (offset {off:.2f})")

    # --- Clamp wings widen the sides + stay valid
    print("\n=== Clamp wings ===")
    reset_scene()
    base = footprint = None
    rno = pipeline.build_mold_system(make_master("Model"),
                                     _over(default_props(), box_style='SOLID', wings=False, sprue=False))
    no_w = max_dim(*combined_bbox(rno["parts"]))
    reset_scene()
    rwg = pipeline.build_mold_system(make_master("Model"),
                                     _over(default_props(), box_style='SOLID', wings=True,
                                           wing_width=10.0, sprue=False))
    with_w = max_dim(*combined_bbox(rwg["parts"]))
    check(with_w > no_w + 5.0, f"wings widen the mold ({with_w:.1f} > {no_w:.1f})")
    check(all(mfutil.part_is_valid(p)[0] for p in rwg["parts"]), "winged halves are valid solids")

    # --- Wing Alignment keys: cone / half sphere / half cone on the wing mating faces.
    # Each style must raise male keys on half A (its volume grows vs no keys) and cut
    # matching sockets into half B (its volume shrinks), with both halves still valid.
    print("\n=== Wing alignment keys (cone / half sphere / half cone) ===")
    wk_kw = dict(box_style='POUR_BOX', base_style='FOLLOW', wings=True, sprue=False,
                 vent_count=0, contoured=False, split_axis='X')
    reset_scene()
    w0 = pipeline.build_mold_system(make_master("Model"),
                                    _over(default_props(), wing_keys='NONE', **wk_kw))
    vA0 = volume.mesh_volume(w0["parts"][0])
    vB0 = volume.mesh_volume(w0["parts"][1])
    for style in ('CONE', 'DOME', 'FRUSTUM'):
        reset_scene()
        wk = pipeline.build_mold_system(make_master("Model"),
                                        _over(default_props(), wing_keys=style, **wk_kw))
        check(all(mfutil.part_is_valid(q)[0] for q in wk["parts"]),
              f"wing keys {style}: halves valid")
        vA = volume.mesh_volume(wk["parts"][0])
        vB = volume.mesh_volume(wk["parts"][1])
        check(vA > vA0 + 0.5, f"wing keys {style}: raised keys on half A (+{vA - vA0:.1f} u³)")
        check(vB < vB0 - 0.5, f"wing keys {style}: sockets cut in half B (-{vB0 - vB:.1f} u³)")
    # The parting is flat across the wings even when contoured — keys must work there too.
    reset_scene()
    wkc = pipeline.build_mold_system(make_master("Model"),
                                     _over(default_props(), wing_keys='DOME',
                                           **{**wk_kw, "contoured": True}))
    check(all(mfutil.part_is_valid(q)[0] for q in wkc["parts"]),
          "wing keys on a contoured parting: valid halves")

    # Key Size scales the keys (footprint; protrusion stays capped by the wing lip)...
    reset_scene()
    wks = pipeline.build_mold_system(make_master("Model"),
            _over(default_props(), wing_keys='DOME', wing_key_size=4.0, **wk_kw))
    dA_small = volume.mesh_volume(wks["parts"][0]) - vA0
    reset_scene()
    wkl = pipeline.build_mold_system(make_master("Model"),
            _over(default_props(), wing_keys='DOME', wing_key_size=12.0, **wk_kw))
    dA_large = volume.mesh_volume(wkl["parts"][0]) - vA0
    check(dA_large > dA_small * 1.5,
          f"Key Size scales the keys (+{dA_small:.1f} -> +{dA_large:.1f} u³)")

    # Key Height overrides the lip cap: taller keys add clearly more volume than auto
    # (auto tops out ~1.3 mm on the default 4 mm wing), and the halves stay valid even
    # though the deep socket punches through the wing lip.
    reset_scene()
    wkh = pipeline.build_mold_system(make_master("Model"),
            _over(default_props(), wing_keys='CONE', wing_key_height=5.0, **wk_kw))
    check(all(mfutil.part_is_valid(q)[0] for q in wkh["parts"]),
          "Key Height 5: halves valid (socket may pierce the lip)")
    dA_tall = volume.mesh_volume(wkh["parts"][0]) - vA0
    reset_scene()
    wka = pipeline.build_mold_system(make_master("Model"),
            _over(default_props(), wing_keys='CONE', wing_key_height=0.0, **wk_kw))
    dA_auto = volume.mesh_volume(wka["parts"][0]) - vA0
    check(dA_tall > dA_auto * 1.5,
          f"Key Height makes taller keys (+{dA_auto:.1f} auto -> +{dA_tall:.1f} u³ at 5 mm)")

    # ...and Key Spacing sets the pitch: a tighter pitch fits more keys along the seam.
    reset_scene()
    wkt = pipeline.build_mold_system(make_master("Model"),
            _over(default_props(), wing_keys='DOME', wing_key_spacing=8.0, **wk_kw))
    dA_tight = volume.mesh_volume(wkt["parts"][0]) - vA0
    reset_scene()
    wkw = pipeline.build_mold_system(make_master("Model"),
            _over(default_props(), wing_keys='DOME', wing_key_spacing=100.0, **wk_kw))
    dA_wide = volume.mesh_volume(wkw["parts"][0]) - vA0
    check(dA_tight > dA_wide * 1.8,
          f"Key Spacing adds keys at a tighter pitch (+{dA_wide:.1f} -> +{dA_tight:.1f} u³)")

    # --- Sprue is a raised pour funnel (stands proud of the mold) + stays valid
    print("\n=== Pour funnel ===")
    reset_scene()
    rf = pipeline.build_mold_system(make_master("Model"),
                                    _over(default_props(), sprue=True, wings=False))
    fmn, fmx = combined_bbox(rf["parts"])
    rf_valid = all(mfutil.part_is_valid(p)[0] for p in rf["parts"])  # before reset wipes them
    reset_scene()
    rn = pipeline.build_mold_system(make_master("Model"),
                                    _over(default_props(), sprue=False, wings=False))
    nmn, nmx = combined_bbox(rn["parts"])
    funnel_h = (fmx.z - fmn.z) - (nmx.z - nmn.z)
    check(funnel_h > 3.0, f"sprue adds a raised funnel (+{funnel_h:.1f} tall)")
    check(rf_valid, "funnel halves are valid solids")

    # --- Multiple pour points + centered sprue + bolt count still build valid
    print("\n=== Sprue options ===")
    for label, over in (("2 pour points", {"sprue_count": 2}),
                        ("centered sprue", {"sprue_place": 'XY'}),
                        ("explicit bolt count", {"wings": True, "bolt_auto": False, "bolt_count": 3})):
        reset_scene()
        try:
            ro = pipeline.build_mold_system(make_master("Model"),
                                            _over(default_props(), box_style='POUR_BOX', **over))
            check(all(mfutil.part_is_valid(p)[0] for p in ro["parts"]),
                  f"{label}: valid halves")
        except Exception as exc:
            check(False, f"{label}: raised {str(exc)[:40]}")

    # --- Sprue Placement modes: XY centres both axes, Highest follows the peak,
    # Center-X centres X while keeping the peak's Y (on a model whose peak leans).
    print("\n=== Sprue placement (XY / X / Highest) ===")
    from moldforge.core import sprue as mfsprue
    reset_scene()
    lean = make_master("Model")
    lean.data.transform(Matrix(((1, 0, 0.5, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1))))
    lmn, lmx = mfutil.world_bbox(lean)
    lcx, lcy = (lmn.x + lmx.x) * 0.5, (lmn.y + lmx.y) * 0.5

    def _primary(place):
        pr = mfsprue._pour_points(
            None, lean, types.SimpleNamespace(sprue_place=place, sprue_count=1))
        return pr[0][0]                      # (point, is_marker) tuples

    p_top, p_xy, p_x = _primary('TOP'), _primary('XY'), _primary('X')
    check(abs(p_xy.x - lcx) < 0.8 and abs(p_xy.y - lcy) < 0.8,
          f"XY sprue centres both axes (dx {p_xy.x - lcx:+.1f}, dy {p_xy.y - lcy:+.1f})")
    check(abs(p_top.x - lcx) > 2.0,
          f"Highest-point sprue follows the leaning peak (dx {p_top.x - lcx:+.1f})")
    check(abs(p_x.x - lcx) < 0.8 and abs(p_x.y - p_top.y) < 0.8,
          f"Center-X sprue centres X, keeps the peak's Y (dx {p_x.x - lcx:+.1f})")
    # Manual placement: the funnel lands at the typed X/Y offset from the centre.
    p_man = mfsprue._pour_points(None, lean, types.SimpleNamespace(
        sprue_place='MANUAL', sprue_count=1, sprue_x=4.0, sprue_y=-3.0))[0][0]
    check(abs(p_man.x - (lcx + 4.0)) < 1.0 and abs(p_man.y - (lcy - 3.0)) < 1.0,
          f"Manual sprue lands at the typed offset "
          f"(dx {p_man.x - lcx:+.1f}, dy {p_man.y - lcy:+.1f}, want +4.0,-3.0)")
    # A manual offset beyond the footprint is clamped back onto the model.
    p_clamp = mfsprue._pour_points(None, lean, types.SimpleNamespace(
        sprue_place='MANUAL', sprue_count=1, sprue_x=9999.0, sprue_y=0.0))[0][0]
    check(p_clamp.x <= lmx.x + 0.01,
          f"Manual sprue clamps to the footprint (x {p_clamp.x:.1f} <= {lmx.x:.1f})")

    # --- Oversized Mouth lifts the 0.45*half_min mouth cap (with a UI warning).
    print("\n=== Oversized Mouth override ===")
    reset_scene()
    moldbox = cube_master(40.0, "Box")           # a wide 'mold' to measure against
    masterS = make_master("Model")               # the model the funnel probes
    pt_top = mfutil.world_bbox(masterS)[1]
    f_off = mfsprue._funnel_at(moldbox, masterS, pt_top,
                               _over(default_props(), box_style='POUR_BOX',
                                     sprue_radius=4.0, sprue_flare=4.0, big_mouth=False))
    f_on = mfsprue._funnel_at(moldbox, masterS, pt_top,
                              _over(default_props(), box_style='POUR_BOX',
                                    sprue_radius=4.0, sprue_flare=4.0, big_mouth=True))
    check(f_off["mouth_r"] < 0.45 * 20.0 + 0.01,
          f"default mouth stays within the fit cap (r {f_off['mouth_r']:.1f} <= 9.0)")
    check(abs(f_on["mouth_r"] - 4.0 * 4.0) < 1e-6,
          f"Oversized Mouth is fully manual: exactly throat x flare "
          f"(r {f_on['mouth_r']:.1f} = 16.0, no cap)")

    # --- Oversized Throat lifts the 0.30*half_min throat cap (parity with Mouth).
    print("\n=== Oversized Throat override ===")
    reset_scene()
    moldbox2 = cube_master(40.0, "Box")          # half_min = 20 -> cap 6.0, big-cap 9.0
    masterT = make_master("Model")
    ptT = mfutil.world_bbox(masterT)[1]
    t_off = mfsprue._funnel_at(moldbox2, masterT, ptT,
                               _over(default_props(), box_style='POUR_BOX',
                                     sprue_radius=8.0, big_throat=False))
    t_on = mfsprue._funnel_at(moldbox2, masterT, ptT,
                              _over(default_props(), box_style='POUR_BOX',
                                    sprue_radius=8.0, big_throat=True))
    check(t_off["sprue_r"] < 0.30 * 20.0 + 0.01,
          f"default throat stays within the fit cap (r {t_off['sprue_r']:.1f} <= 6.0)")
    check(abs(t_on["sprue_r"] - 8.0) < 1e-6,
          f"Oversized Throat is fully manual: exactly the typed radius "
          f"(r {t_on['sprue_r']:.1f} = 8.0, no cap)")
    # Far past any old cap: 18 > 0.45 * 20 (the old Oversized ceiling) — still exact.
    t_huge = mfsprue._funnel_at(moldbox2, masterT, ptT,
                                _over(default_props(), box_style='POUR_BOX',
                                      sprue_radius=18.0, big_throat=True))
    check(abs(t_huge["sprue_r"] - 18.0) < 1e-6,
          f"manual throat has NO ceiling (r {t_huge['sprue_r']:.1f} = 18.0 on a "
          f"half-width-20 mold)")

    # --- Pre-build warnings surface BEFORE a (possibly long) build.
    print("\n=== Pre-build warnings ===")
    reset_scene()
    clean = make_master("Model")                 # closed, light icosphere
    check(pipeline.prebuild_warnings(clean, default_props()) == [],
          "clean light mesh: no pre-build warnings")
    openm = cube_master(20.0, "Open")            # delete a face -> non-manifold
    _bm = bmesh.new(); _bm.from_mesh(openm.data); _bm.faces.ensure_lookup_table()
    bmesh.ops.delete(_bm, geom=[_bm.faces[0]], context='FACES')
    _bm.to_mesh(openm.data); _bm.free(); openm.data.update()
    warns = pipeline.prebuild_warnings(openm, default_props())
    check(any("remesh" in w or "watertight" in w for w in warns),
          f"non-manifold mesh warns about remesh/smoothing ({warns})")
    check(pipeline.prebuild_warnings(openm, _over(default_props(), voxel_safe=True)) == [],
          "Safe Remesh on: no redundant non-manifold warning")

    # --- Staged build yields real per-phase progress, then a valid mold.
    print("\n=== Staged build progress ===")
    reset_scene()
    gen = pipeline.staged_build(make_master("Model"), default_props())
    fracs = []; labels_ok = True; result = None
    try:
        while True:
            frac, label = next(gen)
            fracs.append(frac)
            labels_ok = labels_ok and isinstance(label, str) and bool(label)
    except StopIteration as stop:
        result = stop.value
    check(len(fracs) >= 5, f"staged build reports several phases ({len(fracs)})")
    check(labels_ok and all(0.0 <= f <= 1.0 for f in fracs),
          "staged progress fractions in [0,1] with text labels")
    check(fracs == sorted(fracs),
          f"staged progress is non-decreasing ({[round(f, 2) for f in fracs]})")
    check(result is not None and all(mfutil.part_is_valid(p)[0] for p in result["parts"]),
          "staged build returns a valid finished mold")

    # --- Material density presets fill the density field (Custom leaves it alone).
    print("\n=== Material density presets ===")
    from moldforge import properties as mfprops
    sg = types.SimpleNamespace(silicone_preset='MOLDSTAR', silicone_density=0.0)
    mfprops._apply_silicone_preset(sg, None)
    check(abs(sg.silicone_density - mfprops._SILICONE_PRESETS['MOLDSTAR']) < 1e-6,
          f"Mold Star preset sets silicone density ({sg.silicone_density})")
    cg = types.SimpleNamespace(cast_preset='PLASTER', cast_density=0.0)
    mfprops._apply_cast_preset(cg, None)
    check(abs(cg.cast_density - mfprops._CAST_PRESETS['PLASTER']) < 1e-6,
          f"Plaster preset sets cast density ({cg.cast_density})")
    cu = types.SimpleNamespace(silicone_preset='CUSTOM', silicone_density=1.23)
    mfprops._apply_silicone_preset(cu, None)
    check(abs(cu.silicone_density - 1.23) < 1e-6,
          "Custom preset leaves the typed density untouched")

    # --- Recovery snapshot defaults derive from the PropertyGroup (one source).
    print("\n=== Derived property defaults ===")
    pipeline._PROP_DEFAULTS_CACHE = None          # force a fresh introspection
    defaults = pipeline._prop_defaults()
    check(len(defaults) > 20,
          f"defaults introspected from the PropertyGroup, not the fallback ({len(defaults)})")
    check(defaults.get("box_style") == 'POUR_BOX'
          and defaults.get("wall_thickness") == 3.0
          and defaults.get("parts_count") == 2,
          "derived defaults match the declared property defaults")

    # --- has_nonmanifold drives the MANIFOLD->EXACT boolean fallback.
    print("\n=== Non-manifold boolean fallback ===")
    reset_scene()
    closedc = cube_master(10.0, "Closed")
    check(not mfutil.has_nonmanifold(closedc), "closed cube reads as manifold")
    holed = cube_master(10.0, "Holed")
    _bm = bmesh.new(); _bm.from_mesh(holed.data); _bm.faces.ensure_lookup_table()
    bmesh.ops.delete(_bm, geom=[_bm.faces[0]], context='FACES')
    _bm.to_mesh(holed.data); _bm.free(); holed.data.update()
    check(mfutil.has_nonmanifold(holed), "cube with a deleted face reads as non-manifold")
    # A boolean against a non-manifold cutter must not silently no-op (the old bug):
    # the fallback routes it to EXACT, which completes and leaves a finite mesh.
    target = cube_master(10.0, "Target")
    holed.data.transform(Matrix.Translation(Vector((4.0, 0.0, 0.0))))   # partial overlap
    holed.data.update()
    try:
        mfutil.boolean(target, holed, 'DIFFERENCE')   # MANIFOLD requested -> EXACT used
        bool_ok = bool(target.data.polygons) and all(
            math.isfinite(c) for v in target.data.vertices for c in v.co)
    except Exception:
        bool_ok = False
    check(bool_ok, "boolean with a non-manifold cutter completes via the EXACT fallback")

    # --- Tray / open-pour mold (flat & relief objects): one open part, no split.
    print("\n=== Tray / open pour: modes ===")
    reset_scene()
    embed = pipeline.build_mold_system(
        relief_master(), _over(default_props(), box_style='TRAY', tray_mode='EMBED'))
    check(len(embed["parts"]) == 1 and mfutil.part_is_valid(embed["parts"][0])[0],
          "tray EMBED builds a single valid solid")
    check(embed["silicone_volume"] > 0.0 and embed["plastic_volume"] > 0.0,
          f"tray EMBED reports silicone to pour + pan plastic "
          f"({embed['silicone_volume']:.0f}, {embed['plastic_volume']:.0f})")

    reset_scene()
    frame = pipeline.build_mold_system(
        relief_master(), _over(default_props(), box_style='TRAY', tray_mode='FRAME'))
    check(mfutil.part_is_valid(frame["parts"][0])[0], "tray FRAME builds a valid solid")
    check(frame["plastic_volume"] < embed["plastic_volume"] - 1.0,
          f"tray FRAME pan embeds no object (plastic {frame['plastic_volume']:.0f} "
          f"< embed {embed['plastic_volume']:.0f})")

    pan = frame["parts"][0]
    pmn, pmx = mfutil.world_bbox(pan)
    want_x = 24.0 + 2.0 * (6.0 + 2.5)              # object + 2*(border + wall)
    check(abs((pmx.x - pmn.x) - want_x) < 0.6,
          f"tray footprint = object + border + wall ({pmx.x - pmn.x:.1f} vs {want_x:.1f})")
    # Open top: a ray straight down over the border ring drops to the floor, not a lid.
    inv = pan.matrix_world.inverted()
    dn = (inv.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized()
    hit, loc, _n, _i = pan.ray_cast(inv @ Vector((15.0, 0.0, 1e4)), dn)
    hz = (pan.matrix_world @ loc).z if hit else None
    check(hit and hz < pmn.z + 0.45 * (pmx.z - pmn.z),
          f"tray top is open — the ray drops to the floor ({hz})")

    # --- Visibility handoff: the ORIGINAL model hides after Generate, the
    # printable positive stays visible in its place.
    print("\n=== Visibility handoff ===")
    reset_scene()
    bmp = bmesh.new()
    bmesh.ops.create_cone(bmp, cap_ends=True, segments=48, radius1=16.0,
                          radius2=13.0, depth=56.0)
    mp_ = bpy.data.meshes.new("VisCone"); bmp.to_mesh(mp_); bmp.free()
    op_ = bpy.data.objects.new("VisCone", mp_)
    bpy.context.scene.collection.objects.link(op_)
    pr2 = pipeline.build_mold_system(op_, _over(default_props(),
            box_style='POUR_BOX', base_style='FLAT', wings=True,
            parts_count=2, contoured=False, split_axis='X',
            sprue_place='TOP', vent_count=0))
    check(all(mfutil.part_is_valid(q)[0] for q in pr2["parts"]),
          "winged halves valid (no pry pockets anymore)")
    src2 = bpy.data.objects.get("VisCone")
    check(src2 is not None and src2.hide_get(),
          "the original model is hidden after generation")
    check(not pr2["positive"].hide_get(),
          "the printable MF_Positive stays visible in its place")


    # --- Even soft wall on a CURVED sculpt: the whole point of the erosion.
    # A scaled copy hugs the inside of a bend and stands off the outside; a
    # true inward offset keeps the wall the same on both sides at every height.
    print("\n=== Dual density: even wall on a leaning model ===")
    reset_scene()
    bml = bmesh.new(); gridl = []
    for j in range(41):
        t = j / 40.0
        cxl = 30.0 * math.sin(t * math.pi * 0.5)        # leans 30 mm in +x
        rl = 16.0 - 4.0 * t
        gridl.append([bml.verts.new((cxl + rl * math.cos(2 * math.pi * i / 48),
                                     rl * math.sin(2 * math.pi * i / 48),
                                     90.0 * t)) for i in range(48)])
    for j in range(40):
        for i in range(48):
            k = (i + 1) % 48
            bml.faces.new((gridl[j][i], gridl[j][k], gridl[j + 1][k], gridl[j + 1][i]))
    bml.faces.new(list(reversed(gridl[0]))); bml.faces.new(gridl[40])
    bmesh.ops.recalc_face_normals(bml, faces=bml.faces[:])
    ml_ = bpy.data.meshes.new("Lean"); bml.to_mesh(ml_); bml.free()
    ol_ = bpy.data.objects.new("Lean", ml_)
    bpy.context.scene.collection.objects.link(ol_)
    WALL = 5.0
    lr = pipeline.build_mold_system(ol_, _over(default_props(),
            box_style='POUR_BOX', base_style='LOCK', wings=False, parts_count=2,
            split_axis='Y', sprue_place='TOP', lock_height=10.0, lock_margin=4.0,
            lock_teeth=3, lock_tooth_depth=2.0, lock_tolerance=0.2,
            dual_density=True, core_wall=WALL, vent_count=0))
    bpy.context.evaluated_depsgraph_get()
    lpos = lr["positive"]
    lcore = bpy.data.objects.get(lr["dual_core"])
    check(lcore is not None and mfutil.island_count(lcore) == 1
          and mfutil.nonmanifold_count(lcore) == 0,
          "lean: eroded core is one clean manifold solid")

    def _hx(obj, z, sgn):
        inv = obj.matrix_world.inverted()
        ok, loc, _n, _i = obj.ray_cast(
            inv @ Vector((sgn * 300.0, 0.0, z)),
            (inv.to_3x3() @ Vector((-sgn, 0.0, 0.0))).normalized())
        return (obj.matrix_world @ loc).x if ok else None
    lpn, lpx = mfutil.world_bbox(lpos)
    lcn, lcx = mfutil.world_bbox(lcore)
    base_l = lpn.z + 10.0

    def _band_cx(obj):
        # x-centre of the plinth / cross band below the base plane - the same
        # footprint on both objects, unlike a bbox centre skewed by the lean
        mw = obj.matrix_world
        xs = [(mw @ v.co).x for v in obj.data.vertices
              if (mw @ v.co).z < base_l - 0.5]
        return (min(xs) + max(xs)) * 0.5
    dxp = _band_cx(lcore) - _band_cx(lpos)       # the core is parked +x
    walls = []
    worst_diff, out_of_range = 0.0, False
    for frac in (0.25, 0.5, 0.75):
        z = base_l + 90.0 * frac
        pl, pr = _hx(lpos, z, -1.0), _hx(lpos, z, 1.0)
        cl, cr = _hx(lcore, z, -1.0), _hx(lcore, z, 1.0)
        if None in (pl, pr, cl, cr):
            out_of_range = True
            break
        outside = (cl - dxp) - pl            # wall on the outside of the bend
        inside = pr - (cr - dxp)             # wall on the inside of the bend
        walls.append((round(outside, 1), round(inside, 1)))
        worst_diff = max(worst_diff, abs(outside - inside))
        # a horizontal ray crosses a leaning wall obliquely: wall / cos(lean),
        # up to ~30 degrees here, so 5 mm reads as 5.0 .. 5.8 - never 3 or 8
        if not (WALL - 0.6 <= outside <= WALL * 1.32 + 0.3
                and WALL - 0.6 <= inside <= WALL * 1.32 + 0.3):
            out_of_range = True
    check(not out_of_range and worst_diff < 1.0,
          f"lean: the soft wall is EVEN on both sides of the bend at every "
          f"height - (outside, inside) per level {walls} for {WALL}; a scaled "
          f"core reads ~8 vs ~3 here (worst side difference {worst_diff:.2f})")
    check(abs((lpx.z - lcx.z) - WALL) < 1.2,
          f"lean: the cap on top is one wall ({lpx.z - lcx.z:.1f} for {WALL})")
    check(abs(lcn.z - lpn.z) < 0.3,
          "lean: the core still stands on the plinth bottom (cross welded on)")

    # --- The erosion on a BUMPY sculpt: the 0.40.0 regression. A textured
    # skin folds a Solidify offset at every crease tighter than the wall, and
    # the remesh that tidied the folds returned a lumpy, stair-stepped blob
    # with a shredded tip. Cut on a distance field the core is smooth, one
    # solid, and one wall inside the skin everywhere: on the tip, the flanks
    # and the bottom, bumps or no bumps.
    print("\n=== Dual density: erosion of a bumpy sculpt ===")
    reset_scene()
    bmb = bmesh.new(); gridb = []
    for j in range(61):
        t = j / 60.0
        z = 90.0 * t
        ring = []
        for i in range(96):
            a = 2 * math.pi * i / 96
            # 16 mm body, 1.5 mm bumps in a 12 x 9 pattern: creases every 8 mm
            rb = 16.0 - 3.0 * t + 1.5 * math.sin(12 * a) * math.sin(z * 0.63)
            ring.append(bmb.verts.new((rb * math.cos(a), rb * math.sin(a), z)))
        gridb.append(ring)
    for j in range(60):
        for i in range(96):
            k = (i + 1) % 96
            bmb.faces.new((gridb[j][i], gridb[j][k], gridb[j + 1][k], gridb[j + 1][i]))
    bmb.faces.new(list(reversed(gridb[0]))); bmb.faces.new(gridb[60])
    bmesh.ops.recalc_face_normals(bmb, faces=bmb.faces[:])
    mb_ = bpy.data.meshes.new("Bumpy"); bmb.to_mesh(mb_); bmb.free()
    ob_ = bpy.data.objects.new("Bumpy", mb_)
    bpy.context.scene.collection.objects.link(ob_)
    bcore = mfutil.duplicate_object(ob_, "BumpyCore", bpy.context.scene.collection)
    BW = 5.0
    from moldforge.core import build as mfbuild
    bvox = mfbuild.erode_solid(bcore, BW, bpy.context.scene.collection, 1.0)
    check(bool(bvox) and 0.3 <= bvox <= 1.0,
          f"bumpy: the erosion succeeds at a fine step ({bvox} mm for a {BW} wall)")
    check(mfutil.island_count(bcore) == 1 and mfutil.nonmanifold_count(bcore) == 0
          and len(bcore.data.polygons) > 2000,
          f"bumpy: the core is one clean manifold solid, not a blob "
          f"({mfutil.island_count(bcore)} islands, {len(bcore.data.polygons)} faces)")
    bmn, bmx = mfutil.world_bbox(ob_); cmn, cmx = mfutil.world_bbox(bcore)
    check(abs((bmx.z - cmx.z) - BW) < 0.6 and abs((cmn.z - bmn.z) - BW) < 0.6,
          f"bumpy: the tip and the bottom are eroded by exactly one wall "
          f"(top {bmx.z - cmx.z:.2f}, bottom {cmn.z - bmn.z:.2f} for {BW})")
    # a horizontal ray in: skin hit to core hit is the wall (never under it -
    # the core is a wall from EVERY skin point - and never far over it)
    bworst_lo, bworst_hi, bmiss = 99.0, 0.0, 0
    for zf in (0.2, 0.5, 0.8):
        zz = 90.0 * zf
        for i in range(24):
            a = 2 * math.pi * i / 24
            d = Vector((-math.cos(a), -math.sin(a), 0.0))
            o = Vector((300.0 * math.cos(a), 300.0 * math.sin(a), zz))
            h1, l1, _n, _i = ob_.ray_cast(o, d)
            h2, l2, _n, _i = bcore.ray_cast(o, d)
            if not (h1 and h2):
                bmiss += 1
                continue
            wdist = (l2 - l1).length
            bworst_lo, bworst_hi = min(bworst_lo, wdist), max(bworst_hi, wdist)
    check(bmiss == 0 and bworst_lo > BW - 0.7 and bworst_hi < BW + 3.5,
          f"bumpy: skin-to-core reads one wall all round at three heights "
          f"({bworst_lo:.2f} .. {bworst_hi:.2f} for {BW}, {bmiss} misses; the "
          f"folded offset read 0 at the shredded tip and 8+ in the lumps)")
    # smooth: the core's own bumps are much smaller than the skin's 1.5 mm
    # (a 5 mm ball erosion leaves ripples well under a millimetre)
    rc = []
    for i in range(96):
        a = 2 * math.pi * i / 96
        h2, l2, _n, _i = bcore.ray_cast(
            Vector((300.0 * math.cos(a), 300.0 * math.sin(a), 45.0)),
            Vector((-math.cos(a), -math.sin(a), 0.0)))
        if h2:
            rc.append(math.hypot(l2.x, l2.y))
    check(len(rc) > 90 and (max(rc) - min(rc)) < 2.0,
          f"bumpy: the core is smoother than the skin (radius spread "
          f"{max(rc) - min(rc):.2f} mm at mid height vs 3.0 on the skin)")

    # --- No sharp ridges on the core. An inward offset keeps every convex
    # crease of the skin as a sharp crease on the core (the scaled sculpt of
    # the user's screenshot: a core covered in blades, fragile to print and
    # a tear line in the silicone). The opening rounds them: the sharpest
    # face-to-face angle on the core's body reads under 30 degrees, where a
    # bare erosion read 70 on this skin and 43 on the scaled one.
    def _sharpest(obj, z_lo, z_hi):
        bmz = bmesh.new(); bmz.from_mesh(obj.data); bmz.normal_update()
        worst = 0.0
        for e in bmz.edges:
            if len(e.link_faces) != 2:
                continue
            zc = (e.verts[0].co.z + e.verts[1].co.z) * 0.5
            if z_lo < zc < z_hi:
                worst = max(worst, math.degrees(e.calc_face_angle(0.0)))
        bmz.free()
        return worst
    sharp_b = _sharpest(bcore, 8.0, 82.0)
    check(sharp_b < 30.0,
          f"bumpy: the core carries no sharp ridge (sharpest crease "
          f"{sharp_b:.1f} degrees; a bare erosion read ~70)")
    bms = bmesh.new(); grids = []
    for j in range(61):
        t = j / 60.0
        z = 90.0 * t
        ring = []
        for i in range(96):
            a = 2 * math.pi * i / 96
            # scale-like skin: sharp 2 mm crests every 30 degrees
            crest = 2.0 - 2.0 * abs(math.sin(6 * a)) ** 0.7
            rs_ = 16.0 - 3.0 * t + crest * (0.5 + 0.5 * math.sin(z * 0.63))
            ring.append(bms.verts.new((rs_ * math.cos(a), rs_ * math.sin(a), z)))
        grids.append(ring)
    for j in range(60):
        for i in range(96):
            k = (i + 1) % 96
            bms.faces.new((grids[j][i], grids[j][k], grids[j + 1][k], grids[j + 1][i]))
    bms.faces.new(list(reversed(grids[0]))); bms.faces.new(grids[60])
    bmesh.ops.recalc_face_normals(bms, faces=bms.faces[:])
    ms_ = bpy.data.meshes.new("Scaled"); bms.to_mesh(ms_); bms.free()
    os_ = bpy.data.objects.new("Scaled", ms_)
    bpy.context.scene.collection.objects.link(os_)
    scaled_skin = _sharpest(os_, 8.0, 82.0)
    score = mfutil.duplicate_object(os_, "ScaledCore", bpy.context.scene.collection)
    svox = mfbuild.erode_solid(score, BW, bpy.context.scene.collection, 1.0)
    sharp_s = _sharpest(score, 8.0, 82.0)
    check(bool(svox) and scaled_skin > 80.0 and sharp_s < 30.0
          and mfutil.island_count(score) == 1 and mfutil.nonmanifold_count(score) == 0,
          f"scaled skin: crests of {scaled_skin:.0f} degrees on the skin, the "
          f"core's sharpest crease is {sharp_s:.1f} (a bare erosion read ~43)")
    smn, smx = mfutil.world_bbox(os_); scn, scx = mfutil.world_bbox(score)
    s_lo, s_miss = 99.0, 0
    for zf in (0.2, 0.5, 0.8):
        zz = 90.0 * zf
        for i in range(48):
            a = 2 * math.pi * i / 48
            d = Vector((-math.cos(a), -math.sin(a), 0.0))
            o = Vector((300.0 * math.cos(a), 300.0 * math.sin(a), zz))
            h1, l1, _n, _i = os_.ray_cast(o, d)
            h2, l2, _n, _i = score.ray_cast(o, d)
            if not (h1 and h2):
                s_miss += 1
                continue
            s_lo = min(s_lo, (l2 - l1).length)
    check(s_miss == 0 and s_lo > BW - 0.7 and abs((smx.z - scx.z) - BW) < 0.6,
          f"scaled skin: rounding never brings the core closer than the wall "
          f"(thinnest {s_lo:.2f} for {BW}, cap {smx.z - scx.z:.2f})")

    # --- Contoured parting on a LEANING model must be one continuous surface:
    # outside the silhouette (below the base, beyond the sides) the old cutter
    # snapped to the bbox centre, leaving a ~4 mm ledge along the whole seam at
    # the base plane and a ~15 mm one along the silhouette edge.
    print("\n=== Contoured parting: continuous on a leaning model ===")
    reset_scene()
    bmq = bmesh.new(); gridq = []
    for j in range(41):
        t = j / 40.0
        cxq = 30.0 * math.sin(t * math.pi * 0.5)
        rq = 16.0 - 4.0 * t
        gridq.append([bmq.verts.new((cxq + rq * math.cos(2 * math.pi * i / 48),
                                     rq * math.sin(2 * math.pi * i / 48),
                                     90.0 * t)) for i in range(48)])
    for j in range(40):
        for i in range(48):
            k = (i + 1) % 48
            bmq.faces.new((gridq[j][i], gridq[j][k], gridq[j + 1][k], gridq[j + 1][i]))
    bmq.faces.new(list(reversed(gridq[0]))); bmq.faces.new(gridq[40])
    bmesh.ops.recalc_face_normals(bmq, faces=bmq.faces[:])
    mq_ = bpy.data.meshes.new("LeanCut"); bmq.to_mesh(mq_); bmq.free()
    oq_ = bpy.data.objects.new("LeanCut", mq_)
    bpy.context.scene.collection.objects.link(oq_)
    qmn, qmx = mfutil.world_bbox(oq_)
    cutq = split._contoured_cutter(oq_, 'X', qmn - Vector((5, 5, 10)),
                                   qmx + Vector((5, 5, 14)),
                                   mfutil.ensure_collection(), 0.0)
    invq = cutq.matrix_world.inverted()

    def _H(y, z):
        ok, loc, _n, _i = cutq.ray_cast(
            invq @ Vector((500.0, y, z)),
            (invq.to_3x3() @ Vector((-1.0, 0.0, 0.0))).normalized())
        return (cutq.matrix_world @ loc).x if ok else None
    below, above = _H(0.0, -0.5), _H(0.0, 0.5)
    check(below is not None and above is not None and abs(below - above) < 0.8,
          f"parting surface does not step at the base plane "
          f"({below:.2f} below vs {above:.2f} above; old cutter: ~4 mm)")
    inside, outside = _H(0.0, 80.0), _H(25.0, 80.0)
    check(inside is not None and outside is not None
          and abs(inside - outside) < 0.35 * abs(inside) + 1.5,
          f"parting surface does not step at the silhouette edge "
          f"({inside:.2f} inside vs {outside:.2f} outside; old cutter: ~15 mm)")
    check(above is not None and abs(above) < 3.0,
          f"parting surface passes through the base ring's centre "
          f"({above:.2f}, ring centre 0)")
    mfutil.remove_object(cutq)

    # --- Vac-U-Lock plug: the bundled former on a socket cross.
    print("\n=== Vac-U-Lock plug ===")
    reset_scene()
    bmc = bmesh.new()
    bmesh.ops.create_cone(bmc, cap_ends=True, segments=48, radius1=26.0,
                          radius2=20.0, depth=120.0)
    mc_ = bpy.data.meshes.new("PlugCone"); bmc.to_mesh(mc_); bmc.free()
    oc_ = bpy.data.objects.new("PlugCone", mc_)
    bpy.context.scene.collection.objects.link(oc_)
    ap = pipeline.build_mold_system(oc_, _over(default_props(),
            box_style='POUR_BOX', base_style='LOCK', wings=False, parts_count=2,
            split_axis='X', sprue_place='XY', lock_height=10.0, lock_margin=4.0,
            lock_teeth=3, lock_tooth_depth=2.0, lock_tolerance=0.2,
            anchor_plug=True, vent_count=0))
    bpy.context.evaluated_depsgraph_get()
    plugs = [q for q in ap["parts"] if q.name == "MF_Mold_Plug"]
    check(len(plugs) == 1 and mfutil.part_is_valid(plugs[0])[0],
          "Vac-U-Lock plug printed part exists and is valid")
    apo = plugs[0]
    apn, apx = mfutil.world_bbox(apo)
    ppn, ppx = mfutil.world_bbox(ap["positive"])
    check(abs(apn.z - ppn.z) < 0.3
          and abs((apx.x - apn.x) - (ppx.x - ppn.x)) < 1.0,
          f"plug's cross spans the positive's plinth footprint - same socket "
          f"({apx.x - apn.x:.1f} vs {ppx.x - ppn.x:.1f}, bottoms "
          f"{apn.z:.1f}/{ppn.z:.1f})")
    base_z2 = ppn.z + 10.0                        # plinth top = base plane
    check(apx.z > base_z2 + 30.0,
          f"ribbed plug stands into the cavity (top {apx.z:.1f} vs base "
          f"{base_z2:.1f})")
    check(not ap.get("plug_skipped"),
          "plug not skipped on a mold big enough for the real size")
    pox, poy = ap.get("plug_offset", (0.0, 0.0))
    check(abs(pox) < 1.5 and abs(poy) < 1.5,
          f"on a symmetric toy the plug stays at the base centre (offset "
          f"{pox:+.1f}, {poy:+.1f})")


    def _w_plug(z):
        lo = hi = None
        inv = apo.matrix_world.inverted()
        for sgn in (1.0, -1.0):
            hit, loc, _n, _i = apo.ray_cast(
                inv @ Vector((sgn * 200.0, 0.0, z)),
                (inv.to_3x3() @ Vector((-sgn, 0.0, 0.0))).normalized())
            if hit:
                w = (apo.matrix_world @ loc).x
                if sgn > 0:
                    hi = w
                else:
                    lo = w
        return (hi - lo) if (hi is not None and lo is not None) else None
    ln2 = apx.z - base_z2
    w_rib = _w_plug(base_z2 + ln2 * 0.78)
    w_waist = _w_plug(base_z2 + ln2 * 0.97)
    check(w_rib is not None and w_waist is not None and w_rib > w_waist + 4.0,
          f"plug is ribbed - a grip tier is fatter than the dome tip "
          f"({w_rib:.1f} vs {w_waist:.1f})")
    # The bundled REAL mesh carries the suction BELL at its base - far wider
    # than any plug tier. The parametric fallback has no bell, so this proves
    # the user's combined mesh is what actually ships.
    w_cup = _w_plug(base_z2 + ln2 * 0.02)
    check(w_cup is not None and w_cup > w_rib + 10.0,
          f"plug is the bundled VuL+Succ mesh: suction bell at the base "
          f"({w_cup:.1f} vs tier {w_rib:.1f})")
    # 91.83 native, sunk 0.5 into the cross, which itself tops out on the
    # plinth's 2 mm weld band above the base plane (tooth depth in this test)
    check(abs(ln2 - (91.83 - 0.5 + 2.0)) < 1.5,
          f"plug is ALWAYS the original authored size - never rescaled "
          f"({ln2:.1f} vs 93.3)")
    # The base is a CROSS, not the old full slab: the diagonal corner of the
    # plinth region must be empty below the sealing disc.
    corner = 0
    mwp = apo.matrix_world
    acx, acy = (apn.x + apx.x) * 0.5, (apn.y + apx.y) * 0.5  # parked aside
    for v in apo.data.vertices:
        w = mwp @ v.co
        if (w.z < base_z2 - 0.6
                and abs(w.x - acx) > 0.62 * (apx.x - apn.x) * 0.5
                and abs(w.y - acy) > 0.62 * (apx.y - apn.y) * 0.5):
            corner += 1
    check(corner == 0,
          f"plug base is a bare + cross (no disc): plinth corners carved "
          f"away ({corner} stray verts)")
    reset_scene()
    bms = bmesh.new()
    bmesh.ops.create_cone(bms, cap_ends=True, segments=48, radius1=13.0,
                          radius2=10.0, depth=50.0)
    ms_ = bpy.data.meshes.new("TinyPlug"); bms.to_mesh(ms_); bms.free()
    os_ = bpy.data.objects.new("TinyPlug", ms_)
    bpy.context.scene.collection.objects.link(os_)
    aps = pipeline.build_mold_system(os_, _over(default_props(),
            box_style='POUR_BOX', base_style='LOCK', wings=False, parts_count=2,
            split_axis='X', sprue_place='XY', lock_height=10.0, lock_margin=4.0,
            lock_teeth=3, lock_tooth_depth=2.0, lock_tolerance=0.2,
            anchor_plug=True, vent_count=0))
    check(aps.get("plug_skipped")
          and not any(q.name == "MF_Mold_Plug" for q in aps["parts"])
          and all(mfutil.part_is_valid(q)[0] for q in aps["parts"]),
          "a mold too small for the real plug skips it with a note instead "
          "of shrinking it into a useless size")

    # --- Dual density: Core_Master rides a socket CROSS carved from the same
    # sawtooth plinth (sealing disc + bars with the teeth as lips), so the
    # firm core cast from it clamps into the shells' grooves when they bolt
    # shut - held, centred, poured around (the real 2-pour process).

    # --- A leaning toy: the base centre is a thin valley for a vertical 92 mm
    # column (it breaks out through the side higher up), so the plug must slide
    # along the base cross to where the toy stays solid for its full height.
    print("\n=== Vac-U-Lock plug: placed where the toy is thickest ===")
    reset_scene()
    bml = bmesh.new()
    rows_l, segs_l = [], 64
    for j in range(41):
        t = j / 40.0
        cxl = 26.0 * math.sin(t * math.pi * 0.5)        # leans 26 mm to +x
        # an elliptical section, 72 x 54: wide enough for the 55 mm bell
        rows_l.append([bml.verts.new((cxl + 36.0 * math.cos(2 * math.pi * i / segs_l),
                                      27.0 * math.sin(2 * math.pi * i / segs_l),
                                      140.0 * t)) for i in range(segs_l)])
    for j in range(40):
        for i in range(segs_l):
            k = (i + 1) % segs_l
            bml.faces.new((rows_l[j][i], rows_l[j][k], rows_l[j + 1][k], rows_l[j + 1][i]))
    bml.faces.new(list(reversed(rows_l[0])))
    bml.faces.new(rows_l[40])
    bmesh.ops.recalc_face_normals(bml, faces=bml.faces[:])
    ml_ = bpy.data.meshes.new("LeanPlug"); bml.to_mesh(ml_); bml.free()
    ol_ = bpy.data.objects.new("LeanPlug", ml_)
    bpy.context.scene.collection.objects.link(ol_)
    lp = pipeline.build_mold_system(ol_, _over(default_props(),
            box_style='POUR_BOX', base_style='LOCK', wings=False, parts_count=2,
            split_axis='Y', sprue_place='TOP', lock_height=10.0, lock_margin=4.0,
            lock_teeth=3, lock_tooth_depth=2.0, lock_tolerance=0.2,
            anchor_plug=True, vent_count=0))
    bpy.context.evaluated_depsgraph_get()
    lplugs = [q for q in lp["parts"] if q.name == "MF_Mold_Plug"]
    check(len(lplugs) == 1 and not lp.get("plug_skipped"),
          f"leaning toy still gets its plug ({lp.get('plug_note', '')!r})")
    if lplugs:
        from moldforge.core import build as mfbuild2
        lpos = lp["positive"]
        lmn, _lmx = mfutil.world_bbox(lpos)
        lbase = lmn.z + 10.0
        # the column's axis: the plug's vertices well above the cross
        lpl = lplugs[0]
        mwp = lpl.matrix_world
        colv = [mwp @ v.co for v in lpl.data.vertices if (mwp @ v.co).z > lbase + 25.0]
        pcx = sum(v.x for v in colv) / len(colv)
        pcy = sum(v.y for v in colv) / len(colv)
        # the plug was parked -x of the shells: measure the offset it recorded
        lox, loy = lp.get("plug_offset", (0.0, 0.0))
        check(5.0 < lox < 15.0 and abs(loy) < 1.5,
              f"plug slid along the cross toward the lean (offset {lox:+.1f}, {loy:+.1f})")
        # where it now stands, the toy surrounds the column for its full height.
        # The offset is from the PLINTH's centre (the positive's lowest band),
        # not the whole positive's box centre, which the lean drags sideways.
        mwl = lpos.matrix_world
        band_l = [mwl @ v.co for v in lpos.data.vertices if (mwl @ v.co).z < lbase - 1.0]
        cxp_pos = (min(v.x for v in band_l) + max(v.x for v in band_l)) * 0.5
        cyp_pos = (min(v.y for v in band_l) + max(v.y for v in band_l)) * 0.5
        r_col = mfbuild2._VAC_STL_NATIVE_D * 0.5
        at_spot = mfbuild2.plug_clearance(lpos, cxp_pos + lox, cyp_pos + loy,
                                          lbase + 2.0, lbase + 89.0)
        at_centre = mfbuild2.plug_clearance(lpos, cxp_pos, cyp_pos, lbase + 2.0, lbase + 89.0)
        check(at_spot is not None and at_spot - r_col >= mfbuild2.PLUG_MIN_WALL - 0.3,
              f"column stays inside the toy with wall to spare "
              f"({(at_spot or 0) - r_col:.1f} mm of toy around it)")
        check(at_centre is not None and at_centre - r_col < mfbuild2.PLUG_MIN_WALL,
              f"the old base-centre spot would have broken out "
              f"({(at_centre or 0) - r_col:.1f} mm)")
        check(mfutil.part_is_valid(lpl)[0], "off-centre plug part is valid")

    print("\n=== Dual density (2-pour, Locking Base) ===")
    reset_scene()
    bmd = bmesh.new()
    bmesh.ops.create_cone(bmd, cap_ends=True, segments=48, radius1=15.0,
                          radius2=11.0, depth=44.0)
    md_ = bpy.data.meshes.new("DualLk"); bmd.to_mesh(md_); bmd.free()
    od_ = bpy.data.objects.new("DualLk", md_)
    bpy.context.scene.collection.objects.link(od_)
    dd = pipeline.build_mold_system(od_, _over(default_props(),
            box_style='POUR_BOX', base_style='LOCK', wings=False, parts_count=2,
            split_axis='X', sprue_place='XY', lock_height=10.0, lock_margin=4.0,
            lock_teeth=3, lock_tooth_depth=2.0, lock_tolerance=0.2,
            dual_density=True, core_wall=4.5, vent_count=0))
    cmo = bpy.data.objects.get(dd.get("dual_core") or "")
    check(cmo is not None and not cmo.name.startswith("MF_"),
          "dual: Core_Master created without the MF_ prefix (re-moldable)")
    check(not any(q.name == "MF_Mold_Stem" for q in dd["parts"]),
          "dual: no hanger stem anymore (socket registration instead)")
    pos2 = dd["positive"]
    pmn3, pmx3 = mfutil.world_bbox(pos2)
    cmn3, cmx3 = mfutil.world_bbox(cmo)
    # Same plinth: identical bottom z and identical full-size base footprint.
    check(abs(cmn3.z - pmn3.z) < 0.3,
          f"dual: core bottom = positive bottom (plinth attached; "
          f"{cmn3.z:.1f} vs {pmn3.z:.1f})")
    check(abs((cmx3.x - cmn3.x) - (pmx3.x - pmn3.x)) < 1.0,
          f"dual: core base footprint matches the positive's plinth - it fits "
          f"the SAME socket ({cmx3.x - cmn3.x:.1f} vs {pmx3.x - pmn3.x:.1f})")
    # The BODY above the plinth is shrunk to 70%: width at 60% model height.
    def _w_at(obj, z):
        lo = hi = None
        inv = obj.matrix_world.inverted()
        for sgn in (1.0, -1.0):
            hit, loc, _n, _i = obj.ray_cast(
                inv @ Vector((sgn * 200.0, 0.0, z)),
                (inv.to_3x3() @ Vector((-sgn, 0.0, 0.0))).normalized())
            if hit:
                w = (obj.matrix_world @ loc).x
                if sgn > 0:
                    hi = w
                else:
                    lo = w
        return (hi - lo) if (hi is not None and lo is not None) else None
    zq = pmn3.z + 10.0 + (pmx3.z - pmn3.z - 10.0) * 0.5
    wp = _w_at(pos2, zq)
    wc = _w_at(cmo, zq)
    # A true inward offset: at mid height the core is exactly 2 x Soft Wall
    # narrower than the model, and the cap above it is one Soft Wall thick.
    check(wp is not None and wc is not None and abs((wp - wc) - 2.0 * 4.5) < 1.5,
          f"dual: core is the model eroded by the soft wall on each flank "
          f"(width {wc:.1f} vs positive {wp:.1f}, wall {(wp - wc) / 2:.2f} "
          f"for 4.5)")
    top_gap = pmx3.z - cmx3.z
    check(abs(top_gap - 4.5) < 1.2,
          f"dual: the cap above the core is one soft wall ({top_gap:.1f} for 4.5)")
    # The base is the socket CROSS, not the old full slab: the plinth
    # region's diagonal corners must be carved away below the sealing disc,
    # while the disc keeps the full footprint (checked above) so nothing can
    # leak past it and the lips still ring the whole outline's grooves.
    corner2 = 0
    mwc = cmo.matrix_world
    dcx, dcy = (cmn3.x + cmx3.x) * 0.5, (cmn3.y + cmx3.y) * 0.5  # parked aside
    for v in cmo.data.vertices:
        w = mwc @ v.co
        if (w.z < pmn3.z + 10.0 - 0.6
                and abs(w.x - dcx) > 0.62 * (cmx3.x - cmn3.x) * 0.5
                and abs(w.y - dcy) > 0.62 * (cmx3.y - cmn3.y) * 0.5):
            corner2 += 1
    check(corner2 == 0,
          f"dual: core rides a bare + cross (no disc) - plinth corners "
          f"carved away ({corner2} stray verts)")
    check(not any(q.name == "MF_Mold_Cross" for q in dd["parts"]),
          "dual: no separate stand-on plate anymore (the cross is part of "
          "the core, clamped by the bolted shells)")
    check(not any(q.name == "MF_Mold_Cork" for q in dd["parts"]),
          "dual: no cork part (removed per user - the trimmed silicone sprue "
          "stub plugs the channel)")
    folds_d = {q.name: len(mfutil.folded_faces(q)) for q in list(dd["parts"]) + [cmo]}
    check(not any(folds_d.values()),
          f"dual: no shell or core face folds back on itself ({folds_d})")
    dd2 = pipeline.build_mold_system(cmo, _over(default_props(),
            box_style='POUR_BOX', base_style='FLAT', wings=False, parts_count=2,
            contoured=False, split_axis='X', sprue_place='XY', vent_count=0))
    kept = [o4 for o4 in mfutil.ensure_collection().objects
            if o4.name.startswith("Kept_")]
    check(all(mfutil.part_is_valid(q)[0] for q in dd2["parts"])
          and len(kept) >= 3 and dd2.get("kept_prev", 0) >= 3,
          f"dual second run: core mold valid, first mold kept as Kept_* "
          f"({len(kept)} kept)")

    # --- Stamp from SVG / Text: the artwork is engraved into the pan floor;
    # pour silicone, peel, and the slab is a real ink stamp (raised + mirrored).
    print("\n=== Stamp maker (SVG / Text) ===")

    def _stamp_props(**over):
        p = _over(default_props(), box_style='TRAY', tray_mode='STAMP',
                  tray_wall=2.5, tray_floor=3.0, tray_margin=6.0, tray_depth=5.0,
                  stamp_svg="", stamp_width=60.0, stamp_relief=2.0,
                  stamp_mirror=False)
        return _over(p, **over)

    def _deep_counts(pan):
        """Grid rays from above: (deep_left, deep_right, total_hits) where deep
        means the ray fell below the floor-top plane into an engraved glyph."""
        bpy.context.evaluated_depsgraph_get()
        mn, mx = mfutil.world_bbox(pan)
        cx = (mn.x + mx.x) * 0.5
        inv = pan.matrix_world.inverted()
        dl, dr, tot = 0, 0, 0
        for i in range(30):
            for j in range(12):
                x = mn.x + (mx.x - mn.x) * (i + 0.5) / 30
                y = mn.y + (mx.y - mn.y) * (j + 0.5) / 12
                hit, loc, _n, _i = pan.ray_cast(
                    inv @ Vector((x, y, mx.z + 10.0)),
                    (inv.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized())
                if hit:
                    tot += 1
                    if (pan.matrix_world @ loc).z < -0.5:
                        if x < cx:
                            dl += 1
                        else:
                            dr += 1
        return dl, dr, tot

    reset_scene()
    tcv = bpy.data.curves.new("Txt", type='FONT')
    tcv.body = "AB"
    tob = bpy.data.objects.new("Txt", tcv)
    bpy.context.scene.collection.objects.link(tob)
    st = pipeline.build_mold_system(tob, _stamp_props())
    span = st["parts"][0]
    check(mfutil.part_is_valid(span)[0] and st["cavity_volume"] > 500.0,
          f"text stamp: valid engraved pan (recess {st['cavity_volume']:.0f} u³)")
    d_l, d_r, d_tot = _deep_counts(span)
    check(d_l + d_r > 20,
          f"glyphs really recessed below the floor ({d_l + d_r} of {d_tot} "
          f"grid rays fall in)")
    check(st["silicone_volume"] > st["cavity_volume"],
          "stamp silicone estimate covers slab + recess")

    # Mirror Design flips the engraving left-right (asymmetric text "L.").
    def _l_counts(mirror):
        reset_scene()
        cv = bpy.data.curves.new("L", type='FONT')
        cv.body = "L."
        ob = bpy.data.objects.new("L", cv)
        bpy.context.scene.collection.objects.link(ob)
        r = pipeline.build_mold_system(ob, _stamp_props(stamp_mirror=mirror))
        return _deep_counts(r["parts"][0])
    n_l, n_r, _t = _l_counts(False)
    m_l, m_r, _t = _l_counts(True)
    check(n_l > n_r and m_r > m_l,
          f"Mirror Design flips the engraving (normal {n_l}/{n_r} -> "
          f"mirrored {m_l}/{m_r})")

    # The user's imported-SVG case: the importer leaves a -90 deg X rotation
    # and a wild non-uniform scale on the object. The design must be flattened
    # into the pan plane automatically, not engraved standing on its edge.
    reset_scene()
    rc = bpy.data.curves.new("Rot", type='FONT')
    rc.body = "AB"
    ro = bpy.data.objects.new("Rot", rc)
    bpy.context.scene.collection.objects.link(ro)
    ro.rotation_euler = (math.radians(-90.0), 0.0, 0.0)
    ro.scale = (1.0, 850.0, 0.9)
    bpy.context.view_layer.update()
    rs = pipeline.build_mold_system(ro, _stamp_props())
    rpan = rs["parts"][0]
    check(mfutil.part_is_valid(rpan)[0] and rs["cavity_volume"] > 500.0,
          f"rotated/scaled import: still a valid engraved pan "
          f"(recess {rs['cavity_volume']:.0f} u³)")
    rl, rr2, rtot = _deep_counts(rpan)
    check(rl + rr2 > 20,
          f"rotated import: glyphs land flat in the floor ({rl + rr2} of "
          f"{rtot} rays fall in)")

    # SVG source (filled rect + circle), no object selected.
    reset_scene()
    svg_path = os.path.join("/tmp", "mf_out", "stamp_test.svg")
    os.makedirs(os.path.dirname(svg_path), exist_ok=True)
    with open(svg_path, "w") as fh:
        fh.write('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60">'
                 '<rect x="10" y="10" width="40" height="30" fill="#000"/>'
                 '<circle cx="75" cy="30" r="15" fill="#000"/></svg>')
    sv = pipeline.build_mold_system(None, _stamp_props(stamp_svg=svg_path))
    check(mfutil.part_is_valid(sv["parts"][0])[0] and sv["cavity_volume"] > 800.0,
          f"SVG stamp: valid engraved pan (recess {sv['cavity_volume']:.0f} u³)")
    smn, smx = mfutil.world_bbox(sv["parts"][0])
    check(abs((smx.x - smn.x) - (60.0 + 2 * 6.0 + 2 * 2.5)) < 1.5,
          f"design scaled to Stamp Width (pan {smx.x - smn.x:.1f} wide for a "
          f"60 mm design + border + walls)")

    print("\n=== Tray / open pour: auto-orientation ===")
    reset_scene()
    down = pipeline.build_mold_system(
        relief_master(rot=Matrix.Rotation(math.radians(180), 4, 'X')),
        _over(default_props(), box_style='TRAY', tray_mode='EMBED'))
    reset_scene()
    sideways = pipeline.build_mold_system(
        relief_master(rot=Matrix.Rotation(math.radians(90), 4, 'Y')),
        _over(default_props(), box_style='TRAY', tray_mode='EMBED'))
    tol = 0.02 * embed["silicone_volume"] + 1.0
    check(abs(down["silicone_volume"] - embed["silicone_volume"]) < tol,
          f"tray auto-flips a detail-down object upright "
          f"({down['silicone_volume']:.0f} vs {embed['silicone_volume']:.0f})")
    check(abs(sideways["silicone_volume"] - embed["silicone_volume"]) < tol,
          f"tray auto-lays-down a standing object "
          f"({sideways['silicone_volume']:.0f} vs {embed['silicone_volume']:.0f})")

    print("\n=== Tray / open pour: not-flat warning ===")
    reset_scene()
    flat_warn = pipeline.prebuild_warnings(relief_master(),
                                           _over(default_props(), box_style='TRAY'))
    chunky_warn = pipeline.prebuild_warnings(cube_master(20.0),
                                             _over(default_props(), box_style='TRAY'))
    check(not any("isn't flat" in w for w in flat_warn),
          f"flat object: no not-flat tray warning ({flat_warn})")
    check(any("isn't flat" in w for w in chunky_warn),
          f"chunky object warns it isn't flat for a tray ({chunky_warn})")

    print("\n=== Tray / open pour: hug outline ===")

    def _flat_disc(name="Disc", r=10.0, h=3.0):
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, segments=48,
                              radius1=r, radius2=r, depth=h)
        me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
        o = bpy.data.objects.new(name, me)
        bpy.context.scene.collection.objects.link(o)
        return o

    reset_scene()
    rect_t = pipeline.build_mold_system(
        _flat_disc(), _over(default_props(), box_style='TRAY', tray_outline='RECT'))
    reset_scene()
    hug_t = pipeline.build_mold_system(
        _flat_disc(), _over(default_props(), box_style='TRAY', tray_outline='HUG'))
    check(len(hug_t["parts"]) == 1 and mfutil.part_is_valid(hug_t["parts"][0])[0],
          "tray HUG builds a single valid solid")
    # A round disc in a hugging (round) pan needs less silicone and plastic than the
    # square rectangular pan.
    check(hug_t["silicone_volume"] < rect_t["silicone_volume"] * 0.9,
          f"tray HUG uses less silicone than RECT for a round object "
          f"({hug_t['silicone_volume']:.0f} < {rect_t['silicone_volume']:.0f})")
    check(hug_t["plastic_volume"] < rect_t["plastic_volume"] * 0.95,
          f"tray HUG uses less pan plastic than RECT "
          f"({hug_t['plastic_volume']:.0f} < {rect_t['plastic_volume']:.0f})")

    # --- Tier 3: glove / mother mold (thin silicone skin + rigid 2-part shell)
    print("\n=== Glove / mother mold ===")
    reset_scene()
    glove = pipeline.build_mold_system(make_master("Model"),
                                       _over(default_props(), box_style='POUR_BOX', skin_keys=True,
                                             shell_wall=4.0))
    check(all(mfutil.part_is_valid(p)[0] for p in glove["parts"]),
          "glove mold: valid rigid-shell halves")
    check(glove["plastic_volume"] > 0.0,
          f"glove mold: rigid shell has plastic ({glove['plastic_volume']:.0f})")
    check(glove.get("skin") is not None, "glove mold: produced an MF_Skin preview solid")
    if glove.get("skin") is not None:
        skin_vol = volume.mesh_volume(glove["skin"])
        check(abs(skin_vol - glove["silicone_volume"]) < 0.05 * skin_vol + 1.0,
              f"glove mold: skin solid matches reported silicone ({skin_vol:.0f} vs "
              f"{glove['silicone_volume']:.0f})")
    # The headline: a thin skin uses far less silicone than a fat pour gap.
    reset_scene()
    fatpour = pipeline.build_mold_system(make_master("Model"),
                                         _over(default_props(), box_style='POUR_BOX',
                                               wall_thickness=12.0))
    check(glove["silicone_volume"] < 0.55 * fatpour["silicone_volume"],
          f"glove skin uses far less silicone than a thick pour ("
          f"{glove['silicone_volume']:.0f} vs {fatpour['silicone_volume']:.0f})")

    # --- Solid molds keep an OPEN casting cavity (regression: a shard-repair
    # remesh used to fill the solidify void, leaving solid lumps with no cavity)
    # A direct (SOLID) printed mold's cavity IS the cast impression, so it must be
    # cleaned at a fine, model-detail voxel - not the coarse wall-keyed voxel a
    # pour-box jacket can use (the bug that made the impression low-poly).
    print("\n=== Direct mold cavity voxel (fine, wall-independent) ===")
    reset_scene()
    _m = make_master("Model")
    sv4 = pipeline._derive_sizes(_m, _over(default_props(), box_style='SOLID', wall_thickness=4.0))
    sv10 = pipeline._derive_sizes(_m, _over(default_props(), box_style='SOLID', wall_thickness=10.0))
    jv = pipeline._derive_sizes(_m, _over(default_props(), box_style='POUR_BOX',
                                          wall_thickness=4.0, shell_wall=2.0))
    check(sv4.detail_voxel < 0.6,
          f"direct mold remeshes fine enough to keep the impression ({sv4.detail_voxel:.2f} mm)")
    check(abs(sv10.detail_voxel - sv4.detail_voxel) < 1e-6,
          f"direct cavity voxel keyed to model detail, not wall "
          f"({sv4.detail_voxel:.2f} vs wall=10 {sv10.detail_voxel:.2f})")
    check(sv4.detail_voxel < jv.detail_voxel,
          f"direct cavity finer than the pour-box jacket's "
          f"({sv4.detail_voxel:.2f} < {jv.detail_voxel:.2f})")

    print("\n=== Solid molds keep an open cavity ===")
    reset_scene()
    ra = pipeline.build_mold_system(make_master("Model"),
                                    _over(default_props(), box_style='SOLID',
                                          base_style='FOLLOW', sprue=False, vent_count=0,
                                          key_count=0, contoured=False, split_axis='X'))
    mmn, mmx = mfutil.world_bbox(ra["positive"])
    xs = x_crossings(ra["parts"][0], (mmn.z + mmx.z) * 0.5)   # +X half, mid height
    pretty = [f"{x:.1f}" for x in xs]
    check(len(xs) >= 2, f"adaptive half: outer wall + cavity wall crossed ({pretty})")
    check(all(abs(x) > 0.5 for x in xs),
          f"adaptive cavity NOT sealed at the parting plane ({pretty})")
    check(any(8.5 < x < 11.5 for x in xs),
          f"adaptive cavity wall sits at the model surface ({pretty})")
    check(ra["silicone_volume"] < 1.6 * ra["cavity_volume"],
          f"adaptive halves are hollow shells, not solid lumps "
          f"({ra['silicone_volume']:.0f} u³ for a {ra['cavity_volume']:.0f} u³ model)")
    reset_scene()
    rb = pipeline.build_mold_system(make_master("Model"),
                                    _over(default_props(), box_style='SOLID', solid_shape='BLOCK',
                                          base_style='FOLLOW', sprue=False, vent_count=0,
                                          key_count=0, split_axis='X'))
    xs = x_crossings(rb["parts"][0], 0.0)
    check(any(8.5 < x < 11.5 for x in xs) and all(abs(x) > 0.5 for x in xs),
          f"block cavity open at the parting plane ({[f'{x:.1f}' for x in xs]})")

    # The funnel must bore THROUGH the shell into the casting cavity (not end as
    # a blind plug): a ray straight down the throat must first hit a surface deep
    # inside the cavity, well below the model's top.
    print("\n=== Funnel bores into the cavity (direct molds) ===")
    for shape in ('HUG', 'BLOCK'):
        reset_scene()
        rfb = pipeline.build_mold_system(make_master("Model"),
                                         _over(default_props(), box_style='SOLID',
                                               solid_shape=shape, base_style='FOLLOW',
                                               sprue=True, sprue_place='XY',
                                               vent_count=0, key_count=0,
                                               contoured=False, split_axis='X'))
        mmn, mmx = mfutil.world_bbox(rfb["positive"])
        cx, cy = (mmn.x + mmx.x) * 0.5 + 1.0, (mmn.y + mmx.y) * 0.5
        top = max(mfutil.world_bbox(p)[1].z for p in rfb["parts"])
        first = None
        for p in rfb["parts"]:
            mw = p.matrix_world
            inv = mw.inverted()
            d = (inv.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized()
            hit, loc, _n, _i = p.ray_cast(inv @ Vector((cx, cy, top + 5.0)), d)
            if hit:
                z = (mw @ loc).z
                first = z if first is None else max(first, z)
        check(first is not None and first < mmx.z - 1.0,
              f"{shape}: funnel bore open into cavity (first surface z={first if first is None else round(first, 1)} "
              f"vs model top {mmx.z:.1f})")

    # --- Glove mold must actually differ from a pour box: registration bumps on
    # the skin, matching pockets in the jacket, and a visible skin object.
    print("\n=== Glove mold differs from pour box (skin keys) ===")
    reset_scene()
    pp = pipeline.build_mold_system(make_master("Model"),
                                    _over(default_props(), box_style='POUR_BOX'))
    pour_plastic, pour_sil = pp["plastic_volume"], pp["silicone_volume"]
    reset_scene()
    gm = pipeline.build_mold_system(make_master("Model"),
                                    _over(default_props(), box_style='POUR_BOX',
                                          skin_keys=True))
    check(gm["plastic_volume"] < pour_plastic - 1.0,
          f"jacket carries skin-key pockets ({gm['plastic_volume']:.0f} < {pour_plastic:.0f})")
    check(gm["silicone_volume"] > pour_sil + 1.0,
          f"skin carries raised keys ({gm['silicone_volume']:.0f} > {pour_sil:.0f})")
    sk = gm.get("skin")
    visible = sk is not None
    if visible:
        try:
            visible = not sk.hide_get()
        except Exception:
            pass
    check(visible, "skin preview exists and is visible")

    # --- Block radial wedges must register: wings are impossible there, so the
    # seam pins have to appear even though the wings *setting* is on.
    print("\n=== Block radial wedges get seam pins ===")
    reset_scene()
    b0 = pipeline.build_mold_system(make_master("Model"),
                                    _over(default_props(), box_style='SOLID', solid_shape='BLOCK',
                                          parts_count=3, wings=True, key_count=0))
    b0_faces = [part_face_count(p) for p in b0["parts"]]
    reset_scene()
    b2 = pipeline.build_mold_system(make_master("Model"),
                                    _over(default_props(), box_style='SOLID', solid_shape='BLOCK',
                                          parts_count=3, wings=True, key_count=2))
    check(all(mfutil.part_is_valid(p)[0] for p in b2["parts"]),
          "block radial + pins: valid wedges")
    # Per-wedge volume nearly cancels (ridge on one seam, socket on the other),
    # so count faces instead — both the ridge and the groove ADD geometry.
    df = sum(part_face_count(a) for a in b2["parts"]) - sum(b0_faces)
    check(df > 40, f"seam pins present on block radial wedges (+{df} faces)")

    # --- Detachable base plate: separate keyed bottom (model pocket + shell pins)
    print("\n=== Detachable base plate ===")

    def plate_top_z(plate, x, y):
        mw = plate.matrix_world
        inv = mw.inverted()
        d = (inv.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized()
        top = mfutil.world_bbox(plate)[1].z
        hit, loc, _n, _i = plate.ray_cast(inv @ Vector((x, y, top + 50.0)), d)
        return (mw @ loc).z if hit else None

    reset_scene()
    rp = pipeline.build_mold_system(make_master("Model"),
                                    _over(default_props(), box_style='POUR_BOX',
                                          base_style='OPEN', base_plate=True, wings=True))
    parts = rp["parts"]
    check(len(parts) == 3, f"plate base: 2 shell halves + 1 plate ({len(parts)} parts)")
    check(all(mfutil.part_is_valid(p)[0] for p in parts), "plate base: all parts valid")
    plate = next((p for p in parts if "Base" in p.name), None)
    check(plate is not None, "plate part is named MF_Mold_Base")
    if plate is not None:
        pmn, pmx = mfutil.world_bbox(plate)
        mmn, mmx = mfutil.world_bbox(rp["positive"])
        cx, cy = (mmn.x + mmx.x) * 0.5, (mmn.y + mmx.y) * 0.5
        t_center = plate_top_z(plate, cx, cy)             # pocket floor
        # Plain plate top: just outside the model footprint (the contoured plate
        # reaches here; the bbox corner would be off the contoured plate).
        t_edge = plate_top_z(plate, mmx.x + 1.0, cy)
        check(t_center is not None and t_edge is not None
              and t_edge - t_center > 0.5,
              f"plate carries the model registration pocket "
              f"(depth {0.0 if None in (t_center, t_edge) else t_edge - t_center:.2f})")
        # Chin collar with a groove along its crest (sampled radially outward
        # from just past the model's footprint to the plate's edge), and the
        # matching ring tongue hanging below the shell's rim.
        cut_guess = mmn.z + 1.2          # pocket depth at the default 3.0 gap
        crest = gmin = None
        for i in range(80):
            x = mmx.x + 0.5 + (pmx.x - mmx.x - 0.5) * i / 79.0
            z = plate_top_z(plate, x, cy)
            if z is None:
                continue
            crest = z if crest is None else max(crest, z)
            gmin = z if gmin is None else min(gmin, z)
        check(crest is not None and crest > cut_guess + 1.2,
              f"chin collar rises from the plate (+{0.0 if crest is None else crest - cut_guess:.2f})")
        check(gmin is not None and gmin < cut_guess - 0.2,
              f"groove sunk through the chin crest ({0.0 if gmin is None else gmin - cut_guess:+.2f})")
        shells = [q for q in parts if "Base" not in q.name]
        smin = min(mfutil.world_bbox(q)[0].z for q in shells)
        check(smin < cut_guess + 0.1,
              f"ring tongue hangs below the shell rim ({smin - cut_guess:+.2f} vs plate top)")
    # The keyed plate also works under a radial 3-part split and a solid mold.
    reset_scene()
    rp3 = pipeline.build_mold_system(make_master("Model"),
                                     _over(default_props(), box_style='POUR_BOX',
                                           base_style='OPEN', base_plate=True, wings=True, parts_count=3))
    check(len(rp3["parts"]) == 4 and all(mfutil.part_is_valid(p)[0] for p in rp3["parts"]),
          "plate base + 3 radial wedges: 4 valid parts")
    reset_scene()
    rpa = pipeline.build_mold_system(make_master("Model"),
                                     _over(default_props(), box_style='SOLID',
                                           base_style='OPEN', base_plate=True))
    check(len(rpa["parts"]) == 3 and all(mfutil.part_is_valid(p)[0] for p in rpa["parts"]),
          "plate base on adaptive mold: valid")

    # Fit Clearance opens the groove + pocket so the printed tongue/model actually
    # fit: a bigger clearance removes more plate material (wider groove, looser
    # pocket), so the plate volume drops.
    def plate_vol(fc):
        reset_scene()
        r = pipeline.build_mold_system(make_master("Model"),
                                       _over(default_props(), box_style='POUR_BOX',
                                             base_style='OPEN', base_plate=True,
                                             wings=False, sprue=False, fit_clearance=fc))
        pl = next(p for p in r["parts"] if "Base" in p.name)
        return volume.mesh_volume(pl)
    v_tight, v_loose = plate_vol(0.1), plate_vol(0.6)
    check(v_loose < v_tight - 1.0,
          f"more fit clearance loosens the groove/pocket ({v_loose:.0f} < {v_tight:.0f})")

    # --- Screw the master to the key plate (user request: no more gluing). A
    # hole through the plate, countersunk from below so the plate sits flat, and
    # a heat-set insert hole up into the master's underside, on one axis.
    print("\n=== Key plate: screw the master to the plate ===")

    def _flat_toy(name, r0=20.0, r1=14.0, h=60.0, seg=64, bore=0.0, bore_h=0.0):
        """A flat-based toy: a flared foot, a tapered column, a domed top.
        ``bore`` > 0 sinks a blind hole of that radius up the middle of the
        underside (a toy whose base centre is hollow)."""
        bmq = bmesh.new(); rings_q = []
        rows = 40
        for j in range(rows + 1):
            t = j / rows
            r = r0 + (r1 - r0) * t
            if t < 0.12:
                r = r0 * (1.15 - 1.25 * t)
            if t > 0.85:
                r = r1 * math.sqrt(max(1.0 - ((t - 0.85) / 0.15) ** 2, 0.04))
            rings_q.append([bmq.verts.new((r * math.cos(2 * math.pi * i / seg),
                                           r * math.sin(2 * math.pi * i / seg), h * t))
                            for i in range(seg)])
        for j in range(rows):
            for i in range(seg):
                k = (i + 1) % seg
                bmq.faces.new((rings_q[j][i], rings_q[j][k], rings_q[j + 1][k],
                               rings_q[j + 1][i]))
        bmq.faces.new(list(reversed(rings_q[0]))); bmq.faces.new(rings_q[rows])
        bmesh.ops.recalc_face_normals(bmq, faces=bmq.faces[:])
        mq = bpy.data.meshes.new(name); bmq.to_mesh(mq); bmq.free()
        oq = bpy.data.objects.new(name, mq)
        bpy.context.scene.collection.objects.link(oq)
        if bore > 0.0:
            cut = bpy.data.objects.new("bore", bpy.data.meshes.new("bore"))
            bpy.context.scene.collection.objects.link(cut)
            bmc = bmesh.new()
            bmesh.ops.create_cone(bmc, cap_ends=True, segments=48, radius1=bore,
                                  radius2=bore, depth=bore_h + 2.0,
                                  matrix=Matrix.Translation((0.0, 0.0, bore_h * 0.5 - 1.0)))
            bmc.to_mesh(cut.data); bmc.free()
            mfutil.boolean(oq, cut, 'DIFFERENCE')
            mfutil.remove_object(cut)
        return oq

    def _up(obj, x, y, below):
        """World heights where a ray going up at world (x, y) crosses ``obj``."""
        mw = obj.matrix_world
        inv = mw.inverted()
        d = (inv.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
        out = []
        o = inv @ Vector((x, y, below))
        for _ in range(16):
            hit, loc, _n, _i = obj.ray_cast(o, d)
            if not hit:
                break
            out.append((mw @ loc).z)
            o = loc + d * 1e-3
        return out

    screw_cfg = dict(box_style='POUR_BOX', base_style='OPEN', base_plate=True,
                     wings=False, sprue=False, vent_count=0, parts_count=2,
                     split_axis='Y', wall_thickness=4.0, plate_screw=True,
                     screw_diameter=3.4, screw_countersink=True,
                     screw_head_diameter=6.5, insert_diameter=4.0, insert_depth=6.5)
    reset_scene()
    rs_ = pipeline.build_mold_system(_flat_toy("ScrewToy"),
                                     _over(default_props(), **screw_cfg))
    bpy.context.view_layer.update()        # parts are moved home: world space
    check(all(mfutil.part_is_valid(q)[0] for q in rs_["parts"])
          and mfutil.part_is_valid(rs_["positive"])[0],
          "screwed plate: shells, plate and positive all valid")
    check(rs_.get("screw_ok") and "middle of the base" in rs_.get("screw_note", ""),
          f"the screw goes in the middle of the base ({rs_.get('screw_note')!r})")
    splate = next(q for q in rs_["parts"] if q.name.startswith("MF_Mold_Base"))
    spos = rs_["positive"]
    sx, sy = (rs_["screw_spot"][0] + spos.location.x,      # build space -> world
              rs_["screw_spot"][1] + spos.location.y)
    s_pmn = mfutil.world_bbox(splate)[0]
    s_bot = mfutil.world_bbox(spos)[0].z
    check(not [z for z in _up(splate, sx, sy, s_pmn.z - 5.0) if z < s_bot - 0.1],
          "the screw hole runs clean through the plate")
    cone_z = _up(splate, sx + 2.3, sy, s_pmn.z - 5.0)
    rim_z_ = _up(splate, sx + 4.5, sy, s_pmn.z - 5.0)
    check(cone_z and rim_z_ and cone_z[0] - s_pmn.z > 0.5 and rim_z_[0] - s_pmn.z < 0.05,
          f"the underside is countersunk: 2.3 mm off the axis the plate starts "
          f"{(cone_z or [0])[0] - s_pmn.z:.2f} up the cone, 4.5 mm off at its bottom face")
    floor = (rim_z_[1] if len(rim_z_) > 1 else s_pmn.z) - s_pmn.z
    check(floor >= (6.5 - 3.4) * 0.5 + 1.4,
          f"the plate is thickened under the master to hold the countersink "
          f"({floor:.2f} mm of floor for a {(6.5 - 3.4) * 0.5:.2f} mm cone)")
    ins_top = _up(spos, sx, sy, s_bot - 5.0)
    check(ins_top and abs(ins_top[0] - (s_bot + 6.5)) < 0.3,
          f"a 6.5 mm deep insert hole goes up into the master's underside "
          f"(first surface {(ins_top or [0])[0] - s_bot:.2f} above its bottom)")
    wide_ = _up(spos, sx + 2.4, sy, s_bot - 5.0)
    check(wide_ and wide_[0] - s_bot < 0.3,
          "the insert hole is its 4.0 mm diameter, no wider")
    # Plain hole: countersink off.
    reset_scene()
    rs2_ = pipeline.build_mold_system(_flat_toy("ScrewToy"),
                                      _over(default_props(), **dict(
                                          screw_cfg, screw_countersink=False)))
    bpy.context.view_layer.update()
    splate2 = next(q for q in rs2_["parts"] if q.name.startswith("MF_Mold_Base"))
    sx2 = rs2_["screw_spot"][0] + splate2.location.x
    sy2 = rs2_["screw_spot"][1] + splate2.location.y
    s2mn = mfutil.world_bbox(splate2)[0]
    plain = _up(splate2, sx2 + 2.3, sy2, s2mn.z - 5.0)
    check(mfutil.part_is_valid(splate2)[0] and plain and plain[0] - s2mn.z < 0.05,
          "Countersink off leaves a plain straight hole")
    # A toy whose base is hollow in the middle: the screw moves to the nearest
    # spot with 2 mm of master round the insert, instead of drilling into air.
    reset_scene()
    rs3_ = pipeline.build_mold_system(_flat_toy("HollowBase", bore=6.0, bore_h=20.0),
                                      _over(default_props(), **screw_cfg))
    off3 = math.hypot(*rs3_["screw_spot"]) if rs3_.get("screw_spot") else 0.0
    check(rs3_.get("screw_ok") and off3 > 6.0 + 2.0 + 1.9
          and "off the base centre" in rs3_.get("screw_note", ""),
          f"a hollow base centre moves the screw out to solid master "
          f"({off3:.1f} mm off; {rs3_.get('screw_note')!r})")
    check(all(mfutil.part_is_valid(q)[0] for q in rs3_["parts"]),
          "hollow-base screw build: all parts valid")
    # No room at all: a slim toy skips the screw with a reason, never fails.
    reset_scene()
    rs4_ = pipeline.build_mold_system(_flat_toy("Slim", r0=3.2, r1=2.6, h=30.0),
                                      _over(default_props(), **screw_cfg))
    check(rs4_.get("screw_ok") is False and "glue" in rs4_.get("screw_note", "")
          and all(mfutil.part_is_valid(q)[0] for q in rs4_["parts"]),
          f"a base too slim for the insert skips the screw with a reason "
          f"({rs4_.get('screw_note')!r})")

    # --- The base formers on an OPEN bottom (user request): the suction cup,
    # the Vac-U-Lock plug and the dual core hang from four legs resting on the
    # shells' bottom rim, with or without the key plate.
    print("\n=== Open bottom: suction cup, plug and dual core on legs ===")
    open_cfg2 = dict(box_style='POUR_BOX', base_style='OPEN', wings=True,
                     sprue_place='TOP', vent_count=0, parts_count=2, split_axis='Y',
                     wall_thickness=4.0, bolt_auto=False, bolt_count=0)
    for with_plate in (False, True):
        tag = "plate" if with_plate else "no plate"
        reset_scene()
        rf = pipeline.build_mold_system(
            _flat_toy("BigToy", r0=36.0, r1=24.0, h=125.0),
            _over(default_props(), **open_cfg2, base_plate=with_plate,
                  suction_cup=True, anchor_plug=True, cup_depth=8.0))
        bpy.context.view_layer.update()    # parts are moved home: world space
        names_f = sorted(q.name for q in rf["parts"])
        check(all(mfutil.part_is_valid(q)[0] for q in rf["parts"]),
              f"{tag}: shells, cup former and plug all one valid solid each ({names_f})")
        fcup = next((q for q in rf["parts"] if q.name.startswith("MF_Mold_Cup")), None)
        fplug = next((q for q in rf["parts"] if q.name.startswith("MF_Mold_Plug")), None)
        check(fcup is not None and fplug is not None,
              f"{tag}: the suction-cup former and the Vac-U-Lock plug are both built")
        shells_f = [q for q in rf["parts"] if not q.name.startswith(
            ("MF_Mold_Cup", "MF_Mold_Plug", "MF_Mold_Base"))]
        rim_f = min(mfutil.world_bbox(q)[0].z for q in shells_f)
        if fcup is not None:
            cmn_f, cmx_f = mfutil.world_bbox(fcup)
            fplate = next((q for q in rf["parts"] if q.name.startswith("MF_Mold_Base")),
                          None)
            # the plate top is stored in build space; the parts now sit at home
            open_f = (fplate["mf_plate_top"] + fplate.location.z
                      if fplate is not None else rim_f)
            check(abs(cmn_f.z - (rim_f - 3.0)) < 0.05 and abs(cmx_f.z - (open_f + 8.0)) < 0.1,
                  f"{tag}: the cup's legs sit under the rim and its dome rises 8 mm "
                  f"from the opening ({cmn_f.z - rim_f:+.2f}, {cmx_f.z - open_f:.2f})")
        if fplug is not None:
            pmn_f, pmx_f = mfutil.world_bbox(fplug)
            check(abs(pmn_f.z - (rim_f - 3.0)) < 0.05 and pmx_f.z - rim_f > 90.0,
                  f"{tag}: the plug's legs rest on the rim and the full 92 mm plug "
                  f"stands up into the cavity ({pmx_f.z - rim_f:.1f})")
            # The column's axis is under the dome's apex; 12 mm out from it along
            # x runs between two diagonal legs, under the bell's hollow.
            apex = max((fplug.matrix_world @ v.co for v in fplug.data.vertices),
                       key=lambda w: w.z)
            ups = _up(fplug, apex.x + 12.0, apex.y, pmn_f.z - 5.0)
            open_under = (ups[0] - pmn_f.z) if ups else 0.0
            check(open_under > 5.0 and len(ups) >= 2,
                  f"{tag}: the bell's hollow underside stays open between the legs, "
                  f"no sealed resin trap (first surface {open_under:.1f} up)")
        check("base centre" in (rf.get("plug_note") or ""),
              f"{tag}: the plug note says where it went ({rf.get('plug_note')!r})")
        # Coplanar leg/hook/ring faces once came out of the fast boolean as
        # zero-area slits whose triangles fold back: black patches in a render,
        # inverted facets in the STL.
        folds_f = {q.name: len(mfutil.folded_faces(q)) for q in rf["parts"]}
        check(not any(folds_f.values()),
              f"{tag}: no part has a face whose triangles fold back ({folds_f})")

    for with_plate in (False, True):
        tag = "plate" if with_plate else "no plate"
        reset_scene()
        rd = pipeline.build_mold_system(
            _flat_toy("CoreToy", r0=22.0, r1=16.0, h=70.0),
            _over(default_props(), **open_cfg2, base_plate=with_plate,
                  dual_density=True, core_wall=5.0))
        bpy.context.view_layer.update()
        dcore = bpy.data.objects.get(rd.get("dual_core") or "")
        check(dcore is not None and mfutil.part_is_valid(dcore)[0],
              f"{tag}: Core_Master is one valid solid, legs and all")
        if dcore is not None:
            shells_d = [q for q in rd["parts"] if not q.name.startswith("MF_Mold_Base")]
            rim_d = min(mfutil.world_bbox(q)[0].z for q in shells_d)
            dmn_, dmx_ = mfutil.world_bbox(dcore)
            pmx_d = mfutil.world_bbox(rd["positive"])[1].z
            check(abs(dmn_.z - (rim_d - 3.0)) < 0.05 and abs((pmx_d - dmx_.z) - 5.0) < 0.6,
                  f"{tag}: the core hangs from legs on the rim and sits one Soft "
                  f"Wall under the tip ({dmn_.z - rim_d:+.2f}, cap {pmx_d - dmx_.z:.2f})")
            span_d = min(dmx_.x - dmn_.x, dmx_.y - dmn_.y)
            smn_d, smx_d = combined_bbox(shells_d)
            check(span_d > min(smx_d.x - smn_d.x, smx_d.y - smn_d.y) * 0.7,
                  f"{tag}: the legs reach out to the shells' wall ({span_d:.0f} mm across)")
            check(not mfutil.folded_faces(dcore),
                  f"{tag}: the core's legs join it with no folded faces")
    # The core's own mold (second run, Bottom: Flat) molds it, legs and all.
    if dcore is not None:
        rd2 = pipeline.build_mold_system(dcore, _over(default_props(),
                box_style='POUR_BOX', base_style='FLAT', wings=False, parts_count=2,
                contoured=False, split_axis='X', sprue_place='XY', vent_count=0))
        check(all(mfutil.part_is_valid(q)[0] for q in rd2["parts"])
              and rd2.get("kept_prev", 0) >= 2,
              "the legged Core_Master molds in its own second run")

    # --- From a user review (0.41.0): the positive clipped into the base part,
    # and the silicone preview hung below the case. Both also skewed numbers.
    print("\n=== Review: skin preview and base parts fit the real kit ===")

    def _overlap(a, b):
        """Volume of a ∩ b (world space)."""
        ta = mfutil.duplicate_object(a, "MF_ovA", bpy.context.scene.collection)
        tb = mfutil.duplicate_object(b, "MF_ovB", bpy.context.scene.collection)
        mfutil.boolean(ta, tb, 'INTERSECT', solver='EXACT')
        v = volume.mesh_volume(ta) if ta.data.polygons else 0.0
        mfutil.remove_object(ta); mfutil.remove_object(tb)
        return v

    rev_cfg = dict(box_style='POUR_BOX', wings=True, parts_count=2, split_axis='Y',
                   sprue_place='TOP', vent_count=0, wall_thickness=4.0,
                   bolt_auto=False, bolt_count=0)
    reset_scene()
    rr1 = pipeline.build_mold_system(_flat_toy("RevToy", r0=24.0, r1=16.0, h=80.0),
                                     _over(default_props(), **rev_cfg, base_style='OPEN'))
    bpy.context.view_layer.update()
    sk1 = rr1.get("skin")
    sh1 = [q for q in rr1["parts"] if q.name.startswith("MF_Mold_")]
    bot1 = min(mfutil.world_bbox(q)[0].z for q in sh1)
    check(sk1 is not None and mfutil.world_bbox(sk1)[0].z > bot1 - 0.05,
          f"open bottom: the silicone preview ends at the shells' bottom, nothing "
          f"hangs below the case ({mfutil.world_bbox(sk1)[0].z - bot1:+.2f})")
    check(sk1 is not None and abs(rr1["silicone_volume"] - volume.mesh_volume(sk1)) < 1.0,
          "open bottom: the silicone to mix is measured from that same preview")

    reset_scene()
    rr2 = pipeline.build_mold_system(_flat_toy("RevToy", r0=24.0, r1=16.0, h=80.0),
                                     _over(default_props(), **rev_cfg, base_style='OPEN',
                                           base_plate=True))
    bpy.context.view_layer.update()
    pl2 = next(q for q in rr2["parts"] if q.name.startswith("MF_Mold_Base"))
    top2 = pl2["mf_plate_top"] + pl2.location.z
    sk2 = rr2.get("skin")
    check(sk2 is not None and mfutil.world_bbox(sk2)[0].z > top2 - 0.05,
          f"key plate: the silicone preview ends at the plate's top "
          f"({mfutil.world_bbox(sk2)[0].z - top2:+.2f})")

    reset_scene()
    rr3 = pipeline.build_mold_system(_flat_toy("RevToy", r0=24.0, r1=16.0, h=80.0),
                                     _over(default_props(), **rev_cfg, base_style='LOCK'))
    bpy.context.view_layer.update()
    sk3 = rr3.get("skin")
    in3 = _overlap(sk3, rr3["positive"]) if sk3 is not None else -1.0
    check(sk3 is not None and 0.0 <= in3 < 0.005 * volume.mesh_volume(sk3),
          f"locking base: the silicone preview stays out of the plinth "
          f"({in3 / 1000:.2f} ml inside it; was ~9.5 ml on this toy)")

    reset_scene()
    rr4 = pipeline.build_mold_system(_flat_toy("RevToy", r0=24.0, r1=16.0, h=80.0),
                                     _over(default_props(), **rev_cfg, base_style='LOCK',
                                           lock_unite=False))
    bpy.context.view_layer.update()
    b4 = next(q for q in rr4["parts"] if q.name.startswith("MF_Mold_Base"))
    in4 = _overlap(rr4["positive"], b4)
    p4mn = mfutil.world_bbox(rr4["positive"])[0]
    check(mfutil.part_is_valid(b4)[0] and in4 < 1.0,
          f"separate locking base: the master no longer sits inside it "
          f"({in4:.1f} mm3 overlap; was a 2 mm layer, ~4600 mm3)")
    check(mfutil.world_bbox(b4)[1].z > p4mn.z + 1.0,
          "separate locking base: its top cradles the master's underside")

    # A model that is not watertight is built from a coarse remeshed copy; the
    # key plate's pocket was cut from that copy and the printed master poked in.
    reset_scene()
    leaky = _flat_toy("Leaky", r0=24.0, r1=16.0, h=80.0)
    bml = bmesh.new(); bml.from_mesh(leaky.data); bml.faces.ensure_lookup_table()
    bmesh.ops.delete(bml, geom=[bml.faces[len(bml.faces) // 2]], context='FACES')
    bml.to_mesh(leaky.data); bml.free()
    rr5 = pipeline.build_mold_system(leaky, _over(default_props(), **rev_cfg,
                                                  base_style='OPEN', base_plate=True))
    bpy.context.view_layer.update()
    pl5 = next(q for q in rr5["parts"] if q.name.startswith("MF_Mold_Base"))
    in5 = _overlap(rr5["positive"], pl5)
    check(mfutil.part_is_valid(pl5)[0] and in5 < 1.0,
          f"key plate on a non-watertight model: the pocket follows the detailed "
          f"master ({in5:.1f} mm3 overlap)")

    # --- From the same review: "clamp wings disappearing in places". The wings
    # were cut from a flat slab on the bounding-box centre plane while the
    # contoured split follows the model's mid-depth: on a model leaning ALONG the
    # split axis each height's wing landed wholly in one half.
    print("\n=== Review: clamp wings follow the contoured parting ===")

    def _lean_y(name, lean=20.0):
        bmw = bmesh.new(); rw = []
        for j in range(41):
            t = j / 40.0
            r = 16.0 - 4.0 * t
            if t > 0.88:
                r = 12.0 * math.sqrt(max(1.0 - ((t - 0.88) / 0.12) ** 2, 0.04))
            cy = lean * math.sin(t * math.pi * 0.5)
            rw.append([bmw.verts.new((r * math.cos(2 * math.pi * i / 64),
                                      cy + r * math.sin(2 * math.pi * i / 64), 100.0 * t))
                       for i in range(64)])
        for j in range(40):
            for i in range(64):
                k = (i + 1) % 64
                bmw.faces.new((rw[j][i], rw[j][k], rw[j + 1][k], rw[j + 1][i]))
        bmw.faces.new(list(reversed(rw[0]))); bmw.faces.new(rw[40])
        bmesh.ops.recalc_face_normals(bmw, faces=bmw.faces[:])
        mw_ = bpy.data.meshes.new(name); bmw.to_mesh(mw_); bmw.free()
        ow_ = bpy.data.objects.new(name, mw_)
        bpy.context.scene.collection.objects.link(ow_)
        return ow_

    lean_cfg = dict(box_style='POUR_BOX', base_style='FLAT', wings=True, parts_count=2,
                    split_axis='Y', sprue_place='TOP', vent_count=0, wall_thickness=4.0,
                    shell_wall=2.0, bolt_auto=False, bolt_count=0, contoured=True)

    def _past(objs, model, z, sgn):
        """How far each object reaches past the model along X at height z (a
        fine sweep across Y, so a 2 mm half-wing is never stepped over)."""
        pmn_, pmx_ = mfutil.world_bbox(model)

        def far(o):
            inv = o.matrix_world.inverted()
            best = None
            for k in range(361):
                y = pmn_.y - 20.0 + (pmx_.y - pmn_.y + 40.0) * k / 360.0
                hit, loc, _n, _i = o.ray_cast(
                    inv @ Vector(((pmn_.x + pmx_.x) * 0.5 + sgn * 300.0, y, z)),
                    (inv.to_3x3() @ Vector((-sgn, 0.0, 0.0))).normalized())
                if hit:
                    v = (o.matrix_world @ loc).x * sgn
                    best = v if best is None else max(best, v)
            return best
        m_ = far(model)
        return [None if far(o) is None else far(o) - m_ for o in objs]

    reset_scene()
    rw1 = pipeline.build_mold_system(_lean_y("LeanY"), _over(default_props(), **lean_cfg))
    bpy.context.view_layer.update()
    hw1 = [q for q in rw1["parts"] if q.name in ("MF_Mold_A", "MF_Mold_B")]
    pmn_w, pmx_w = mfutil.world_bbox(rw1["positive"])
    worst = 99.0
    for frac in (0.15, 0.4, 0.65, 0.85):
        z = pmn_w.z + (pmx_w.z - pmn_w.z) * frac
        for sgn in (1.0, -1.0):
            for v in _past(hw1, rw1["positive"], z, sgn):
                worst = min(worst, -1.0 if v is None else v)
    check(worst > 4.0 + 2.0 + 8.0 * 0.5,
          f"leaning along the split axis: BOTH halves carry a wing at every height "
          f"(least reach past the model {worst:.1f}; about 14 with a wing, 6 without)")
    check(not rw1.get("wing_notes"),
          f"... and the wing check finds nothing to report ({rw1.get('wing_notes')})")
    # The check itself: build the wings the old way (flat slab, contoured split)
    # and the shortfall must be reported.
    reset_scene()
    from moldforge.core import split as mfsplit
    real_ps = mfsplit.parting_surface
    mfsplit.parting_surface = lambda *a, **k: None
    try:
        rw2 = pipeline.build_mold_system(_lean_y("LeanY"), _over(default_props(), **lean_cfg))
    finally:
        mfsplit.parting_surface = real_ps
    check(any("missing on one or both halves" in n for n in (rw2.get("wing_notes") or [])),
          f"wings built off the parting surface are reported, not silent "
          f"({rw2.get('wing_notes')})")
    reset_scene()
    rw3 = pipeline.build_mold_system(_lean_y("LeanY"), _over(default_props(), **dict(
        lean_cfg, bolt_count=2, wing_keys='CONE')))
    bpy.context.view_layer.update()
    hw3 = [q for q in rw3["parts"] if q.name in ("MF_Mold_A", "MF_Mold_B")]
    check(all(mfutil.part_is_valid(q)[0] for q in hw3) and not rw3.get("wing_notes"),
          "bolt holes and cone keys on the curved wings: both halves valid")

    # --- Bottom-fill channel (review request): pour beside the model through a
    # pipe on the parting seam, gated into the cavity at its floor.
    print("\n=== Bottom-fill channel ===")
    from moldforge.core import channel as mfchan
    from moldforge.core import build as mfbuild_c
    chan_seen = {}
    real_bore_c = mfchan.bore_channel

    def _spy_bore(mold, geo, coll):
        fr = geo["frame"]
        zs = [geo["gate_z"] + (geo["throat"] - geo["gate_z"]) * i / 20 for i in range(21)]
        chan_seen["pipe"] = [fr.point(geo["t_pipe"], z) for z in zs]
        chan_seen["gate"] = [fr.point(geo["t_pipe"] - (geo["t_pipe"] - geo["t_cav"] + 1.0)
                                      * i / 10, geo["gate_z"]) for i in range(11)]
        zm = (geo["gate_z"] + geo["throat"]) * 0.5
        mid = fr.point(geo["t_pipe"], zm)
        chan_seen["sides"] = (mid + fr.n * (geo["bore_r"] + 1.0),
                              mid - fr.n * (geo["bore_r"] + 1.0))
        chan_seen["wide"] = mid + fr.n * (geo["bore_r"] - 0.5)
        chan_seen["under"] = fr.point(geo["t_pipe"], geo["floor_z"] + 0.5)
        # just outside the gate's bore, halfway from the pipe to the cavity, on
        # both sides of the seam: there must be wall there or the gate leaks
        t_mid = (geo["t_pipe"] + geo["t_cav"]) * 0.5
        gm = fr.point(t_mid, geo["gate_z"])
        chan_seen["gate_wall"] = (gm + fr.n * (geo["bore_r"] + 0.8),
                                  gm - fr.n * (geo["bore_r"] + 0.8))
        chan_seen["geo"] = dict(geo)
        return real_bore_c(mold, geo, coll)

    mfchan.bore_channel = _spy_bore
    try:
        def _blocked(parts, pts, home):
            dg = bpy.context.evaluated_depsgraph_get()
            return [round(q_.z, 1) for q_ in pts
                    if any(mfbuild_c._inside_solid(o, q_ + home, dg) for o in parts)]

        chan_cfg = dict(box_style='POUR_BOX', wings=True, parts_count=2, split_axis='Y',
                        sprue_place='TOP', vent_count=0, wall_thickness=3.0,
                        shell_wall=2.0, bolt_auto=False, bolt_count=0,
                        pour_channel=True, channel_radius=5.0)
        reset_scene()
        rc1 = pipeline.build_mold_system(_lean_y("ChanLean", lean=12.0), _over(
            default_props(), **chan_cfg, base_style='FLAT', contoured=True))
        bpy.context.view_layer.update()
        home_c = rc1["positive"].location
        hc1 = [q for q in rc1["parts"] if q.name in ("MF_Mold_A", "MF_Mold_B")]
        g1 = chan_seen.get("geo", {})
        check(rc1.get("channel") and all(mfutil.part_is_valid(q)[0] for q in rc1["parts"]),
              f"channel on a leaning model: built, both halves valid "
              f"({rc1.get('channel_note')!r})")
        bp = _blocked(hc1, chan_seen.get("pipe", []), home_c)
        bg = _blocked(hc1, chan_seen.get("gate", []), home_c)
        check(not bp and not bg,
              f"the channel is open from the mouth down the pipe and in along the gate "
              f"(blocked pipe {bp[:3]}, gate {bg[:3]})")
        check(g1 and g1["gate_z"] - g1["floor_z"] <= g1["bore_r"] + 0.5,
              f"the gate opens right above the cavity floor "
              f"({g1.get('gate_z', 0) - g1.get('floor_z', 0):.1f} mm up for a "
              f"{g1.get('bore_r', 0):.0f} mm bore)")
        dgc = bpy.context.evaluated_depsgraph_get()
        sa, sb = chan_seen.get("sides", (None, None))
        a_has = sa is not None and mfbuild_c._inside_solid(hc1[0], sa + home_c, dgc)
        b_has = sb is not None and mfbuild_c._inside_solid(hc1[1], sb + home_c, dgc)
        check(a_has and b_has,
              "each half carries half of the pipe (it sits on the contoured parting), "
              "so the cured sprue lifts out")
        check(not _blocked(hc1, [chan_seen["wide"]], home_c),
              "the bore is the Channel Radius (5 mm here, more than the 3 mm silicone)")
        gw = chan_seen.get("gate_wall", ())
        walled_g = len(_blocked(hc1, list(gw), home_c))
        check(walled_g == 2,
              f"the gate is walled in on both sides between pipe and case - it "
              f"cannot leak sideways through the thin fin ({walled_g}/2)")

        reset_scene()
        rc2 = pipeline.build_mold_system(_flat_toy("ChanOpen", r0=22.0, r1=15.0, h=80.0),
                                         _over(default_props(), **chan_cfg,
                                               base_style='OPEN'))
        bpy.context.view_layer.update()
        check(rc2.get("channel") and all(mfutil.part_is_valid(q)[0] for q in rc2["parts"])
              and not _blocked(rc2["parts"], [chan_seen["under"]],
                               rc2["positive"].location),
              "open bottom: the channel runs out through the open base onto the board")

        reset_scene()
        rc3 = pipeline.build_mold_system(_flat_toy("ChanLock", r0=22.0, r1=15.0, h=80.0),
                                         _over(default_props(), **chan_cfg,
                                               base_style='LOCK'))
        bpy.context.view_layer.update()
        g3 = chan_seen.get("geo", {})
        # build space: the parts were moved home by the positive's location
        plinth_bot = mfutil.world_bbox(rc3["positive"])[0].z - rc3["positive"].location.z
        above = g3.get("gate_z", -1e9) - g3.get("bore_r", 0) - plinth_bot
        check(rc3.get("channel") and all(mfutil.part_is_valid(q)[0] for q in rc3["parts"])
              and above > 10.0,
              f"locking base: the gate opens above the 10 mm socket, not into it "
              f"(bottom of the gate {above:.1f} above the plinth's bottom)")
    finally:
        mfchan.bore_channel = real_bore_c

    # --- Label on the case (review request): the weights, debossed.
    print("\n=== Label on the case ===")
    from moldforge.core import label as mflabel_t
    flds = mflabel_t.label_fields(types.SimpleNamespace(mm_per_unit=1.0,
                                                        silicone_density=1.1,
                                                        cast_density=1.2),
                                  "Dragon", 50000.0, 100000.0)
    check(mflabel_t.format_label("{name}|{silicone_g} g / {cast_ml} ml {nope}", flds)
          == ["Dragon", "55 g / 100 ml {nope}"],
          f"label fields fill in, | starts a line, unknown fields stay as typed "
          f"({mflabel_t.format_label('{name}|{silicone_g} g / {cast_ml} ml {nope}', flds)})")
    lab_cfg = dict(box_style='POUR_BOX', base_style='FLAT', wings=True, parts_count=2,
                   split_axis='Y', sprue_place='TOP', vent_count=0, wall_thickness=4.0,
                   bolt_auto=False, bolt_count=0)
    reset_scene()
    rl0 = pipeline.build_mold_system(_flat_toy("LabelToy", r0=22.0, r1=15.0, h=90.0),
                                     _over(default_props(), **lab_cfg))
    fa0 = len(next(q for q in rl0["parts"] if q.name == "MF_Mold_A").data.polygons)
    va = sum(volume.mesh_volume(q) for q in rl0["parts"] if q.name.startswith("MF_Mold_"))
    reset_scene()
    rl1 = pipeline.build_mold_system(_flat_toy("LabelToy", r0=22.0, r1=15.0, h=90.0),
                                     _over(default_props(), **lab_cfg, label_on=True))
    la = next(q for q in rl1["parts"] if q.name == "MF_Mold_A")
    note_l = rl1.get("label_note") or ""
    s_g = f"{rl1['silicone_volume'] / 1000.0 * 1.15:.0f} g silicone"
    check(note_l.startswith("label cut in") and s_g in note_l,
          f"the label carries the silicone weight of THIS mold ({note_l!r}, expected {s_g!r})")
    check(all(mfutil.part_is_valid(q)[0] for q in rl1["parts"])
          and len(la.data.polygons) > fa0 + 300,
          f"... cut into a plaque on one shell, which stays valid "
          f"({fa0} -> {len(la.data.polygons)} faces)")
    # 0.44.0: the plaque bends with the round shell, so the text runs across it
    check("across the shell" in note_l,
          f"... and it reads across the round shell, not up it ({note_l!r})")
    vb = sum(volume.mesh_volume(q) for q in rl1["parts"] if q.name.startswith("MF_Mold_"))
    check(0.0 < vb - va < 3000.0,
          f"... a thin plaque: {(vb - va):.0f} mm3 of plastic")
    reset_scene()
    rlc_ = pipeline.build_mold_system(_flat_toy("LabelToy", r0=22.0, r1=15.0, h=90.0),
                                      _over(default_props(), **lab_cfg, label_on=True,
                                            label_style='RAISED'))
    note_c = rlc_.get("label_note") or ""
    check(note_c.startswith("label raised")
          and all(mfutil.part_is_valid(q)[0] for q in rlc_["parts"])
          and not any(mfutil.folded_faces(q) for q in rlc_["parts"]),
          f"Label Style Raised: the letters stand out of the plaque ({note_c!r})")
    # 0.44.1: on a ridged, knobbly toy (the user's) the label was skipped: the
    # plaque was laid on the bumps themselves. It now sits on a smooth surface
    # fitted through them, bridging the ridges.
    def _knobbly(name):
        import random as _rnd
        _rnd.seed(7)
        knobs = [(_rnd.uniform(0, 1), _rnd.uniform(0, 2 * math.pi), _rnd.uniform(0.8, 2.0),
                  _rnd.uniform(0.01, 0.02), _rnd.uniform(0.1, 0.2)) for _ in range(300)]
        bmk = bmesh.new(); rk = []
        rows_k, seg_k = 150, 96
        for j in range(rows_k + 1):
            t = j / rows_k
            r = 20.0 + 6.0 * math.exp(-((t - 0.35) / 0.08) ** 2)
            if t > 0.9:
                r *= math.sqrt(max(1 - ((t - 0.9) / 0.1) ** 2, 0.03))
            ridge = 2.0 * math.sin(2 * math.pi * 12 * t) if 0.45 < t < 0.85 else 0.0
            ring = []
            for i in range(seg_k):
                a = 2 * math.pi * i / seg_k
                rr = r + ridge
                for (bt, ba, amp, wt, wa) in knobs:
                    da = math.atan2(math.sin(a - ba), math.cos(a - ba))
                    rr += amp * math.exp(-((t - bt) / wt) ** 2 - (da / wa) ** 2)
                ring.append(bmk.verts.new((rr * math.cos(a), rr * math.sin(a), 150.0 * t)))
            rk.append(ring)
        for j in range(rows_k):
            for i in range(seg_k):
                k = (i + 1) % seg_k
                bmk.faces.new((rk[j][i], rk[j][k], rk[j + 1][k], rk[j + 1][i]))
        bmk.faces.new(list(reversed(rk[0]))); bmk.faces.new(rk[rows_k])
        bmesh.ops.recalc_face_normals(bmk, faces=bmk.faces[:])
        mk = bpy.data.meshes.new(name); bmk.to_mesh(mk); bmk.free()
        ok_ = bpy.data.objects.new(name, mk)
        bpy.context.scene.collection.objects.link(ok_)
        return ok_

    reset_scene()
    rlk = pipeline.build_mold_system(_knobbly("Knobbly"), _over(
        default_props(), **dict(lab_cfg, base_style='LOCK', split_axis='X', contoured=True,
                                silicone_gap=3.0, shell_wall=2.0, label_on=True)))
    note_k = rlk.get("label_note") or ""
    check(note_k.startswith("label cut in")
          and all(mfutil.part_is_valid(q)[0] for q in rlk["parts"])
          and not any(mfutil.folded_faces(q) for q in rlk["parts"]),
          f"a ridged, knobbly toy still gets its label, on a plaque bridging the "
          f"ridges ({note_k!r})")
    reset_scene()
    rl2 = pipeline.build_mold_system(_flat_toy("Tiny", r0=5.0, r1=4.0, h=14.0),
                                     _over(default_props(), **lab_cfg, label_on=True,
                                           label_text="A very long label that cannot fit"))
    check(all(mfutil.part_is_valid(q)[0] for q in rl2["parts"])
          and (rl2.get("label_note") or "").startswith("label skipped"),
          f"a label that fits nowhere is skipped with a reason ({rl2.get('label_note')!r})")

    # --- Stand legs (user request, instead of a separate cast box): the cured
    # silicone stays in the pour box; turned over, the mold rests on the funnel's
    # mouth and short thin legs keep it from tipping (0.43.1: the first legs ran
    # from the widest point to past the funnel - long, thick, plastic-hungry).
    print("\n=== Stand legs for casting ===")
    from moldforge.core import legs as mflegs
    cb_cfg = dict(box_style='POUR_BOX', wings=True, parts_count=2, split_axis='Y',
                  sprue_place='TOP', vent_count=0, wall_thickness=4.0, shell_wall=2.0,
                  bolt_auto=False, bolt_count=0)

    def _shell_pts(res_):
        sh = [q for q in res_["parts"] if q.name.startswith("MF_Mold_")
              and not q.name.startswith(("MF_Mold_Base", "MF_Mold_Cup", "MF_Mold_Plug"))]
        return sh, [q.matrix_world @ v.co for q in sh for v in q.data.vertices]

    hq, mq = mflegs._hull([(0, 0), (10, 0), (10, 10), (0, 10), (5, 5), (3, 7)]), None
    mq, _e = mflegs._margin(hq, (5.0, 5.0))
    mo, _e = mflegs._margin(hq, (12.0, 5.0))
    check(len(hq) == 4 and abs(mq - 5.0) < 1e-6 and abs(mo + 2.0) < 1e-6,
          f"stance maths: a 10 mm square of feet keeps its centre 5 mm in ({mq}), a point "
          f"2 mm outside reads -2 ({mo})")

    reset_scene()
    rl0_ = pipeline.build_mold_system(_flat_toy("LegToy", r0=22.0, r1=15.0, h=80.0),
                                      _over(default_props(), **cb_cfg, base_style='LOCK'))
    bpy.context.view_layer.update()
    sh0, pts0 = _shell_pts(rl0_)
    top0 = max(p_.z for p_ in pts0)                 # the funnel's mouth
    ax0 = Vector((sum(p_.x for p_ in pts0 if p_.z > top0 - 0.05),
                  sum(p_.y for p_ in pts0 if p_.z > top0 - 0.05), 0.0))
    ax0 /= max(sum(1 for p_ in pts0 if p_.z > top0 - 0.05), 1)
    mouth0 = max((p_ - ax0).xy.length for p_ in pts0 if p_.z > top0 - 0.5)
    vol0 = sum(volume.mesh_volume(q) for q in sh0)
    reset_scene()
    rlg = pipeline.build_mold_system(_flat_toy("LegToy", r0=22.0, r1=15.0, h=80.0),
                                     _over(default_props(), **cb_cfg, base_style='LOCK',
                                           stand_legs=True))
    bpy.context.view_layer.update()
    shl, ptsl = _shell_pts(rlg)
    topl = max(p_.z for p_ in ptsl)
    check(rlg.get("legs") == 4 and all(mfutil.part_is_valid(q)[0] for q in rlg["parts"]),
          f"locking base: four stand legs, every part valid ({rlg.get('legs_note')!r})")
    check(-0.5 < topl - top0 < -0.1,
          f"the feet are level with the funnel's mouth ({topl - top0:+.2f} mm: one cut "
          f"planes both), so the mouth carries the weight")
    flat_ = [p_ for p_ in ptsl if p_.z > topl - 0.05]
    feet = [p_ for p_ in flat_ if (p_ - ax0).xy.length > mouth0 + 1.0]
    quad = {(p_.x > ax0.x, p_.y > ax0.y) for p_ in feet}
    check(len(feet) >= 4 * 6 and len(quad) == 4
          and any((p_ - ax0).xy.length < mouth0 for p_ in flat_),
          f"four feet off the funnel, one per diagonal, flat on the mouth's plane "
          f"({len(feet)} foot points in {len(quad)} quadrants)")
    vl = sum(volume.mesh_volume(q) for q in shl)
    check(vl - vol0 < 0.06 * vol0,
          f"short thin legs: {(vl - vol0) / 1000:.2f} cm3 on {vol0 / 1000:.1f} cm3 of shells "
          f"(the 0.43.0 legs took about a sixth)")
    tip_ = rlg.get("legs_tip") or 0.0
    check(tip_ >= 9.9 and "tips over" not in (rlg.get("legs_note") or ""),
          f"... yet it must tip {tip_:.1f} degrees before it falls")
    reach_ = max((p_ - ax0).xy.length for p_ in feet)
    wide_ = max((p_ - ax0).xy.length for p_ in ptsl)
    check(reach_ < 0.8 * wide_,
          f"the feet reach only as far as the stance needs ({reach_:.0f} mm out, the "
          f"mold is {wide_:.0f})")
    by_half = {}
    for q in shl:
        zs_ = [(q.matrix_world @ v.co) for v in q.data.vertices]
        by_half[q.name] = sum(1 for p_ in zs_ if p_.z > topl - 0.05
                              and (p_ - ax0).xy.length > mouth0 + 1.0)
    check(all(n_ > 0 for n_ in by_half.values()),
          f"each half carries its own feet - none is cut by the seam ({by_half})")

    # A leaning toy: turned over its centre of gravity is off the funnel, and
    # the feet on that side reach further.
    reset_scene()
    rll = pipeline.build_mold_system(_lean_y("LegLean", lean=18.0), _over(
        default_props(), **cb_cfg, base_style='LOCK', stand_legs=True))
    bpy.context.view_layer.update()
    shll, ptll = _shell_pts(rll)
    topll = max(p_.z for p_ in ptll)
    fl_ = [p_ for p_ in ptll if p_.z > topll - 0.05]
    axl = Vector((sum(p_.x for p_ in fl_) / len(fl_), sum(p_.y for p_ in fl_) / len(fl_), 0.0))
    ry_lo = max((axl.y - p_.y for p_ in fl_), default=0.0)
    ry_hi = max((p_.y - axl.y for p_ in fl_), default=0.0)
    tipl = rll.get("legs_tip") or 0.0
    check(rll.get("legs") == 4 and all(mfutil.part_is_valid(q)[0] for q in rll["parts"])
          and tipl >= 9.9 and abs(ry_hi - ry_lo) > 4.0,
          f"a leaning toy: the feet reach further on its heavy side ({ry_lo:.0f} vs "
          f"{ry_hi:.0f} mm), it tips at {tipl:.1f} degrees ({rll.get('legs_note')!r})")

    reset_scene()
    rlt = pipeline.build_mold_system(_flat_toy("LegToy", r0=22.0, r1=15.0, h=80.0),
                                     _over(default_props(), **cb_cfg, base_style='OPEN',
                                           stand_legs=True, stand_tilt=15.0))
    bpy.context.view_layer.update()
    shl_t, ptst = _shell_pts(rlt)
    tipt = rlt.get("legs_tip") or 0.0
    check(rlt.get("legs") == 4 and all(mfutil.part_is_valid(q)[0] for q in rlt["parts"])
          and tipt >= 9.9,
          f"open bottom, 15 degree tilt: four legs, parts valid, tips at {tipt:.1f} degrees")
    # the feet and the mouth lie on one 15 degree plane, leaning to one of the
    # eight sides: the most points within 0.05 mm of the highest such plane
    best_t = (0, None)
    for i_ in range(8):
        a_ = math.pi * 0.25 * i_
        u_ = (math.cos(a_), math.sin(a_))
        lifts = [p_.z - (p_.x * u_[0] + p_.y * u_[1]) * math.tan(math.radians(15.0))
                 for p_ in ptst]
        top_ = max(lifts)
        on_ = sum(1 for v_ in lifts if v_ > top_ - 0.05)
        best_t = max(best_t, (on_, i_))
    check(best_t[0] >= 4 * 10,
          f"the feet are cut on one 15 degree plane ({best_t[0]} points on it, leaning "
          f"to side {best_t[1]})")
    reset_scene()
    rlf = pipeline.build_mold_system(_flat_toy("LegToy", r0=22.0, r1=15.0, h=80.0),
                                     _over(default_props(), **cb_cfg, base_style='FLAT',
                                           stand_legs=True))
    check(not rlf.get("legs") and "Open" in (rlf.get("legs_note") or ""),
          f"a closed bottom skips the legs with a reason ({rlf.get('legs_note')!r})")

    # A toy that curves away from a flat parting (Contoured off): at
    # mid-height all of it lies on one side of the plane, so that half of the
    # shell is two real pieces. 0.42.0 silently threw the smaller one away (a
    # missing wall section).
    print("\n=== A half in two pieces is kept, not trimmed ===")

    def _c_toy(name):
        bmc_ = bmesh.new(); rc_ = []
        for j in range(41):
            t = j / 40.0
            cxc = 64.0 * math.sin(math.pi * t)
            rc_.append([bmc_.verts.new((cxc + 13.0 * math.cos(2 * math.pi * i / 48),
                                        13.0 * math.sin(2 * math.pi * i / 48), 130.0 * t))
                        for i in range(48)])
        for j in range(40):
            for i in range(48):
                k = (i + 1) % 48
                bmc_.faces.new((rc_[j][i], rc_[j][k], rc_[j + 1][k], rc_[j + 1][i]))
        bmc_.faces.new(list(reversed(rc_[0]))); bmc_.faces.new(rc_[40])
        bmesh.ops.recalc_face_normals(bmc_, faces=bmc_.faces[:])
        mc_ = bpy.data.meshes.new(name); bmc_.to_mesh(mc_); bmc_.free()
        oc_ = bpy.data.objects.new(name, mc_)
        bpy.context.scene.collection.objects.link(oc_)
        return oc_

    reset_scene()
    rpc = pipeline.build_mold_system(_c_toy("CToy"), _over(default_props(), **dict(
        cb_cfg, base_style='LOCK', split_axis='X', contoured=False)))
    names_pc = sorted(q.name for q in rpc["parts"])
    check(all(mfutil.part_is_valid(q)[0] for q in rpc["parts"])
          and any(n.startswith("MF_Mold_") and n[-1] == "2" for n in names_pc)
          and not rpc.get("trimmed"),
          f"a C-shaped toy on a flat parting: the split half's two pieces are both "
          f"kept as printable parts ({names_pc})")
    check(any("pieces" in n for n in (rpc.get("split_notes") or [])),
          f"... and Generate says so ({rpc.get('split_notes')})")

    # 0.44.0: a Locking Base follows the contoured parting too (it used to be
    # forced flat), so the curved toy's halves are one piece each - but the
    # seam stays a flat plane through the plinth and its socket, through the
    # middle of the base, so each half's share pulls straight off the teeth.
    print("\n=== Locking Base on a contoured parting ===")
    reset_scene()
    rlc = pipeline.build_mold_system(_c_toy("CToyC"), _over(default_props(), **dict(
        cb_cfg, base_style='LOCK', split_axis='X', contoured=True)))
    bpy.context.view_layer.update()
    names_lc = sorted(q.name for q in rlc["parts"])
    halves_lc = [q for q in rlc["parts"] if q.name in ("MF_Mold_A", "MF_Mold_B")]
    check(names_lc.count("MF_Mold_A") == 1 and not any(n[-1] == "2" for n in names_lc)
          and len(halves_lc) == 2
          and all(mfutil.part_is_valid(q)[0] for q in rlc["parts"])
          and not rlc.get("split_notes"),
          f"the curved toy on a Locking Base: two whole halves ({names_lc})")
    zb_ = min(mfutil.world_bbox(q)[0].z for q in halves_lc)
    band_ = (zb_ + 0.5, zb_ + 9.0)               # the plinth's socket
    a_x = [(q.matrix_world @ v.co).x for q in halves_lc if q.name == "MF_Mold_A"
           for v in q.data.vertices if band_[0] <= (q.matrix_world @ v.co).z <= band_[1]]
    b_x = [(q.matrix_world @ v.co).x for q in halves_lc if q.name == "MF_Mold_B"
           for v in q.data.vertices if band_[0] <= (q.matrix_world @ v.co).z <= band_[1]]
    on_ = sum(1 for x_ in a_x if abs(x_ - min(a_x)) < 0.02)
    check(abs(min(a_x) - max(b_x)) < 0.02 and on_ >= 20,
          f"... the seam is one flat plane through the socket (x = {min(a_x):.2f} on A, "
          f"{max(b_x):.2f} on B, {on_} vertices on it)")
    check(abs(min(a_x) - (min(b_x) + max(a_x)) * 0.5) < 3.0,
          f"... through the middle of the base ({min(b_x):.1f} to {max(a_x):.1f}), "
          f"so both halves hold half the socket")
    check(not any(mfutil.folded_faces(q) for q in rlc["parts"]),
          "... and no folded faces where the contoured seam meets the flat one")

    # 0.44.1 (the user's toy): a HOLLOW underside on a Locking Base. The
    # plinth's top closes the hollow, the cavity cutter had a sealed bubble
    # there, and the carve left a plastic wafer floating in the silicone
    # space - each half came out as two "shells".
    print("\n=== A hollow underside leaves nothing floating ===")

    def _recess_toy(name, R=26.0, r_in=20.0, depth=8.0, h=90.0, seg=96):
        bmr = bmesh.new()

        def ring_(r, z):
            return [bmr.verts.new((r * math.cos(2 * math.pi * i / seg),
                                   r * math.sin(2 * math.pi * i / seg), z))
                    for i in range(seg)]
        prof = [(r_in, depth), (r_in, 0.0), (R, 0.0)]
        for j in range(1, 31):
            t = j / 30
            r = R - 6.0 * t
            if t > 0.85:
                r *= math.sqrt(max(1 - ((t - 0.85) / 0.15) ** 2, 0.04))
            prof.append((r, h * t))
        rings_r = [ring_(r, z) for r, z in prof]
        for a_, b_ in zip(rings_r, rings_r[1:]):
            for i in range(seg):
                k = (i + 1) % seg
                bmr.faces.new((a_[i], a_[k], b_[k], b_[i]))
        bmr.faces.new(rings_r[0])
        bmr.faces.new(list(reversed(rings_r[-1])))
        bmesh.ops.recalc_face_normals(bmr, faces=bmr.faces[:])
        mr = bpy.data.meshes.new(name); bmr.to_mesh(mr); bmr.free()
        orr = bpy.data.objects.new(name, mr)
        bpy.context.scene.collection.objects.link(orr)
        return orr

    # the fill itself: a box with a sealed hollow inside
    bmv = bmesh.new()
    bmesh.ops.create_cube(bmv, size=20.0)
    inner_v = bmesh.ops.create_cube(bmv, size=8.0)["verts"]
    inner_f = list({f for v in inner_v for f in v.link_faces})
    bmesh.ops.reverse_faces(bmv, faces=inner_f)
    mv = bpy.data.meshes.new("Hollow"); bmv.to_mesh(mv); bmv.free()
    ov = bpy.data.objects.new("Hollow", mv)
    bpy.context.scene.collection.objects.link(ov)
    vol_h = volume.mesh_volume(ov)
    filled = mfutil.fill_voids(ov)
    check(filled == 1 and mfutil.island_count(ov) == 1
          and abs(volume.mesh_volume(ov) - 8000.0) < 1.0,
          f"fill_voids fills a sealed hollow ({filled} filled, {vol_h:.0f} -> "
          f"{volume.mesh_volume(ov):.0f} mm3)")
    mfutil.remove_object(ov)
    reset_scene()
    rhu = pipeline.build_mold_system(_recess_toy("RecessToy"), _over(default_props(), **dict(
        cb_cfg, base_style='LOCK', split_axis='X', contoured=True, wall_thickness=3.0,
        silicone_gap=3.0)))
    names_hu = sorted(q.name for q in rhu["parts"] if q.name.startswith("MF_Mold_"))
    check(names_hu == ["MF_Mold_A", "MF_Mold_B"] and not rhu.get("split_notes")
          and all(mfutil.part_is_valid(q)[0] for q in rhu["parts"]),
          f"a toy with an 8 mm hollow underside on a Locking Base: two halves, no "
          f"wafer left floating in the silicone space ({names_hu})")

    print("\n=== Band notches on the clamp wings ===")
    nb_cfg = dict(box_style='POUR_BOX', base_style='FLAT', wings=True, parts_count=2,
                  split_axis='Y', sprue_place='TOP', vent_count=0, wall_thickness=4.0,
                  bolt_auto=False, bolt_count=0, wing_width=10.0)
    reset_scene()
    rn0 = pipeline.build_mold_system(_flat_toy("NotchToy", r0=22.0, r1=15.0, h=100.0),
                                     _over(default_props(), **nb_cfg))
    v0 = sum(volume.mesh_volume(q) for q in rn0["parts"])
    reset_scene()
    rn1 = pipeline.build_mold_system(_flat_toy("NotchToy", r0=22.0, r1=15.0, h=100.0),
                                     _over(default_props(), **nb_cfg, wing_band_notches=True))
    v1 = sum(volume.mesh_volume(q) for q in rn1["parts"])
    one = 2.5 * 4.0 * (4.0 + 0.0)               # depth x height x wing thickness, roughly
    check(all(mfutil.part_is_valid(q)[0] for q in rn1["parts"])
          and v0 - v1 > 4 * one * 0.6,
          f"band notches cut into both wings' edges ({(v0 - v1):.0f} mm3 removed, "
          f"at least four notches)")
    # 0.44.0: a radial split gets them too - a row up every seam's flange,
    # dodging its bolt holes, and none on the bottom-fill channel's seam.
    from moldforge.core import build as mfbuild_n
    for n_parts, extra in ((3, {}), (4, {"bolt_auto": True}),
                           (3, {"pour_channel": True})):
        cfg_n = dict(nb_cfg, parts_count=n_parts, **extra)
        reset_scene()
        rr0 = pipeline.build_mold_system(_flat_toy("NotchToyR", r0=22.0, r1=15.0, h=100.0),
                                         _over(default_props(), **cfg_n))
        vr0 = sum(volume.mesh_volume(q) for q in rr0["parts"])
        reset_scene()
        cut_n = []
        _orig_rn = mfbuild_n.cut_radial_band_notches

        def _count_rn(*a, **k):
            cut_n.append(_orig_rn(*a, **k))
            return cut_n[-1]
        mfbuild_n.cut_radial_band_notches = _count_rn
        try:
            rr1 = pipeline.build_mold_system(
                _flat_toy("NotchToyR", r0=22.0, r1=15.0, h=100.0),
                _over(default_props(), **cfg_n, wing_band_notches=True))
        finally:
            mfbuild_n.cut_radial_band_notches = _orig_rn
        vr1 = sum(volume.mesh_volume(q) for q in rr1["parts"])
        seams_n = n_parts - (1 if extra.get("pour_channel") else 0)
        wedges = [q for q in rr1["parts"] if q.name.startswith("MF_Mold_")]
        check(all(mfutil.part_is_valid(q)[0] for q in rr1["parts"])
              and len(wedges) == n_parts
              and cut_n and cut_n[-1] >= 2 * seams_n
              and vr0 - vr1 > 2 * seams_n * one * 0.6,
              f"{n_parts} wedges{' + channel' if extra.get('pour_channel') else ''}"
              f"{' + bolts' if extra.get('bolt_auto') else ''}: {cut_n[-1] if cut_n else 0} "
              f"notches up {seams_n} seam flanges ({(vr0 - vr1):.0f} mm3 removed), "
              f"every wedge valid")

    # --- No folded faces anywhere: a face whose triangles fold back renders as a
    # black patch and exports inverted facets. Boolean zig-zags in big flat seam
    # faces did it even on a plain Locking Base.
    print("\n=== No folded faces ===")
    fold_cases = [dict(base_style='LOCK'), dict(base_style='FLAT'),
                  dict(base_style='OPEN', base_plate=True),
                  dict(base_style='LOCK', pour_channel=True, label_on=True, stand_legs=True,
                       wing_band_notches=True)]
    worst_f = {}
    for fc in fold_cases:
        reset_scene()
        rf_ = pipeline.build_mold_system(_lean_y("FoldLean", lean=10.0), _over(
            default_props(), box_style='POUR_BOX', wings=True, parts_count=2,
            split_axis='Y', sprue_place='TOP', vent_count=0, wall_thickness=4.0,
            shell_wall=2.0, bolt_auto=False, bolt_count=0, contoured=True,
            wing_width=10.0, **fc))
        for q in rf_["parts"]:
            nf = len(mfutil.folded_faces(q))
            if nf:
                worst_f[f"{fc.get('base_style')}:{q.name}"] = nf
    check(not worst_f, f"no generated part has folded faces on a leaning model, with "
                       f"every option on ({worst_f})")

    # --- Locking Base (Pour Box): a sawtooth plinth united into the HIGH-POLY positive,
    # the shells socketing onto it (anti-slip) with an OPEN bottom. The rebuilt feature
    # must (1) keep the positive at full detail, (2) print the bottom open, (3) cut a
    # fine sawtooth, (4) keep the clamp wings clean — the four things the graft broke.
    print("\n=== Locking base (pour box) ===")

    def _lock_cone(name, r1=9.0, r2=6.0, depth=26.0, seg=48):
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, segments=seg, radius1=r1, radius2=r2, depth=depth)
        m = bpy.data.meshes.new(name); bm.to_mesh(m); bm.free()
        o = bpy.data.objects.new(name, m); bpy.context.scene.collection.objects.link(o)
        return o

    def _lock_star(name, n=5, ro=15.0, ri=6.5, depth=16.0):
        pts = [((ro if k % 2 == 0 else ri) * math.cos(math.pi / 2 + k * math.pi / n),
                (ro if k % 2 == 0 else ri) * math.sin(math.pi / 2 + k * math.pi / n))
               for k in range(2 * n)]
        bm = bmesh.new()
        bot = [bm.verts.new((x, y, 0.0)) for (x, y) in pts]
        top = [bm.verts.new((x, y, depth)) for (x, y) in pts]
        bm.faces.new(bot); bm.faces.new(list(reversed(top)))
        for i in range(len(pts)):
            j = (i + 1) % len(pts)
            bm.faces.new((bot[i], bot[j], top[j], top[i]))
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        m = bpy.data.meshes.new(name); bm.to_mesh(m); bm.free()
        o = bpy.data.objects.new(name, m); bpy.context.scene.collection.objects.link(o)
        return o

    def _detailed_blob(name):           # a fine, flat-bottomed mesh (would-be remesh bait)
        bm = bmesh.new()
        bmesh.ops.create_icosphere(bm, subdivisions=5, radius=10.0)
        for v in bm.verts:
            if v.co.z < -7.0:
                v.co.z = -7.0           # flatten the base so the plinth welds cleanly
        m = bpy.data.meshes.new(name); bm.to_mesh(m); bm.free()
        o = bpy.data.objects.new(name, m); bpy.context.scene.collection.objects.link(o)
        return o

    def _bottom_open(parts):
        """Cast rays straight up from below several off-centre footprint points; the
        bottom is OPEN if every ray travels a long way before its first hit (it passes
        through the hollow socket — no floor)."""
        mn = Vector((1e9, 1e9, 1e9)); mx = -mn
        for p in parts:
            a, b = mfutil.world_bbox(p)
            mn = Vector((min(mn.x, a.x), min(mn.y, a.y), min(mn.z, a.z)))
            mx = Vector((max(mx.x, b.x), max(mx.y, b.y), max(mx.z, b.z)))
        cx, cy, H = (mn.x + mx.x) * 0.5, (mn.y + mx.y) * 0.5, mx.z - mn.z
        worst = H
        for dx, dy in ((0, 3), (3, 3), (-3, -3), (0, 5), (5, 0), (-5, 0)):
            best = None
            for p in parts:
                inv = p.matrix_world.inverted()
                hit, loc, _n, _i = p.ray_cast(inv @ Vector((cx + dx, cy + dy, mn.z - 3.0)),
                                              (inv.to_3x3() @ Vector((0, 0, 1))).normalized())
                if hit:
                    wz = (p.matrix_world @ loc).z
                    best = wz if best is None else min(best, wz)
            worst = min(worst, (best - mn.z) if best is not None else H)
        return worst > 0.3 * H

    def _outer_wall_oscillation(parts, z0, z1):
        """Variation of the OUTER base-wall radius up the side. The sawtooth must NOT
        telegraph to the shell's outside (the artefact): the skirt is smooth, so this is
        ~0 even though the inner socket has deep teeth."""
        def outer_r(theta, z):
            d = Vector((-math.cos(theta), -math.sin(theta), 0.0))
            o = Vector((100.0 * math.cos(theta), 100.0 * math.sin(theta), z))
            best = None
            for p in parts:
                inv = p.matrix_world.inverted()
                hit, loc, _n, _i = p.ray_cast(inv @ o, (inv.to_3x3() @ d).normalized())
                if hit:
                    w = p.matrix_world @ loc
                    rr = math.hypot(w.x, w.y)
                    best = rr if best is None else max(best, rr)
            return best
        worst = 0.0
        for theta in (0.6, 1.0, 2.2, 3.7, 5.0):
            rs = [outer_r(theta, z0 + (z1 - z0) * k / 10.0) for k in range(11)]
            rs = [x for x in rs if x is not None]
            if len(rs) >= 6:
                worst = max(worst, max(rs) - min(rs))
        return worst

    reset_scene()
    lk = pipeline.build_mold_system(_lock_cone("Lk"), _over(default_props(),
            box_style='POUR_BOX', base_style='LOCK', wings=True, parts_count=2,
            contoured=True, lock_height=10.0, lock_margin=4.0, lock_teeth=3,
            lock_tooth_depth=2.0, lock_tolerance=0.3))
    check(len(lk["parts"]) == 2 and all(mfutil.part_is_valid(p)[0] for p in lk["parts"]),
          "locking base: valid two-part pour box (wings on)")
    pmn, pmx = mfutil.world_bbox(lk["positive"])
    check((pmx.z - pmn.z) > 26.0 + 6.0,
          f"plinth united into the positive (height {pmx.z - pmn.z:.1f} > model 26)")
    # Resin-hollowing regression: a plain mesh join left the plinth as a SECOND closed
    # shell nested in the positive, so slicer hollowing (Anycubic Photon Workshop etc.)
    # hollowed the model shell and kept the base solid. On a clean master the plinth must
    # now be boolean-united: one island, fully manifold — a hollow offset sees ONE solid.
    isl_p = island_count(lk["positive"])
    nme_p = mfutil.nonmanifold_count(lk["positive"])
    check(lk.get("base_united") is True and isl_p == 1 and nme_p == 0,
          f"positive+base is ONE manifold solid for slicer hollowing "
          f"(united={lk.get('base_united')}, {isl_p} island(s), {nme_p} non-manifold edges)")
    check(_bottom_open(lk["parts"]), "locking base: shells print OPEN at the bottom")
    lmn = mfutil.world_bbox(lk["parts"][0])[0]
    for q in lk["parts"][1:]:
        a = mfutil.world_bbox(q)[0]
        lmn = Vector((min(lmn.x, a.x), min(lmn.y, a.y), min(lmn.z, a.z)))
    osc = _outer_wall_oscillation(lk["parts"], lmn.z + 1.5, lmn.z + 8.0)
    check(osc < 1.0,
          f"sawtooth does NOT telegraph to the shell's OUTSIDE "
          f"(outer base-wall oscillation {osc:.2f} mm << tooth 2.0 — smooth skirt)")
    check(lk["silicone_volume"] > 0.0, f"locking base reports silicone ({lk['silicone_volume']:.0f})")

    # The plinth's sawtooth must be CLEAN and CONSISTENT (the bug was chaotic spikes / a
    # vertical seam from per-vertex displacement on triangulated geometry). Measure the
    # positive's plinth radius oscillation up the side at several angles: the teeth must be
    # present (oscillation ~ tooth) AND consistent across angles (a small spread — chaos
    # would make some angles spike far more than others).
    def _radius_ray(obj, theta, z):
        d = Vector((-math.cos(theta), -math.sin(theta), 0.0))
        o = Vector((200.0 * math.cos(theta), 200.0 * math.sin(theta), z))
        inv = obj.matrix_world.inverted()
        hit, loc, _n, _i = obj.ray_cast(inv @ o, (inv.to_3x3() @ d).normalized())
        if not hit:
            return None
        w = obj.matrix_world @ loc
        return math.hypot(w.x, w.y)
    pmn2, _pmx2 = mfutil.world_bbox(lk["positive"])
    z0p, z1p = pmn2.z + 1.5, pmn2.z + 8.0
    per_angle = []
    for theta in (0.3, 1.2, 2.0, 2.9, 3.8, 4.6, 5.5):
        rs = [_radius_ray(lk["positive"], theta, z0p + (z1p - z0p) * k / 12) for k in range(13)]
        rs = [r for r in rs if r is not None]
        if len(rs) >= 8:
            per_angle.append(max(rs) - min(rs))
    teeth_osc = sum(per_angle) / len(per_angle)
    teeth_spread = max(per_angle) - min(per_angle)
    check(teeth_osc > 0.8,
          f"plinth has real sawtooth teeth (mean oscillation {teeth_osc:.2f} mm for a 2 mm tooth)")
    check(teeth_spread < 1.2,
          f"plinth teeth are CLEAN/consistent, not chaotic spikes "
          f"(angle-to-angle spread {teeth_spread:.2f} mm)")

    # No HOLES at the inside rim corner: the socket is dilate(plinth, tol) and pokes tol
    # above the plinth top, so the outer skirt must reach past it or a slot opens around
    # the rim into the cavity. Fire rays outward from the axis across the rim band against
    # BOTH halves; with the cavity sealed there, every ray hits shell (a slot lets some
    # escape to the outside).
    z_rim0, z_rim1 = lmn.z + 8.5, lmn.z + 12.5
    escapes = total = 0
    for ti in range(48):
        theta = 2.0 * math.pi * ti / 48
        d = Vector((math.cos(theta), math.sin(theta), 0.0))
        for k in range(9):
            z = z_rim0 + (z_rim1 - z_rim0) * k / 8
            o = Vector((0.0, 0.0, z))
            hitany = False
            for part in lk["parts"]:
                inv = part.matrix_world.inverted()
                if part.ray_cast(inv @ o, (inv.to_3x3() @ d).normalized())[0]:
                    hitany = True
                    break
            total += 1
            if not hitany:
                escapes += 1
    check(escapes <= total * 0.02,
          f"rim is sealed — no holes into the cavity ({escapes}/{total} rays escaped)")

    # The socket teeth must transfer to the shells at ANY base height. A short plinth has a
    # finer tooth pitch, and the old Solidify-offset socket self-intersected there and got
    # voxel-remeshed smooth (no grip). Build a LOW base and measure the socket inner-wall
    # oscillation (a ray outward from the axis hits the socket wall the plinth seats into).
    reset_scene()
    lks5 = pipeline.build_mold_system(_lock_cone("LkS5"), _over(default_props(),
            box_style='POUR_BOX', base_style='LOCK', wings=False, parts_count=2,
            contoured=True, lock_height=5.0, lock_teeth=3, lock_tooth_depth=2.0,
            lock_margin=4.0, lock_tolerance=0.3))
    smn = min(mfutil.world_bbox(p)[0].z for p in lks5["parts"])

    def _socket_osc(parts, z0, z1):
        per = []
        for theta in (0.4, 1.0, 2.2, 3.6, 4.8):
            rs = []
            for k in range(17):
                z = z0 + (z1 - z0) * k / 16.0
                d = Vector((math.cos(theta), math.sin(theta), 0.0)); o = Vector((0.0, 0.0, z))
                best = None
                for p in parts:
                    inv = p.matrix_world.inverted()
                    hit, loc, _n, _i = p.ray_cast(inv @ o, (inv.to_3x3() @ d).normalized())
                    if hit:
                        w = p.matrix_world @ loc
                        r = math.hypot(w.x, w.y)
                        best = r if best is None else min(best, r)
                if best is not None:
                    rs.append(best)
            if len(rs) >= 10:
                per.append(max(rs) - min(rs))
        return (sum(per) / len(per)) if per else 0.0

    sock_osc = _socket_osc(lks5["parts"], smn + 1.0, smn + 4.5)
    check(sock_osc > 1.2,
          f"socket teeth transfer to the shells at a low base height "
          f"(inner-wall oscillation {sock_osc:.2f} mm for a 2 mm tooth)")

    # The positive must keep the model's FULL detail (the old graft showed the low-poly
    # cleanup remesh instead). A fine 5k-face blob must come back with MORE faces (model +
    # plinth), never collapsed to a coarse remesh.
    reset_scene()
    blob = _detailed_blob("LkDetail")
    nblob = len(blob.data.polygons)
    lkd = pipeline.build_mold_system(blob, _over(default_props(),
            box_style='POUR_BOX', base_style='LOCK', wings=False, contoured=True))
    check(len(lkd["positive"].data.polygons) >= nblob,
          f"positive keeps full detail ({len(lkd['positive'].data.polygons)} faces "
          f">= model {nblob}, not a coarse remesh)")

    # Concave footprint with the REAL default (contoured on): must build cleanly and not
    # explode (the even-offset blow-up the sawtooth/concave outline used to trigger).
    reset_scene()
    lks = pipeline.build_mold_system(_lock_star("LkStar"), _over(default_props(),
            box_style='POUR_BOX', base_style='LOCK', wings=True, contoured=True))
    check(len(lks["parts"]) == 2 and all(mfutil.part_is_valid(p)[0] for p in lks["parts"]),
          "locking base on a concave (star) footprint builds")
    smn, smx = mfutil.world_bbox(lks["positive"])
    check(max(smx.x - smn.x, smx.y - smn.y, smx.z - smn.z) < 200.0,
          f"concave dilation didn't explode (span "
          f"{max(smx.x - smn.x, smx.y - smn.y, smx.z - smn.z):.0f} units, not millions)")

    reset_scene()
    lk3 = pipeline.build_mold_system(_lock_cone("Lk3"), _over(default_props(),
            box_style='POUR_BOX', base_style='LOCK', wings=False, parts_count=3))
    check(len(lk3["parts"]) == 3 and all(mfutil.part_is_valid(p)[0] for p in lk3["parts"]),
          "locking base: valid three radial parts")

    # Unite Base with Model OFF: the base is emitted as its own printable part
    # (MF_Mold_Base) and the positive stays the pristine model (no plinth joined in).
    reset_scene()
    lku = pipeline.build_mold_system(_lock_cone("LkU"), _over(default_props(),
            box_style='POUR_BOX', base_style='LOCK', wings=False, parts_count=2,
            lock_unite=False, lock_height=10.0))
    check(len(lku["parts"]) == 3 and all(mfutil.part_is_valid(p)[0] for p in lku["parts"]),
          "lock base separate: 2 shells + the base part, all valid")
    unames = [q.name for q in lku["parts"]]
    check(any(n.startswith("MF_Mold_Base") for n in unames),
          f"separate base part present ({unames})")
    umn, umx = mfutil.world_bbox(lku["positive"])
    check((umx.z - umn.z) < 26.0 + 2.0,
          f"positive stays the pristine model, no base united (h {umx.z - umn.z:.1f} ~ 26)")
    ubase = next(q for q in lku["parts"] if q.name.startswith("MF_Mold_Base"))
    ubmn, ubmx = mfutil.world_bbox(ubase)
    check(ubmn.z < umn.z - 5.0 and ubmx.z <= umn.z + 2.5,
          f"separate base sits below the model, topping out at the weld band "
          f"(base {ubmn.z:.1f}..{ubmx.z:.1f} vs model bottom {umn.z:.1f})")

    # Locking Base only applies to the pour box; on a direct mold it must no-op safely.
    reset_scene()
    lkn = pipeline.build_mold_system(_lock_cone("LkN"),
            _over(default_props(), box_style='SOLID', base_style='LOCK', wings=False))
    check(all(mfutil.part_is_valid(p)[0] for p in lkn["parts"]),
          "locking base ignored on a non-pour-box (no crash)")

    # A multi-island sculpt (separate-but-touching sub-parts) must build: the jacket is
    # cleaned from a proxy so touching islands merge, while the positive stays high-poly.
    # Genuinely far-apart pieces must FAIL FAST (not loop) with the join hint.
    def _two_islands(name, sep):
        bm = bmesh.new()
        for cx in (-sep * 0.5, sep * 0.5):
            tmp = bmesh.new()
            bmesh.ops.create_icosphere(tmp, subdivisions=3, radius=7.0)
            for v in tmp.verts:
                v.co.x += cx
                if v.co.z < -5.0:
                    v.co.z = -5.0
            me = bpy.data.meshes.new("t"); tmp.to_mesh(me); tmp.free()
            bm.from_mesh(me); bpy.data.meshes.remove(me)
        m = bpy.data.meshes.new(name); bm.to_mesh(m); bm.free()
        o = bpy.data.objects.new(name, m); bpy.context.scene.collection.objects.link(o)
        return o

    reset_scene()
    touch = _two_islands("LkTouch", 8.0)
    check(island_count(touch) > 1, "two-island test model really is multiple islands")
    lkt = pipeline.build_mold_system(touch, _over(default_props(),
            box_style='POUR_BOX', base_style='LOCK', wings=False))
    check(len(lkt["parts"]) == 2 and all(mfutil.part_is_valid(p)[0] for p in lkt["parts"]),
          "locking base merges touching sub-islands into one moldable solid")

    reset_scene()
    failed_fast = False
    try:
        pipeline.build_mold_system(_two_islands("LkFar", 60.0),
                _over(default_props(), box_style='POUR_BOX', base_style='LOCK', wings=False))
    except pipeline.MoldGeometryError as exc:
        failed_fast = "separate pieces" in str(exc)
    check(failed_fast, "genuinely separate pieces fail fast with the join hint (no loop)")

    # --- Deep-crease models must not ship fold-overs (self-intersections): the
    # offset folds across bulb creases; the dilation gate has to catch + remesh.
    print("\n=== No self-intersections on crease-heavy models ===")
    reset_scene()
    rsx = pipeline.build_mold_system(make_bulbs_master(),
                                     _over(default_props(), box_style='POUR_BOX',
                                           base_style='OPEN', wings=True,
                                           wall_thickness=4.0, sprue=True,
                                           vent_count=0))
    for q in rsx["parts"]:
        check(not mfutil.has_self_intersections(q),
              f"bulb-crease pour box: {q.name} free of fold-overs")
    check(all(mfutil.part_is_valid(q)[0] for q in rsx["parts"]),
          "bulb-crease pour box: parts valid")

    # --- Bolt holes: Auto Bolts on = by height; off with count 0 = truly none.
    print("\n=== Bolt holes: auto vs none ===")
    reset_scene()
    rba = pipeline.build_mold_system(make_master("Model"),
                                     _over(default_props(), box_style='POUR_BOX',
                                           wings=True, sprue=False,
                                           bolt_auto=True))
    fa = sum(part_face_count(q) for q in rba["parts"])
    reset_scene()
    rb0 = pipeline.build_mold_system(make_master("Model"),
                                     _over(default_props(), box_style='POUR_BOX',
                                           wings=True, sprue=False,
                                           bolt_auto=False, bolt_count=0))
    f0 = sum(part_face_count(q) for q in rb0["parts"])
    check(all(mfutil.part_is_valid(q)[0] for q in rb0["parts"]),
          "no-bolt wings: valid halves")
    check(fa > f0 + 40,
          f"bolt count 0 really drills nothing (auto {fa} vs none {f0} faces)")

    # --- The funnel must keep silicone between itself and the positive: spout
    # base and bore end 0.4 gap ABOVE the pour point (they used to dig to
    # pt_z - gap/2, almost touching the model with a wide sprue).
    # The funnel channel must CONNECT to the cavity (it's the cast pour path) and
    # the bored throat radius must equal the user's Throat Radius — not the silent
    # auto-narrowed value of old. Measure the hole radius at the ceiling.
    print("\n=== Throat radius respected + funnel opens to the cavity ===")
    reset_scene()
    set_r = 8.0
    big = make_master("Model", radius=30.0)      # big enough that r=8 isn't size-capped
    rcl = pipeline.build_mold_system(big,
                                     _over(default_props(), box_style='POUR_BOX',
                                           base_style='OPEN', wings=False,
                                           wall_thickness=4.0, sprue=True,
                                           sprue_radius=set_r, vent_count=0))
    pos = rcl["positive"]
    topv = max((pos.matrix_world @ v.co for v in pos.data.vertices), key=lambda w: w.z)
    # Rays down a ring at radius 6 (inside an r=8 throat, but well OUTSIDE the old
    # auto-narrowed ~1.2 neck) must reach the cavity — proving the wide throat is
    # honoured and open, not silently shrunk.
    blocked = 0
    for k in range(16):
        a = 2.0 * math.pi * k / 16.0
        px = topv.x + math.cos(a) * 6.0
        py = topv.y + math.sin(a) * 6.0
        for q in rcl["parts"]:
            mw = q.matrix_world; invq = mw.inverted()
            d = (invq.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized()
            hit, _l, _n, _i = q.ray_cast(invq @ Vector((px, py, topv.z + 40.0)), d)
            if hit and (mw @ _l).z > topv.z - 1.0:
                blocked += 1
                break
    check(blocked == 0,
          f"throat open at the full set radius {set_r} ({blocked}/16 rays blocked)")

    # --- The funnel throat must be FULLY open: the bore has to punch below the
    # lowest shell ceiling under the neck, or part of the throat stays blocked
    # by a crescent shelf of un-bored shell.
    print("\n=== Funnel throat fully open (no shelf) ===")

    def throat_first_hits(res, ring_r=1.6):
        pos = res["positive"]
        topv = max((pos.matrix_world @ v.co for v in pos.data.vertices),
                   key=lambda w: w.z)
        hits = []
        for k in range(8):
            a = 2.0 * math.pi * k / 8.0
            px = topv.x + math.cos(a) * ring_r
            py = topv.y + math.sin(a) * ring_r
            first = None
            for q in res["parts"]:
                mw = q.matrix_world
                invq = mw.inverted()
                d = (invq.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized()
                top = mfutil.world_bbox(q)[1].z
                hit, loc, _n, _i = q.ray_cast(invq @ Vector((px, py, top + 5.0)), d)
                if hit:
                    z = (mw @ loc).z
                    first = z if first is None else max(first, z)
            hits.append(first)
        return hits, topv

    def sph_at(r, x):
        bm2 = bmesh.new()
        bmesh.ops.create_icosphere(bm2, subdivisions=3, radius=r)
        me2 = bpy.data.meshes.new("p"); bm2.to_mesh(me2); bm2.free()
        o2 = bpy.data.objects.new("p", me2)
        o2.data.transform(Matrix.Translation((x, 0, 0)))
        bpy.context.scene.collection.objects.link(o2)
        return o2

    # (a) plain blob; (b) a tall side-bump inside the flared mouth's footprint
    # but outside the neck — it must not push the throat up into a shelf;
    # (c) steep crown + fat sprue — the constraints conflict at full width, so
    # the neck must auto-narrow rather than leave a plug.
    cases = (("plain crown", None, (1.0, 0.8, 1.3), 4.0),
             ("side bump near mouth", (7.0, 2.5, 5.0), (1.0, 0.8, 1.3), 4.0),
             ("steep crown, fat sprue", None, (1.0, 0.95, 2.2), 10.0))
    for label, bump, mscale, srad in cases:
        reset_scene()
        mst = make_master("Model", scale=mscale)
        if bump is not None:
            bx, br, bh = bump
            mmn0, mmx0 = mfutil.world_bbox(mst)
            sb2 = sph_at(br, 0)
            sb2.data.transform(Matrix.Translation((bx, 0, mmx0.z - br + bh - 0.0)))
            md2 = mst.modifiers.new('u', 'BOOLEAN'); md2.operation = 'UNION'; md2.object = sb2
            mfutil.apply_all_modifiers(mst); mfutil.remove_object(sb2)
        rto = pipeline.build_mold_system(mst, _over(default_props(), box_style='POUR_BOX',
                                                    base_style='OPEN', wings=False,
                                                    sprue=True, sprue_radius=srad,
                                                    vent_count=0))
        hits, topv = throat_first_hits(rto)
        blocked = [h for h in hits if h is not None and h > topv.z - 1.0]
        check(not blocked,
              f"{label}: throat open at all 8 angles ({len(blocked)} blocked)")

    # A big center throat on a locally-narrow top (wide base, slim top) used to
    # float the neck above the dropped-away shell, opening a side gap. The spout
    # base now drops to the shell under the neck, so the parts stay watertight
    # single solids and the neck connects with NO daylight gap beside it.
    print("\n=== Big throat on a narrow top: no side gap ===")
    reset_scene()
    base = make_bulbs_master()                # not narrow enough; build a taper
    mfutil.remove_object(base)
    taper = sph_at(18, 0)
    for r, z in ((13, 18), (7, 31)):
        s = sph_at(r, 0); s.data.transform(Matrix.Translation((0, 0, z)))
        md = taper.modifiers.new('u', 'BOOLEAN'); md.operation = 'UNION'; md.object = s
        mfutil.apply_all_modifiers(taper); mfutil.remove_object(s)
    taper.name = "Model"
    rbt = pipeline.build_mold_system(taper, _over(default_props(), box_style='POUR_BOX',
                                                  base_style='OPEN', wings=False,
                                                  sprue=True, sprue_place='XY',
                                                  sprue_radius=10.0, vent_count=0))
    check(all(mfutil.part_is_valid(q)[0] for q in rbt["parts"]),
          "big center throat: parts are valid watertight solids")
    hits, topv = throat_first_hits(rbt)
    blocked = [h for h in hits if h is not None and h > topv.z - 1.0]
    check(not blocked, f"big center throat: throat open ({len(blocked)} blocked)")
    # No daylight gap beside the neck: a downward ray just OUTSIDE the neck wall,
    # over the shell shoulder, must hit shell (not fall straight through a gap).
    fx = rbt["funnels"][0]["x"] if rbt.get("funnels") else topv.x
    nr = (rbt["funnels"][0]["neck_out"] + 1.5) if rbt.get("funnels") else 12.0
    fell_through = 0
    for k in range(12):
        a = 2.0 * math.pi * k / 12.0
        px, py = topv.x + math.cos(a) * nr, topv.y + math.sin(a) * nr
        first = None
        for q in rbt["parts"]:
            mw = q.matrix_world; iq = mw.inverted()
            d = (iq.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized()
            hit, loc, _n, _i = q.ray_cast(iq @ Vector((px, py, topv.z + 60.0)), d)
            if hit:
                z = (mw @ loc).z
                first = z if first is None else max(first, z)
        if first is None or first < topv.z - 12.0:
            fell_through += 1
    check(fell_through <= 1,
          f"no daylight gap beside the neck ({fell_through}/12 rays fell through)")

    # --- Wings RUN UP the funnel: at funnel height the winged mold reaches wider
    # along the seam axis than the bare funnel mouth does (the flange continues up
    # the spout), so there's no empty wedge between the body wings and the funnel.
    print("\n=== Wings run up the funnel ===")

    def hext(parts, cx, z):                      # max |x - cx| of geometry near height z
        m = 0.0
        for q in parts:
            mw = q.matrix_world
            for v in q.data.vertices:
                w = mw @ v.co
                if z - 1.0 <= w.z <= z + 1.0:
                    m = max(m, abs(w.x - cx))
        return m

    fover = dict(box_style='POUR_BOX', base_style='OPEN', split_axis='Y',
                 contoured=False, sprue=True, sprue_place='XY', vent_count=0,
                 sprue_radius=6.0, funnel_height=18.0)
    reset_scene()
    rfw = pipeline.build_mold_system(make_master("Model"), _over(default_props(), wings=True, **fover))
    pos = rfw["positive"]; pmn, pmx = mfutil.world_bbox(pos)
    cx = (pmn.x + pmx.x) * 0.5
    zf = pmx.z + 4.0                             # just up into the funnel
    w_with = hext(rfw["parts"], cx, zf)
    reset_scene()
    rfn = pipeline.build_mold_system(make_master("Model"), _over(default_props(), wings=False, **fover))
    w_without = hext(rfn["parts"], cx, zf)
    check(w_with > w_without + 3.0,
          f"wings reach up the funnel ({w_with:.1f} vs bare funnel {w_without:.1f})")

    # --- Wings must CONTOUR the body, not be square slabs: on a bulbed model the
    # mold's width at the wing seam should vary with height (pinch at waists,
    # bulge at bulbs). A square wing would be near-constant.
    print("\n=== Wings contour the body (not square) ===")
    reset_scene()
    rwc = pipeline.build_mold_system(make_bulbs_master(),
                                     _over(default_props(), box_style='POUR_BOX',
                                           base_style='OPEN', split_axis='Y', wings=True,
                                           wing_width=8.0, contoured=False, sprue=False,
                                           vent_count=0))
    parts = rwc["parts"]
    cmn, cmx = combined_bbox(parts)
    cx = (cmn.x + cmx.x) * 0.5
    z0, z1 = cmn.z + 3.0, cmx.z - 3.0           # skip the very ends
    prof = []
    for i in range(24):
        zlo = z0 + (z1 - z0) * i / 24.0
        zhi = z0 + (z1 - z0) * (i + 1) / 24.0
        half = 0.0
        for q in parts:
            mw = q.matrix_world
            for v in q.data.vertices:
                w = mw @ v.co
                if zlo <= w.z < zhi:
                    half = max(half, abs(w.x - cx))
        if half > 0.0:
            prof.append(half)
    span = max(prof) - min(prof) if prof else 0.0
    check(span > 4.0,
          f"wing width varies with height (contours): span {span:.1f} mm across the body")
    # gap, so the preview has to be cut by the shell (no interpenetration lip).
    print("\n=== Skin preview matches the real pour (no funnel lip) ===")
    reset_scene()
    rsk = pipeline.build_mold_system(make_master("Model"),
                                     _over(default_props(), box_style='POUR_BOX',
                                           base_style='OPEN', wings=True,
                                           sprue=True, vent_count=0))
    sk = rsk.get("skin")
    check(sk is not None, "skin preview produced")
    if sk is not None:
        coll2 = bpy.data.collections.get("MoldForge")
        clash = 0.0
        for q in rsk["parts"]:
            probe = mfutil.duplicate_object(sk, "MF_skclash", coll2)
            mfutil.boolean(probe, q, 'INTERSECT')
            if probe.data.polygons:
                clash += abs(volume.mesh_volume(probe))
            mfutil.remove_object(probe)
        check(clash < 1.0,
              f"skin does not interpenetrate the shell (overlap {clash:.2f} u³)")

    # --- A centred funnel over a dip must clear the NEIGHBOURING bumps: the
    # bore footprint may not slice into the model anywhere (point-in-mesh probe).
    print("\n=== Centred funnel clears neighbouring bumps ===")

    def inside_master(master, w):
        mw = master.matrix_world
        inv = mw.inverted()
        d = (inv.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
        cur = Vector(w) + Vector((0.0, 0.0, 1e-3))
        n = 0
        for _ in range(24):
            hit, loc, _nr, _i = master.ray_cast(inv @ cur, d)
            if not hit:
                break
            n += 1
            cur = (mw @ loc) + Vector((0.0, 0.0, 1e-3))
        return n % 2 == 1

    def sph_at(r, x):
        bm2 = bmesh.new()
        bmesh.ops.create_icosphere(bm2, subdivisions=3, radius=r)
        me2 = bpy.data.meshes.new("p"); bm2.to_mesh(me2); bm2.free()
        o2 = bpy.data.objects.new("p", me2)
        o2.data.transform(Matrix.Translation((x, 0, 0)))
        bpy.context.scene.collection.objects.link(o2)
        return o2

    reset_scene()
    tw = sph_at(9, -8)
    other = sph_at(9, 8)
    md = tw.modifiers.new('u', 'BOOLEAN'); md.operation = 'UNION'; md.object = other
    mfutil.apply_all_modifiers(tw); mfutil.remove_object(other)
    tw.name = "Model"
    rtw = pipeline.build_mold_system(tw, _over(default_props(), box_style='POUR_BOX',
                                               base_style='OPEN', wings=False,
                                               sprue=True, sprue_place='XY',
                                               vent_count=0))
    pos = rtw["positive"]
    pmn, pmx = mfutil.world_bbox(pos)
    cx, cy = (pmn.x + pmx.x) * 0.5, (pmn.y + pmx.y) * 0.5
    intruders = 0
    for q in rtw["parts"]:
        mw = q.matrix_world
        for v in q.data.vertices:
            w = mw @ v.co
            if (math.hypot(w.x - cx, w.y - cy) < 10.0
                    and pmx.z - 10.0 < w.z < pmx.z + 2.0
                    and inside_master(pos, w)):
                intruders += 1
    check(intruders == 0,
          f"centred funnel bore does not cut into the bumps ({intruders} intruding verts)")

    # --- Horizontal split: top/bottom stacks with a bolted mating ring
    print("\n=== Horizontal split ===")

    def stack_counts(parts, hz):
        above = below = 0
        for p in parts:
            mn, mx = mfutil.world_bbox(p)
            if (mn.z + mx.z) * 0.5 > hz:
                above += 1
            else:
                below += 1
        return above, below

    reset_scene()
    rhp = pipeline.build_mold_system(make_master("Model"),
                                     _over(default_props(), box_style='POUR_BOX',
                                           base_style='OPEN', wings=True,
                                           split_horizontal=True))
    check(len(rhp["parts"]) == 4, f"horizontal split: 4 stacked pieces ({len(rhp['parts'])})")
    check(all(mfutil.part_is_valid(p)[0] for p in rhp["parts"]),
          "horizontal split: all pieces valid")
    names = sorted(p.name for p in rhp["parts"])
    check(any("_Top" in n for n in names) and any("_Bot" in n for n in names),
          f"horizontal split: pieces named Top/Bot ({names})")
    cmn, cmx = combined_bbox(rhp["parts"])
    above, below = stack_counts(rhp["parts"], (cmn.z + cmx.z) * 0.5)
    check(above == 2 and below == 2, f"horizontal split: 2 above + 2 below ({above}/{below})")
    # The mating ring must widen the mold at the seam vs the same build without it.
    reset_scene()
    rnp = pipeline.build_mold_system(make_master("Model"),
                                     _over(default_props(), box_style='POUR_BOX',
                                           base_style='OPEN', wings=False,
                                           split_horizontal=False))
    def footprint_xy(parts):
        mn, mx = combined_bbox(parts)
        return max(mx.x - mn.x, mx.y - mn.y)   # the ring widens x/y, not height

    plain_w = footprint_xy(rnp["parts"])
    reset_scene()
    rhw = pipeline.build_mold_system(make_master("Model"),
                                     _over(default_props(), box_style='POUR_BOX',
                                           base_style='OPEN', wings=False,
                                           split_horizontal=True))
    ring_w = footprint_xy(rhw["parts"])
    check(ring_w > plain_w + 4.0,
          f"horizontal seam carries a flange ring ({ring_w:.1f} > {plain_w:.1f})")
    # Radial + horizontal: 3 wedges x 2 stacks = 6 valid pieces.
    reset_scene()
    rh6 = pipeline.build_mold_system(make_master("Model"),
                                     _over(default_props(), box_style='POUR_BOX',
                                           base_style='OPEN', wings=True,
                                           parts_count=3, split_horizontal=True))
    check(len(rh6["parts"]) == 6 and all(mfutil.part_is_valid(p)[0] for p in rh6["parts"]),
          f"radial 3 + horizontal: 6 valid pieces ({len(rh6['parts'])})")

    # --- Printer Fit: a shell taller than the printer's build height is cut into
    # stacked levels, EACH under the limit, with a bolted mating ring at every seam.
    print("\n=== Printer Fit (Max Print Height) ===")

    def _tall_cyl(name, r=13.0, depth=170.0):
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, segments=48, radius1=r, radius2=r,
                              depth=depth)
        m = bpy.data.meshes.new(name); bm.to_mesh(m); bm.free()
        o = bpy.data.objects.new(name, m); bpy.context.scene.collection.objects.link(o)
        return o

    reset_scene()
    pf = pipeline.build_mold_system(_tall_cyl("TallPF"), _over(default_props(),
            box_style='POUR_BOX', base_style='FLAT', wings=True, parts_count=2,
            contoured=False, printer_fit=True, max_print_height=80.0))
    pf_shells = [q for q in pf["parts"] if q.name.startswith("MF_Mold_")]
    check(pf.get("stack_levels") == 3 and len(pf_shells) == 6,
          f"printer fit 80 mm on a ~190 mm mold: 2 halves x 3 levels "
          f"({pf.get('stack_levels')} levels, {len(pf_shells)} shell pieces)")
    spans = sorted(mfutil.world_bbox(q)[1].z - mfutil.world_bbox(q)[0].z
                   for q in pf["parts"])
    check(spans[-1] <= 75.0 + 0.6,
          f"every level fits 80 mm MINUS the 5 mm support reserve "
          f"(tallest {spans[-1]:.1f} <= 75 mm)")
    check(all(mfutil.part_is_valid(q)[0] for q in pf["parts"]),
          "printer fit: all stacked levels are valid solids")
    pfn = sorted(q.name for q in pf["parts"])
    check(any(n.endswith("_S1") for n in pfn) and any(n.endswith("_S3") for n in pfn)
          and not any("_Top" in n for n in pfn),
          f"3+ levels numbered bottom-up _S1.._S3 ({pfn})")
    check(not pf.get("stack_capped") and not pf.get("stack_over"),
          "printer fit: no cap/overflow on a clean fit")

    # The POSITIVE is a print too: the 170 mm master must come out as glue-up
    # sections under the limit, with internal dowel sockets across each seam
    # (never anything on its OUTSIDE - the cast forms against that surface).
    psec = pf.get("positive_parts") or []
    pspans = [mfutil.world_bbox(q)[1].z - mfutil.world_bbox(q)[0].z for q in psec]
    check(pf.get("positive_sections") == 3 and len(psec) == 3
          and pf.get("positive_over") is False,
          f"the 170 mm positive is cut into 3 sections too "
          f"({pf.get('positive_sections')}, over={pf.get('positive_over')})")
    check(max(pspans) <= 75.6 and all(mfutil.part_is_valid(q)[0] for q in psec),
          f"positive sections valid and under the limit (tallest {max(pspans):.1f})")
    check(pf.get("positive_keys", 0) >= 4,
          f"alignment keys added across the positive seams "
          f"({pf.get('positive_keys')})")
    # Dowels are INTERNAL: no section may be wider than the master's footprint
    # (26 mm cylinder + a hair) - nothing telegraphs to the cast surface.
    pw = max(max(mfutil.world_bbox(q)[1].x - mfutil.world_bbox(q)[0].x,
                 mfutil.world_bbox(q)[1].y - mfutil.world_bbox(q)[0].y)
             for q in psec)
    check(pw <= 26.0 + 0.4,
          f"positive sections stay exactly the model's width ({pw:.1f} <= 26)")
    psn = sorted(q.name for q in psec)
    check(all(n.startswith("MF_Positive") for n in psn) and psn[0].endswith("_S1"),
          f"positive sections named for the export filter ({psn})")
    # No loose hardware: pegs are printed ON the lower section's face and seat
    # into clearance sockets in the next. Assembled in place, a section's peg
    # tips reach ~6 mm past the seam into the section above.
    s_by = sorted(psec, key=lambda q: mfutil.world_bbox(q)[0].z)
    ov01 = (mfutil.world_bbox(s_by[0])[1].z
            - mfutil.world_bbox(s_by[1])[0].z)
    ov12 = (mfutil.world_bbox(s_by[1])[1].z
            - mfutil.world_bbox(s_by[2])[0].z)
    check(3.5 <= ov01 <= 8.0 and 3.5 <= ov12 <= 8.0,
          f"integral pegs reach past both seams ({ov01:.1f} / {ov12:.1f} mm)")
    check(not any(q.name == "MF_Positive_Pins" for q in pf["parts"]),
          "no separate pin plate anymore (keys are integral)")

    # A bolted mating ring must sit at EVERY seam (not just the manual one):
    # the outer wall bulges by ~wing width at each level boundary.
    bots = sorted({round(mfutil.world_bbox(q)[0].z, 1) for q in pf_shells})
    seams = bots[1:]
    cbb = combined_bbox(pf_shells)
    ccx, ccy = (cbb[0].x + cbb[1].x) * 0.5, (cbb[0].y + cbb[1].y) * 0.5

    def _outer_r_at(parts, theta, z):
        d = Vector((-math.cos(theta), -math.sin(theta), 0.0))
        o = Vector((ccx + 300.0 * math.cos(theta), ccy + 300.0 * math.sin(theta), z))
        best = None
        for q in parts:
            inv = q.matrix_world.inverted()
            hit, loc, _n, _i = q.ray_cast(inv @ o, (inv.to_3x3() @ d).normalized())
            if hit:
                w = q.matrix_world @ loc
                rr = math.hypot(w.x - ccx, w.y - ccy)
                best = rr if best is None else max(best, rr)
        return best

    ring_hits = 0
    for sz in seams:
        r_seam = _outer_r_at(pf_shells, 1.0, sz + 1.0)     # 1.0 rad: off wings & bolts
        r_mid = _outer_r_at(pf_shells, 1.0, sz + 14.0)
        if r_seam is not None and r_mid is not None and r_seam > r_mid + 3.0:
            ring_hits += 1
    check(len(seams) == 2 and ring_hits == 2,
          f"a bolted mating ring sits at every seam ({ring_hits}/{len(seams)} bulge)")

    # Under the limit -> untouched (no seams, no rings).
    reset_scene()
    pg = pipeline.build_mold_system(_tall_cyl("TallOK"), _over(default_props(),
            box_style='POUR_BOX', base_style='FLAT', wings=False, parts_count=2,
            contoured=False, printer_fit=True, max_print_height=500.0))
    check(pg.get("stack_levels") is None and len(pg["parts"]) == 2
          and pg.get("positive_sections") is None,
          "printer fit leaves molds under the limit untouched (positive whole)")

    # The checkbox is the gate: a typed height with Printer Fit UNTICKED does nothing.
    reset_scene()
    poff = pipeline.build_mold_system(_tall_cyl("TallOff"), _over(default_props(),
            box_style='POUR_BOX', base_style='FLAT', wings=False, parts_count=2,
            contoured=False, max_print_height=80.0))
    check(poff.get("stack_levels") is None and poff.get("positive_sections") is None
          and len(poff["parts"]) == 2,
          "Printer Fit unticked: a typed height alone does nothing")

    # Support Height is reserved OUT of the limit: same ~173 mm mold, same 100 mm
    # printer - a 5 mm reserve leaves 95 mm usable (2 levels), a 30 mm support
    # riser leaves only 70 mm usable and forces a third level.
    reset_scene()
    ps5 = pipeline.build_mold_system(_tall_cyl("TallS5", depth=150.0),
            _over(default_props(),
            box_style='POUR_BOX', base_style='FLAT', wings=False, parts_count=2,
            contoured=False, printer_fit=True, max_print_height=100.0, support_clearance=5.0))
    lv5 = ps5.get("stack_levels")
    reset_scene()
    ps30 = pipeline.build_mold_system(_tall_cyl("TallS30", depth=150.0),
            _over(default_props(),
            box_style='POUR_BOX', base_style='FLAT', wings=False, parts_count=2,
            contoured=False, printer_fit=True, max_print_height=100.0, support_clearance=30.0))
    lv30 = ps30.get("stack_levels")
    s30max = max(mfutil.world_bbox(q)[1].z - mfutil.world_bbox(q)[0].z
                 for q in ps30["parts"])
    check(lv5 == 2 and lv30 == 3 and s30max <= 70.6,
          f"support reserve shrinks the usable height (5 mm -> {lv5} levels, "
          f"30 mm -> {lv30} levels, tallest {s30max:.1f} <= 70)")

    # A typo-small limit (15 typed for a 150 mm printer, cm instead of mm) must
    # NOT shred the mold into confetti: the usable height is floored at 25 mm
    # and the build flags it so the summary can shout about the units.
    reset_scene()
    pt = pipeline.build_mold_system(_tall_cyl("TallTypo", depth=60.0),
            _over(default_props(),
            box_style='POUR_BOX', base_style='FLAT', wings=False, parts_count=2,
            contoured=False, printer_fit=True, max_print_height=15.0, support_clearance=5.0))
    tl = pt.get("stack_levels")
    tspans = [mfutil.world_bbox(q)[1].z - mfutil.world_bbox(q)[0].z
              for q in pt["parts"]]
    check(pt.get("stack_floored") is True and tl is not None and 3 <= tl <= 4
          and max(tspans) <= 25.6
          and all(mfutil.part_is_valid(q)[0] for q in pt["parts"]),
          f"typo-small 15 mm limit floors to 25 mm levels instead of confetti "
          f"({tl} levels, tallest {max(tspans):.1f}, floored={pt.get('stack_floored')})")

    # When the 8-level cap bites, the seam budget must be split FAIRLY across
    # the regions of a manual seam - the old greedy fill cut the bottom half
    # into bits and left the top half whole (pure math, no build needed).
    fz, fcap = pipeline._fit_seams(0.0, 380.0, [190.0], 25.0, z_hi=370.0)
    above = [z for z in fz if z > 190.1]
    below = [z for z in fz if z < 189.9]
    check(fcap and len(above) >= 2 and len(below) >= 2
          and abs(len(above) - len(below)) <= 1,
          f"capped seam budget splits fairly around a manual seam "
          f"({len(below)} below / {len(above)} above)")
    fz2, fcap2 = pipeline._fit_seams(0.0, 100.0, [], 40.0, z_hi=95.0)
    check(not fcap2 and len(fz2) == 2, f"uncapped fit unchanged ({len(fz2)} seams)")

    # Cut Positive Too = off: the positive stays whole and the too-tall note fires.
    reset_scene()
    pnc = pipeline.build_mold_system(_tall_cyl("TallNC", depth=100.0),
            _over(default_props(),
            box_style='POUR_BOX', base_style='FLAT', wings=False, parts_count=2,
            contoured=False, printer_fit=True, max_print_height=60.0, fit_positive=False))
    check(pnc.get("stack_levels") is not None
          and pnc.get("positive_sections") is None
          and pnc.get("positive_over") is True,
          "Cut Positive Too off: shells stacked, positive kept whole + warned")

    # A MESSY positive must still section - the user's case: a sculpt whose
    # plinth union falls back to the overlapping JOIN leaves the positive as two
    # nested shells (2 islands), and the old strict validity check then silently
    # rolled the cut back to a whole positive. Force the fallback deterministically
    # (solvers sometimes heal hand-made damage) and require sections anyway: a
    # messy source can never yield cleaner pieces than itself.
    reset_scene()
    from moldforge.core import build as mfbuild
    _orig_unite = mfbuild.unite_plinth

    def _force_join(positive, plinth, coll):
        mfutil.join_meshes(positive, mfutil.duplicate_object(plinth, "MF_pp", coll))
        return False
    mfbuild.unite_plinth = _force_join
    try:
        pmess = pipeline.build_mold_system(
            _lock_cone("LkMessy", r1=12.0, r2=9.0, depth=110.0),
            _over(default_props(), box_style='POUR_BOX', base_style='LOCK',
                  wings=False, parts_count=2, lock_height=10.0, lock_margin=4.0,
                  lock_teeth=3, lock_tooth_depth=2.0, lock_tolerance=0.3,
                  printer_fit=True, max_print_height=60.0))
    finally:
        mfbuild.unite_plinth = _orig_unite
    check(pmess.get("base_united") is False,
          "fallback-join positive really is the two-shell case (forced)")
    check(pmess.get("positive_sections") is not None
          and len(pmess.get("positive_parts") or []) >= 2,
          f"two-shell (join) positive still sections instead of silently "
          f"staying whole ({pmess.get('positive_sections')} sections)")

    # Printer Fit + Locking Base: stacked shells, socket intact, positive united.
    reset_scene()
    plk = pipeline.build_mold_system(_lock_cone("LkPF", r1=10.0, r2=8.0, depth=120.0),
            _over(default_props(), box_style='POUR_BOX', base_style='LOCK',
                  wings=False, parts_count=2, lock_height=10.0, lock_margin=4.0,
                  lock_teeth=3, lock_tooth_depth=2.0, lock_tolerance=0.3,
                  printer_fit=True, max_print_height=60.0))
    plk_shells = [q for q in plk["parts"] if q.name.startswith("MF_Mold_")]
    check(plk.get("stack_levels") == 3 and len(plk_shells) == 6
          and all(mfutil.part_is_valid(q)[0] for q in plk["parts"]),
          f"printer fit + Locking Base: 6 valid stacked shells "
          f"({plk.get('stack_levels')} levels, {len(plk_shells)} shell pieces)")
    lspans = [mfutil.world_bbox(q)[1].z - mfutil.world_bbox(q)[0].z
              for q in plk["parts"]]
    check(max(lspans) <= 55.6,
          f"lock levels fit 60 mm minus supports (tallest {max(lspans):.1f} <= 55)")
    check(plk.get("base_united") is True,
          "printer fit + lock: positive still boolean-united")
    # The united positive (120 model + 10 base = 130 mm) is sectioned too, and
    # the bottom section keeps the WHOLE sawtooth plinth (seams stay above it).
    lsec = plk.get("positive_parts") or []
    lsp = [mfutil.world_bbox(q)[1].z - mfutil.world_bbox(q)[0].z for q in lsec]
    lbot = min(lsec, key=lambda q: mfutil.world_bbox(q)[0].z) if lsec else None
    check(plk.get("positive_sections") == 3 and max(lsp) <= 55.6
          and all(mfutil.part_is_valid(q)[0] for q in lsec),
          f"lock positive cut into 3 valid sections (tallest {max(lsp):.1f})")
    check(lbot is not None
          and (mfutil.world_bbox(lbot)[1].z - mfutil.world_bbox(lbot)[0].z) > 13.0,
          "bottom positive section keeps the whole plinth (no seam through teeth)")

    # --- Manual placement markers: vents drilled EXACTLY at the user's Vent
    # Markers (Vent Count ignored), and Pour Markers add extra spouts at their
    # X/Y while the FIRST spout keeps the Placement logic.
    print("\n=== Vent & Pour markers ===")
    reset_scene()
    mcoll = bpy.data.collections.get(mfutil.MARKER_COLLECTION)
    if mcoll is None:
        mcoll = bpy.data.collections.new(mfutil.MARKER_COLLECTION)

    def _mark(name, loc):
        em = bpy.data.objects.new(name, None)
        em.location = loc
        mcoll.objects.link(em)
        return em

    def _drop_z(parts, x, y):
        """First surface hit casting straight DOWN at (x, y) from above."""
        top = max(mfutil.world_bbox(q)[1].z for q in parts)
        best = None
        for q in parts:
            inv = q.matrix_world.inverted()
            hit, loc, _n, _i = q.ray_cast(
                inv @ Vector((x, y, top + 30.0)),
                (inv.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized())
            if hit:
                wz = (q.matrix_world @ loc).z
                best = wz if best is None else max(best, wz)
        return best, top

    def _wide_cyl_m(name):
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, segments=64, radius1=30.0,
                              radius2=30.0, depth=40.0)
        m = bpy.data.meshes.new(name); bm.to_mesh(m); bm.free()
        o = bpy.data.objects.new(name, m)
        bpy.context.scene.collection.objects.link(o)
        return o

    # Wide flat-topped cylinder (r30, z -20..20): model top 20, gap void 20..23,
    # jacket top face 25, funnel apex higher. Markers sit ON the model top, well
    # clear of the centre funnel mouth (~10).
    _mark("MF_VentMark", (20.0, 0.0, 19.5))
    _mark("MF_VentMark.001", (-20.0, 0.0, 19.5))
    _mark("MF_PourMark", (0.0, 20.0, 19.5))
    mk = pipeline.build_mold_system(_wide_cyl_m("MkM"), _over(default_props(),
            box_style='POUR_BOX', base_style='FLAT', wings=False, parts_count=2,
            contoured=False, split_axis='X', sprue_place='XY', sprue_count=1,
            vent_place='MARKERS', vent_count=0))
    bpy.context.evaluated_depsgraph_get()   # flush: ray_cast answers stale state otherwise
    z1, _t = _drop_z(mk["parts"], 20.0, 0.0)
    z2, _t = _drop_z(mk["parts"], -20.0, 0.0)
    zc, _t = _drop_z(mk["parts"], 24.0, -24.0)
    check(z1 is not None and z1 < 23.0 and z2 is not None and z2 < 23.0,
          f"vents drilled at BOTH markers, into the gap void (first hits "
          f"{z1:.1f} / {z2:.1f} < jacket top 25) despite Vent Count 0")
    check(zc is not None and zc > 24.0,
          f"no vent where no marker (control point solid at {zc:.1f})")
    zp, _t = _drop_z(mk["parts"], 0.9, 20.0)   # 0.9 off the x=0 parting plane
    zf, _t = _drop_z(mk["parts"], 0.9, 0.0)
    check(zp is not None and zp < 23.5,
          f"pour marker adds an extra spout at its X/Y (bore down to {zp:.1f})")
    check(zf is not None and zf < 23.5,
          "the FIRST spout still follows Placement (centre bore present)")

    # A SIDE marker at mid-height (the reported bug): the spout must stand ON
    # the mold above it and the channel must bore ALL the way down to the marker,
    # not stop at the model's local top under that column (the old behaviour left
    # the spout floating in air with a short, useless bore).
    for o in list(mcoll.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    reset_scene()
    mcoll = bpy.data.collections.get(mfutil.MARKER_COLLECTION)   # reset purges all
    if mcoll is None:
        mcoll = bpy.data.collections.new(mfutil.MARKER_COLLECTION)
    _mark("MF_PourMark", (14.0, 0.0, 0.0))       # on the wall, half-way up
    bmt = bmesh.new()
    bmesh.ops.create_cone(bmt, cap_ends=True, segments=48, radius1=14.0,
                          radius2=14.0, depth=60.0)
    mt = bpy.data.meshes.new("SideM"); bmt.to_mesh(mt); bmt.free()
    ot = bpy.data.objects.new("SideM", mt)
    bpy.context.scene.collection.objects.link(ot)
    sk = pipeline.build_mold_system(ot, _over(default_props(),
            box_style='POUR_BOX', base_style='FLAT', wings=False, parts_count=2,
            contoured=False, split_axis='X', sprue_place='XY', sprue_count=1,
            vent_count=0))
    bpy.context.evaluated_depsgraph_get()   # flush: ray_cast answers stale state otherwise
    # The runner: a tube up the OUTSIDE of the shell (never a trench through the
    # thin wall), cup above the top, and a radial gate into the gap at the marker.
    tube_verts = 0
    for q in sk["parts"]:
        mwq = q.matrix_world
        for v in q.data.vertices:
            w = mwq @ v.co
            if w.x > 21.0 and abs(w.y) < 9.0 and -8.0 < w.z < 30.0:
                tube_verts += 1
    check(tube_verts > 50,
          f"external runner tube climbs the shell outside ({tube_verts} verts "
          f"beyond the wall)")

    def _hx(parts, y, z, sign):
        """First hit X casting horizontally (along -sign*X) from outside."""
        best = None
        for q in parts:
            inv = q.matrix_world.inverted()
            hit, loc, _n, _i = q.ray_cast(
                inv @ Vector((0.0, y, z)),
                (inv.to_3x3() @ Vector((sign, 0.0, 0.0))).normalized())
            if hit:
                wx = (q.matrix_world @ loc).x
                if best is None or abs(wx) < abs(best):
                    best = wx
        return best
    gx = _hx(sk["parts"], 0.0, 0.0, +1.0)      # from cavity centre out through the gate
    cxx = _hx(sk["parts"], 0.0, 0.0, -1.0)     # opposite side: solid wall
    check(gx is not None and gx > 20.0,
          f"the radial gate is OPEN through the wall at the marker "
          f"(first hit at x={gx:.1f}, past the shell)")
    check(cxx is not None and abs(cxx) < 20.0,
          f"opposite wall stays solid (first hit at x={cxx:.1f})")
    zt, _t = _drop_z(sk["parts"], 23.8, 0.0)
    check(zt is None or zt < 10.0,
          f"pour path runs from the top cup down the tube to the marker "
          f"(vertical bore open, first hit {zt})")

    # The USER's failing shape: fat bottom bulge, strong neck, smaller top bulge,
    # marker on the bulge - and a BIG Throat Radius (the pipe must stay slim).
    for o in list(mcoll.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    reset_scene()
    mcoll = bpy.data.collections.get(mfutil.MARKER_COLLECTION)
    if mcoll is None:
        mcoll = bpy.data.collections.new(mfutil.MARKER_COLLECTION)
    def _bulgy(name):
        bmb = bmesh.new()
        segs, rings, H = 48, 48, 100.0
        grid = []
        for j in range(rings + 1):
            t = j / rings
            z = H * t
            rr = (12.0 + 14.0 * math.exp(-((t - 0.18) / 0.22) ** 2)
                  + 7.0 * math.exp(-((t - 0.75) / 0.2) ** 2))
            ring = []
            for i in range(segs):
                a = 2 * math.pi * i / segs
                ring.append(bmb.verts.new((rr * math.cos(a), rr * math.sin(a), z)))
            grid.append(ring)
        for j in range(rings):
            for i in range(segs):
                k2 = (i + 1) % segs
                bmb.faces.new((grid[j][i], grid[j][k2],
                               grid[j + 1][k2], grid[j + 1][i]))
        bmb.faces.new(list(reversed(grid[0]))); bmb.faces.new(grid[rings])
        bmesh.ops.recalc_face_normals(bmb, faces=bmb.faces[:])
        mb = bpy.data.meshes.new(name); bmb.to_mesh(mb); bmb.free()
        o = bpy.data.objects.new(name, mb)
        bpy.context.scene.collection.objects.link(o)
        return o

    _mark("MF_PourMark", (25.8, 0.0, 18.0))
    bk = pipeline.build_mold_system(_bulgy("Bulgy"), _over(default_props(),
            box_style='POUR_BOX', base_style='FLAT', wings=False, parts_count=2,
            contoured=False, split_axis='X', sprue_place='XY', sprue_count=1,
            sprue_radius=12.0, vent_count=0))
    bpy.context.evaluated_depsgraph_get()   # flush: ray_cast answers stale state otherwise
    check(all(mfutil.part_is_valid(q)[0] for q in bk["parts"]),
          "bulgy + big-throat: halves valid with the filler pipe")

    def _hx2(parts, y, z, sign):
        best = None
        for q in parts:
            inv = q.matrix_world.inverted()
            hit, loc, _n, _i = q.ray_cast(
                inv @ Vector((0.0, y, z)),
                (inv.to_3x3() @ Vector((sign, 0.0, 0.0))).normalized())
            if hit:
                wx = (q.matrix_world @ loc).x
                if best is None or abs(wx) < abs(best):
                    best = wx
        return best
    bg = _hx2(bk["parts"], 0.0, 18.0, +1.0)
    bc = _hx2(bk["parts"], 0.0, 18.0, -1.0)
    check(bg is not None and bg > 32.0,
          f"bulgy: gate open through the bulge wall (first hit x={bg:.1f})")
    check(bc is not None and abs(bc) < 32.0,
          f"bulgy: opposite wall solid (x={bc:.1f})")
    zb, _t = _drop_z(bk["parts"], 35.0, 0.0)
    check(zb is None or zb < 22.0,
          f"bulgy: pipe channel open from the cup down to the marker "
          f"(first hit {zb})")
    bt = 0
    for q in bk["parts"]:
        mwq = q.matrix_world
        for v in q.data.vertices:
            w = mwq @ v.co
            if w.x > 32.0 and abs(w.y) < 8.0:
                bt += 1
    check(bt > 50, f"bulgy: slim pipe welded outside the bulge ({bt} verts)")
    # Brace struts tie the pipe to the wall where the body narrows above the
    # bulge (the pipe must not be a fragile cantilever): geometry must exist in
    # the corridor between the wall and the pipe at mid heights.
    brace_v = 0
    for q in bk["parts"]:
        mwq = q.matrix_world
        for v in q.data.vertices:
            w = mwq @ v.co
            if 22.0 < w.x < 29.0 and abs(w.y) < 5.0 and 40.0 < w.z < 80.0:
                brace_v += 1
    check(brace_v > 10,
          f"brace struts bridge the standoff above the bulge ({brace_v} verts)")

    # The user's EXACT panel: Locking Base + wings + contoured + OVERSIZED
    # Throat 20 + marker low on the bulge near the primary column. Three past
    # failures live here: the Throat-scaled keep-out silently swallowed the
    # marker; the near-top branch built a coreable spout on the low shoulder;
    # and a gate could open into the socket band.
    for o in list(mcoll.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    reset_scene()
    mcoll = bpy.data.collections.get(mfutil.MARKER_COLLECTION)
    if mcoll is None:
        mcoll = bpy.data.collections.new(mfutil.MARKER_COLLECTION)
    _mark("MF_PourMark", (0.0, 25.0, 22.0))
    lkp = pipeline.build_mold_system(_bulgy("BulgyL"), _over(default_props(),
            box_style='POUR_BOX', base_style='LOCK', wings=True, parts_count=2,
            contoured=True, split_axis='Y', sprue_place='TOP', sprue_count=1,
            sprue_radius=20.0, big_throat=True, vent_count=0,
            lock_height=10.0, lock_margin=4.0, lock_teeth=3,
            lock_tooth_depth=2.0, lock_tolerance=0.2))
    bpy.context.evaluated_depsgraph_get()
    check(all(mfutil.part_is_valid(q)[0] for q in lkp["parts"]),
          "user config (LOCK + oversized throat + bulge marker): halves valid")
    hi_v = lo_v = 0
    for q in lkp["parts"]:
        mwq = q.matrix_world
        for v in q.data.vertices:
            w = mwq @ v.co
            if abs(w.x) < 12.0 and w.y > 30.0:
                if w.z > 95.0:
                    hi_v += 1
    check(hi_v > 60,
          f"marker not swallowed by the throat keep-out: pipe + cup reach the "
          f"top ({hi_v} verts high)")
    gb = None
    for q in lkp["parts"]:
        inv = q.matrix_world.inverted()
        hit, loc, _n, _i = q.ray_cast(
            inv @ Vector((0.0, 0.0, 22.0)),
            (inv.to_3x3() @ Vector((0.0, 1.0, 0.0))).normalized())
        if hit:
            wy = (q.matrix_world @ loc).y
            if gb is None or abs(wy) < abs(gb):
                gb = wy
    check(gb is not None and gb > 32.0,
          f"gate open through the wall above the socket (first hit y={gb:.1f})")
    for o in list(mcoll.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for o in list(mcoll.objects):
        bpy.data.objects.remove(o, do_unlink=True)

    # --- The "uncut line across the bottom of the shell": a model with a HOLLOW
    # underside (a cap standing on its rim) touches the plinth only along the rim,
    # so the socket is rim-sized and an air pocket sits above the socket top under
    # the arch. The clamp-wing rind is dilated offset+width from the model, and
    # under the arch that dilation points DOWN into the pocket: clipped to the seam
    # slab it became a straight bar bridging the socket mouth along the parting
    # line, flush with the socket top - outside the silicone gap AND the socket, so
    # no cutter touched it. The wings now avoid the pocket. Point-in-solid probes
    # (a vertex scan misses it: the bar is a few big faces).
    print("\n=== Locking Base: clamp wings never bar the socket pocket ===")
    reset_scene()
    prof = [(0.0, 22.0), (10.0, 21.5), (20.0, 19.5), (30.0, 15.5), (38.0, 10.0),
            (43.0, 5.0), (45.0, 2.0), (45.0, 0.0), (39.0, 0.0),
            (38.0, 1.5), (35.0, 4.5), (30.0, 8.5), (22.0, 12.0), (12.0, 14.5), (0.0, 15.5)]
    bmc = bmesh.new()
    ringsc = []
    for (rr, zz) in prof:
        if rr < 1e-6:
            ringsc.append([bmc.verts.new((0.0, 0.0, zz))] * 72)
        else:
            ringsc.append([bmc.verts.new((rr * math.cos(2 * math.pi * i / 72),
                                          rr * math.sin(2 * math.pi * i / 72), zz))
                           for i in range(72)])
    for k in range(len(prof) - 1):
        for i in range(72):
            quad = []
            for v in (ringsc[k][i], ringsc[k][(i + 1) % 72],
                      ringsc[k + 1][(i + 1) % 72], ringsc[k + 1][i]):
                if v not in quad:
                    quad.append(v)
            if len(quad) >= 3:
                bmc.faces.new(quad)
    bmesh.ops.recalc_face_normals(bmc, faces=bmc.faces[:])
    mc_ = bpy.data.meshes.new("Cap"); bmc.to_mesh(mc_); bmc.free()
    oc_ = bpy.data.objects.new("Cap", mc_)
    bpy.context.scene.collection.objects.link(oc_)
    capres = pipeline.build_mold_system(oc_, _over(default_props(),
            box_style='POUR_BOX', base_style='LOCK', wings=True, wing_width=10.0,
            parts_count=2, contoured=True, split_axis='Y', sprue_place='TOP',
            vent_count=0, wall_thickness=3.0, sprue_radius=20.0, big_throat=True,
            bolt_auto=True, lock_height=10.0, lock_margin=4.0, lock_teeth=3,
            lock_tooth_depth=2.0, lock_tolerance=0.2, dual_density=True, core_wall=10.0))
    bpy.context.evaluated_depsgraph_get()

    def solid_at(obj, w):
        inv = obj.matrix_world.inverted()
        d = (inv.to_3x3() @ Vector((1.0, 0.0, 0.0))).normalized()
        cur = inv @ Vector(w)
        n = 0
        for _ in range(200):
            hit, loc, _nr, _i = obj.ray_cast(cur, d)
            if not hit:
                break
            n += 1
            cur = loc + d * 1e-3
        return n % 2 == 1

    def underside(r):
        pts = prof[8:]
        for (r0, z0), (r1, z1) in zip(pts, pts[1:]):
            if r1 <= r <= r0:
                return z0 + (z1 - z0) * (r0 - r) / (r0 - r1)
        return 15.5

    capshells = {q.name: q for q in capres["parts"] if q.name in ("MF_Mold_A", "MF_Mold_B")}
    cbase = mfutil.world_bbox(capres["positive"])[0].z + 10.0     # the model's base plane
    ctop = cbase + 2.0                                            # socket top (weld band)
    bar_hits, bar_total = 0, 0
    for name, q in capshells.items():
        yy = 0.6 if name.endswith("A") else -0.6
        for xx in range(-24, 25, 6):
            for dz in (1.0, 2.5, 4.0):
                zz = ctop + dz
                if zz > cbase + underside(abs(xx)) - 5.0 - 1.0:
                    continue                                      # that's the jacket's own wall
                bar_total += 1
                bar_hits += solid_at(q, (float(xx), yy, zz))
    check(len(capshells) == 2 and bar_total >= 40 and bar_hits == 0,
          f"no wing bar across the socket mouth under a hollow underside "
          f"({bar_hits}/{bar_total} seam probes solid in the pocket)")
    cw = max(mfutil.world_bbox(q)[1].x - mfutil.world_bbox(q)[0].x for q in capshells.values())
    fl_ok = all(any(solid_at(capshells["MF_Mold_A"], (sx, 0.6, cbase + zz)) for zz in (3.0, 9.0))
                for sx in (55.0, -55.0))
    check(cw >= 118.0 and fl_ok,
          f"the clamp flange outside the mouth is intact (shell width {cw:.1f}, flange probes {fl_ok})")
    ceil_ok = solid_at(capshells["MF_Mold_A"], (0.0, 0.6, cbase + underside(0.0) - 4.0))
    check(ceil_ok, "the shell's own ceiling under the arch is still there")
    check(all(mfutil.part_is_valid(q)[0] for q in capres["parts"]),
          "hollow-underside Locking Base parts are all valid")

    # --- The user's ACTUAL bar (their STL, flat base): a 2 mm fin hanging under
    # the shell bottom along the seam across the whole socket mouth. The clamp-wing
    # slab overhung the mold bottom by 2 mm, the rind reaches far below the base,
    # and only the final bottom cut removed the fin - a cut the fast boolean solver
    # can silently refuse on some machines, leaving the fin in the shipped shells.
    # Now (a) the slab never reaches below the bottom, so there is no fin even if
    # that cut does nothing, and (b) the cut verifies itself and retries EXACT.
    print("\n=== Locking Base: no fin under the shell along the seam ===")
    from moldforge.core import build as mfbuild
    fin_cfg = dict(box_style='POUR_BOX', base_style='LOCK', wings=True, wing_width=10.0,
                   parts_count=2, contoured=True, split_axis='Y', sprue_place='TOP',
                   vent_count=0, wall_thickness=3.0, sprue_radius=20.0, big_throat=True,
                   bolt_auto=True, lock_height=10.0, lock_margin=4.0, lock_teeth=3,
                   lock_tooth_depth=2.0, lock_tolerance=0.2)

    def fin_probe(res):
        """Solid probes half a millimetre below the plinth bottom, across the mouth."""
        shells = [q for q in res["parts"] if q.name in ("MF_Mold_A", "MF_Mold_B")]
        pmn, _pmx = mfutil.world_bbox(res["positive"])
        pos = res["positive"]
        mw = pos.matrix_world
        band = [mw @ v.co for v in pos.data.vertices if (mw @ v.co).z < pmn.z + 9.0]
        bx0, bx1 = min(w.x for w in band), max(w.x for w in band)
        by0, by1 = min(w.y for w in band), max(w.y for w in band)
        hits = 0
        for i in range(21):
            xx = bx0 + 5.0 + (bx1 - bx0 - 10.0) * i / 20
            for yy in (-1.2, -0.4, 0.4, 1.2):
                if by0 + 5.0 < yy < by1 - 5.0:
                    hits += any(solid_at(q, (xx, yy, pmn.z - 0.5)) for q in shells)
        lowest = min(mfutil.world_bbox(q)[0].z for q in shells)
        return hits, lowest - pmn.z

    # (a) the bottom cut does NOTHING (simulating the silent refusal): still no fin
    reset_scene()
    real_cut = mfbuild.cut_below_z
    mfbuild.cut_below_z = lambda *a, **k: None
    try:
        fr = pipeline.build_mold_system(make_master("FinBlob"), _over(default_props(), **fin_cfg))
    finally:
        mfbuild.cut_below_z = real_cut
    bpy.context.evaluated_depsgraph_get()
    hits, below = fin_probe(fr)
    check(hits == 0 and below > -0.35,
          f"no wing fin under the shell along the seam even when the bottom cut is skipped "
          f"({hits} seam probes solid below the bottom; shell bottom {below:+.2f} mm vs plinth bottom)")

    # (b) the cut itself: with a Locking Base nothing is left below the plinth
    # bottom any more, so the cut is a no-op there; an Open Bottom mold still
    # cuts its floor off at the model's base. When the fast solver leaves the
    # mesh untouched, the cut must retry EXACT and the shells still end flush.
    reset_scene()
    real_bool = mfutil.boolean
    calls = {"basecut": 0}

    def flaky_boolean(obj, cutter, operation='DIFFERENCE', solver='MANIFOLD'):
        if cutter.name.startswith("MF_basecut") and solver == 'MANIFOLD':
            calls["basecut"] += 1
            return                                   # the silent refusal
        return real_bool(obj, cutter, operation, solver)

    open_cfg = dict(fin_cfg)
    open_cfg.update(base_style='OPEN', base_plate=False, suction_cup=False)
    mfutil.boolean = flaky_boolean
    try:
        fr2 = pipeline.build_mold_system(make_master("FinBlob2"), _over(default_props(), **open_cfg))
    finally:
        mfutil.boolean = real_bool
    bpy.context.evaluated_depsgraph_get()
    ob_shells = [q for q in fr2["parts"] if q.name in ("MF_Mold_A", "MF_Mold_B")]
    ob_base = mfutil.world_bbox(fr2["positive"])[0].z          # no plinth: the model's base
    ob_low = min(mfutil.world_bbox(q)[0].z for q in ob_shells) - ob_base
    ob_hits = sum(any(solid_at(q, (xx, yy, ob_base - 0.5)) for q in ob_shells)
                  for xx in (-6.0, -3.0, 0.0, 3.0, 6.0) for yy in (-1.0, 0.0, 1.0))
    check(calls["basecut"] >= 1 and ob_hits == 0 and abs(ob_low) < 0.05,
          f"bottom cut retries with EXACT when the fast solver refuses "
          f"({calls['basecut']} refusals; shell bottom {ob_low:+.2f} mm vs model base, {ob_hits} probes solid below)")
    check(all(mfutil.part_is_valid(q)[0] for q in fr2["parts"]),
          "parts valid after the EXACT bottom-cut retry")

    # --- Vent bores never leave paper blades (the "needle at the bottom of a
    # Locking Base" artifact): on a narrowing blob the vertical vent bore used
    # to graze the parting plane / the sloped outer wall and leave a ~0.1 mm
    # blade standing on the shell. Vents now nudge off the seam and any
    # surviving shard is shaved.
    print("\n=== Vent blade artifact (LOCK bottom needle) ===")
    reset_scene()
    bmv = bmesh.new()
    segs2, rings2, H2 = 48, 40, 70.0
    grid2 = []
    for j in range(rings2 + 1):
        t = j / rings2
        rr = (8.0 + 22.0 * math.sin(min(max((t - 0.15) / 0.85, 0.0), 1.0)
                                    * math.pi) ** 1.5 if t > 0.15 else 8.0)
        grid2.append([bmv.verts.new((rr * math.cos(2 * math.pi * i / segs2),
                                     rr * math.sin(2 * math.pi * i / segs2),
                                     H2 * t)) for i in range(segs2)])
    for j in range(rings2):
        for i in range(segs2):
            k2 = (i + 1) % segs2
            bmv.faces.new((grid2[j][i], grid2[j][k2],
                           grid2[j + 1][k2], grid2[j + 1][i]))
    bmv.faces.new(list(reversed(grid2[0]))); bmv.faces.new(grid2[rings2])
    bmesh.ops.recalc_face_normals(bmv, faces=bmv.faces[:])
    mv_ = bpy.data.meshes.new("BladeBlob"); bmv.to_mesh(mv_); bmv.free()
    ov_ = bpy.data.objects.new("BladeBlob", mv_)
    bpy.context.scene.collection.objects.link(ov_)
    vb = pipeline.build_mold_system(ov_, _over(default_props(),
            box_style='POUR_BOX', base_style='LOCK', wings=True, parts_count=2,
            contoured=True, split_axis='Y', sprue_place='TOP', sprue_count=1,
            sprue_radius=20.0, big_throat=True, vent_count=2,
            lock_height=10.0, lock_margin=4.0, lock_teeth=3,
            lock_tooth_depth=2.0, lock_tolerance=0.2))
    blades = 0
    for q in vb["parts"]:
        if not q.name.startswith("MF_Mold_"):
            continue
        bmq = bmesh.new(); bmq.from_mesh(q.data)
        bmq.faces.ensure_lookup_table()
        skinny = set()
        for f in bmq.faces:
            a = f.calc_area()
            e = max((ed.calc_length() for ed in f.edges), default=0.0)
            if a > 1e-9 and e > 2.0 and e * e / a > 150.0:
                skinny.add(f.index)
        seen2 = set()
        mwq = q.matrix_world
        for fi in skinny:
            if fi in seen2:
                continue
            stack2, comp = [fi], []
            while stack2:
                k = stack2.pop()
                if k in seen2:
                    continue
                seen2.add(k); comp.append(k)
                for ed in bmq.faces[k].edges:
                    for lf in ed.link_faces:
                        if lf.index in skinny and lf.index not in seen2:
                            stack2.append(lf.index)
            if len(comp) < 3:
                continue
            pts = [mwq @ v.co for k in comp for v in bmq.faces[k].verts]
            dims = sorted((max(p2.x for p2 in pts) - min(p2.x for p2 in pts),
                           max(p2.y for p2 in pts) - min(p2.y for p2 in pts),
                           max(p2.z for p2 in pts) - min(p2.z for p2 in pts)))
            if dims[0] < 0.45 and dims[2] > 3.0:
                blades += 1
        bmq.free()
    check(blades == 0 and all(mfutil.part_is_valid(q)[0] for q in vb["parts"]),
          f"vent bores leave no paper-thin blade on the shells ({blades} found)")

    # The operators create markers in the dedicated collection (survives builds).
    n_before = len(mfutil.marker_points("MF_VentMark"))
    bpy.context.scene.cursor.location = (3.0, 4.0, 5.0)
    bpy.ops.moldforge.add_vent_marker()
    check(len(mfutil.marker_points("MF_VentMark")) == n_before + 1,
          "Add Vent Marker operator drops a marker at the 3D cursor")
    np_before = len(mfutil.marker_points("MF_PourMark"))
    bpy.ops.moldforge.add_pour_marker()
    check(len(mfutil.marker_points("MF_PourMark")) == np_before + 1,
          "Add Pour Marker operator drops a pour marker")

    # --- Exploded preview: parts slide apart, snap back exactly, and export
    # auto-reassembles first (STLs must never ship shifted).
    homes = {}
    mfc2 = bpy.data.collections.get(mfutil.COLLECTION_NAME)
    for o5 in mfc2.objects:
        if o5.type == 'MESH':
            homes[o5.name] = tuple(o5.location)
    bpy.ops.moldforge.explode()
    moved = sum(1 for o5 in mfc2.objects if o5.type == 'MESH'
                and tuple(o5.location) != homes.get(o5.name))
    check(moved >= 2, f"exploded preview slides the parts apart ({moved} moved)")
    bpy.ops.moldforge.explode()
    back = all(max(abs(a - b) for a, b in zip(tuple(o5.location),
                                              homes[o5.name])) < 1e-6
               for o5 in mfc2.objects if o5.type == 'MESH'
               and o5.name in homes)
    check(back, "second press reassembles every part exactly")
    bpy.ops.moldforge.explode()
    import tempfile
    with tempfile.TemporaryDirectory() as td2:
        bpy.ops.moldforge.export('EXEC_DEFAULT', directory=td2)
    back2 = all(max(abs(a - b) for a, b in zip(tuple(o5.location),
                                               homes[o5.name])) < 1e-6
                for o5 in mfc2.objects if o5.type == 'MESH'
                and o5.name in homes)
    check(back2, "export reassembles an exploded preview before writing STLs")

    # --- Exploded preview must actually OPEN the mold: the halves clear each
    # other and the positive lifts out the top. The parked accessories (plug,
    # Core_Master) must not drag the anchor off the mold's axis - that was the
    # bug: the halves slid sideways together and the master stayed buried.
    print("\n=== Exploded preview: flat parts layout ===")
    reset_scene()
    bmx2 = bmesh.new()
    bmesh.ops.create_cone(bmx2, cap_ends=True, segments=48, radius1=26.0,
                          radius2=20.0, depth=120.0)
    mx2_ = bpy.data.meshes.new("XCone"); bmx2.to_mesh(mx2_); bmx2.free()
    ox2_ = bpy.data.objects.new("XCone", mx2_)
    bpy.context.scene.collection.objects.link(ox2_)
    xr = pipeline.build_mold_system(ox2_, _over(default_props(),
            box_style='POUR_BOX', base_style='LOCK', wings=True, parts_count=2,
            split_axis='X', sprue_place='TOP', lock_height=10.0,
            lock_margin=4.0, lock_teeth=3, lock_tooth_depth=2.0,
            lock_tolerance=0.2, dual_density=True, core_wall=5.0,
            anchor_plug=True, vent_count=0))
    coll_x = mfutil.ensure_collection()
    homes_x = {o.name: tuple(o.location) for o in coll_x.objects
               if o.type == 'MESH'}
    a_x = next(o for o in xr["parts"] if o.name == "MF_Mold_A")
    b_x = next(o for o in xr["parts"] if o.name == "MF_Mold_B")
    pos_x = xr["positive"]
    bpy.ops.moldforge.explode()
    bpy.context.evaluated_depsgraph_get()
    laid = [o for o in coll_x.objects if o.type == 'MESH']
    bbs = {o.name: mfutil.world_bbox(o) for o in laid}
    bottoms = [b[0].z for b in bbs.values()]
    check(max(bottoms) - min(bottoms) < 0.05,
          f"every part sits on ONE baseline (spread {max(bottoms) - min(bottoms):.3f} mm)")
    # A row: sort by x and require each part to start after the previous ends.
    order = sorted(bbs.items(), key=lambda kv: kv[1][0].x)
    worst = min((order[i + 1][1][0].x - order[i][1][1].x)
                for i in range(len(order) - 1))
    check(worst > 0.0,
          f"no two parts overlap - the tightest gap is {worst:.1f} mm")
    skin_x = next((o for o in laid if o.name == "MF_Skin"), None)
    if skin_x is not None:
        smn, smx = bbs["MF_Skin"]
        pmn4, pmx4 = bbs[pos_x.name]
        check(smx.x < pmn4.x or smn.x > pmx4.x,
              "the skin and the positive no longer share the same space")
    amn, amx = bbs[a_x.name]
    bmn2, bmx3 = bbs[b_x.name]
    check(abs(amn.z - bmn2.z) < 0.05 and abs(amn.z - bbs[pos_x.name][0].z) < 0.05,
          "the shells and the positive are at the same height")
    bpy.ops.moldforge.explode()
    exact = all(max(abs(a - b) for a, b in zip(tuple(o.location),
                                               homes_x[o.name])) < 1e-6
                for o in coll_x.objects
                if o.type == 'MESH' and o.name in homes_x)
    check(exact, "every part returns to its exact build position")
    mcoll = bpy.data.collections.get(mfutil.MARKER_COLLECTION)  # re-fetch: a
    # reset_scene() above invalidated the old reference (purged collection)
    for o in (list(mcoll.objects) if mcoll else []):
        bpy.data.objects.remove(o, do_unlink=True)   # never leak into later tests

    # --- Spout flare: 1.0 = straight tube, bigger = wider catch funnel
    print("\n=== Spout flare ===")

    def mouth_radius(parts):
        top = max(mfutil.world_bbox(p)[1].z for p in parts)
        pts = []
        for p in parts:
            mw = p.matrix_world
            for v in p.data.vertices:
                w = mw @ v.co
                if w.z > top - 1.5:
                    pts.append(w)
        cx = sum(q.x for q in pts) / len(pts)
        cy = sum(q.y for q in pts) / len(pts)
        return max(math.hypot(q.x - cx, q.y - cy) for q in pts)

    radii = {}
    for flare in (1.0, 3.5):
        reset_scene()
        rfl = pipeline.build_mold_system(make_master("Model"),
                                         _over(default_props(), box_style='POUR_BOX',
                                               sprue=True, sprue_place='XY',
                                               sprue_flare=flare, vent_count=0))
        check(all(mfutil.part_is_valid(p)[0] for p in rfl["parts"]),
              f"flare {flare}: valid halves")
        radii[flare] = mouth_radius(rfl["parts"])
    # The auto-cap (0.45 x mold half-width) legitimately limits a big flare on a
    # small mold, so assert a clear-but-capped widening.
    check(radii[3.5] > radii[1.0] + 1.5,
          f"flare widens the funnel mouth ({radii[3.5]:.1f} vs {radii[1.0]:.1f})")

    # --- Tier 2: parting offset, interlocking teeth, undercut metric
    print("\n=== Parting offset / teeth / undercut ===")
    reset_scene()
    ro = pipeline.build_mold_system(make_master("Model"),
                                    _over(default_props(), box_style='POUR_BOX',
                                          split_axis='X', split_offset=6.0))
    check(all(mfutil.part_is_valid(p)[0] for p in ro["parts"]), "parting offset: valid halves")
    reset_scene()
    rt = pipeline.build_mold_system(make_master("Model"),
                                    _over(default_props(), box_style='SOLID', contoured=False,
                                          wings=False, key_count=4, registration='TEETH'))
    check(all(mfutil.part_is_valid(p)[0] for p in rt["parts"]), "interlocking teeth: valid halves")
    reset_scene()
    bpy.ops.mesh.primitive_torus_add(major_radius=14, minor_radius=4)
    tor = bpy.context.active_object
    check(mfutil.undercut_fraction(tor, 'X') > 0.3, "undercut: torus trapped across the hole")
    check(mfutil.undercut_fraction(tor, 'Z') < 0.05, "undercut: torus frees along the hole axis")

    # --- Tier 3: auto-orientation picks the best-releasing split axis
    print("\n=== Auto-orientation ===")
    reset_scene()
    # A torus with its hole along Y: it releases pulling along Y (through the hole)
    # but is badly trapped along X — even though X is the WIDER footprint. AUTO must
    # override the wider-axis default and choose Y.
    bpy.ops.mesh.primitive_torus_add(major_radius=14, minor_radius=4,
                                     rotation=(math.radians(90), 0, 0))
    rtor = bpy.context.active_object
    tmn, tmx = mfutil.world_bbox(rtor)
    check((tmx.x - tmn.x) > (tmx.y - tmn.y), "auto-orient: X is the wider footprint")
    check(mfutil.undercut_fraction(rtor, 'Y') < mfutil.undercut_fraction(rtor, 'X'),
          "auto-orient: model releases better along Y than X")
    chosen = split.resolve_axis(_over(default_props(), split_axis='AUTO'), rtor)
    check(chosen == 'Y', f"auto-orient: AUTO picks the releasing axis Y (got {chosen})")
    # A convex blob releases either way -> AUTO falls back to the wider axis (no flip).
    reset_scene()
    blob = make_master("Model")          # scale (1.0, 0.8, 1.3): X wider than Y
    conv = split.resolve_axis(_over(default_props(), split_axis='AUTO'), blob)
    check(conv == 'X', f"auto-orient: convex blob falls back to wider axis X (got {conv})")

    # --- Tier 3: multi-part radial molds (3-4 wedges around the vertical axis)
    print("\n=== Multi-part radial molds ===")
    reset_scene()
    two = pipeline.build_mold_system(make_master("Model"),
                                     _over(default_props(), box_style='POUR_BOX',
                                           base_style='FLAT', base_flange=True, wings=False, parts_count=2))
    two_vol = sum(volume.mesh_volume(p) for p in two["parts"])
    for n in (3, 4):
        reset_scene()
        rm = pipeline.build_mold_system(make_master("Model"),
                                        _over(default_props(), box_style='POUR_BOX',
                                              base_style='FLAT', base_flange=True, wings=False, parts_count=n))
        parts = rm["parts"]
        check(len(parts) == n, f"{n}-part: produced {n} wedges (got {len(parts)})")
        check(all(mfutil.part_is_valid(p)[0] for p in parts),
              f"{n}-part: every wedge is a valid watertight solid")
        check(all(island_count(p) == 1 for p in parts),
              f"{n}-part: every wedge is a single piece")
        nvol = sum(volume.mesh_volume(p) for p in parts)
        check(abs(nvol - two_vol) < 0.12 * two_vol,
              f"{n}-part: wedges tile the same mold ({nvol:.0f} vs 2-part {two_vol:.0f})")
        out_dir = os.path.join("/tmp", "mf_out", f"radial_{n}")
        written = mfutil_export(parts, out_dir)
        check(len(written) == n, f"{n}-part: exported {n} STL files")
    # Multi-part also works for the solid styles and the glove mold.
    reset_scene()
    rmg = pipeline.build_mold_system(make_master("Model"),
                                     _over(default_props(), box_style='POUR_BOX', skin_keys=True,
                                           base_style='FLAT', base_flange=True, wings=False, parts_count=3))
    check(len(rmg["parts"]) == 3 and all(mfutil.part_is_valid(p)[0] for p in rmg["parts"]),
          "multi-part glove mold: 3 valid wedges")

    # Radial clamp wings: one bolted flange per seam, so the mold must get wider
    # and every winged wedge must stay a valid solid.
    print("\n=== Radial clamp wings ===")
    reset_scene()
    rnw = pipeline.build_mold_system(make_master("Model"),
                                     _over(default_props(), box_style='POUR_BOX',
                                           base_style='OPEN', wings=False,
                                           parts_count=3, sprue=False))
    no_w = max_dim(*combined_bbox(rnw["parts"]))
    reset_scene()
    rww = pipeline.build_mold_system(make_master("Model"),
                                     _over(default_props(), box_style='POUR_BOX',
                                           base_style='OPEN', wings=True, wing_width=10.0,
                                           parts_count=3, sprue=False))
    with_w = max_dim(*combined_bbox(rww["parts"]))
    check(all(mfutil.part_is_valid(p)[0] for p in rww["parts"]),
          "radial wings: 3 winged wedges are valid solids")
    check(with_w > no_w + 4.0, f"radial wings widen the mold ({with_w:.1f} > {no_w:.1f})")
    # The reported real-world config: Pour Box + Open Bottom + 3 pieces + funnel.
    reset_scene()
    rws = pipeline.build_mold_system(make_master("Model"),
                                     _over(default_props(), box_style='POUR_BOX',
                                           base_style='OPEN', wings=True,
                                           parts_count=3, sprue=True, vent_count=0))
    check(len(rws["parts"]) == 3 and all(mfutil.part_is_valid(p)[0] for p in rws["parts"]),
          "radial wings + funnel + open bottom: 3 valid wedges")

    # Seams must cut exactly THROUGH the wings. The wings stick out at the seam
    # angles and shift the mold's bounding box, so if the radial split re-derived
    # its centre the off-axis seams would slide off their wings. On an axisymmetric
    # model with no funnel a correct split makes the 3 wedges congruent, so equal
    # wedge volumes prove every seam still bisects its wing.
    print("\n=== Radial seams cut through the wings ===")
    reset_scene()
    rwc = pipeline.build_mold_system(make_axisym_master("Model"),
                                     _over(default_props(), box_style='POUR_BOX',
                                           base_style='OPEN', wings=True, wing_width=12.0,
                                           parts_count=3, sprue=False))
    check(len(rwc["parts"]) == 3 and all(mfutil.part_is_valid(p)[0] for p in rwc["parts"]),
          "axisym radial wings: 3 valid wedges")
    wv = sorted(max(volume.mesh_volume(p), 0.0) for p in rwc["parts"])
    imb = (wv[2] / wv[0]) if wv[0] > 1e-6 else 999.0
    check(imb < 1.10,
          f"seams bisect the wings: wedges stay balanced (max/min vol {imb:.2f})")

    # Operator path with real PropertyGroup defaults.
    print("\n=== Operator path (real defaults) ===")
    reset_scene()
    master = make_master("Model")
    bpy.context.view_layer.objects.active = master
    master.select_set(True)
    with bpy.context.temp_override(active_object=master, selected_objects=[master], object=master):
        res = bpy.ops.moldforge.generate()
    check('FINISHED' in res, f"generate operator returned {res}")
    check(bpy.context.scene.moldforge.last_silicone_volume > 0.0, "operator stored a silicone volume")

    # --- Scene units: the mm size fields convert by the scene's unit scale, so a mold is
    # correct in an Imperial scene or any metric scale, not only the 1 unit = 1 mm
    # convention (the bug: sizes were used as raw Blender units regardless of unit system).
    print("\n=== Scene units (Imperial / non-mm metric) ===")
    from moldforge.core import units as mfunits

    def _set_units(system, scale, length_unit=None):
        us = bpy.context.scene.unit_settings
        us.system = system
        us.scale_length = scale
        if length_unit is not None:
            us.length_unit = length_unit

    _set_units('NONE', 1.0)
    check(abs(mfunits.mm_per_unit(bpy.context.scene) - 1.0) < 1e-9, "mm/unit: none -> 1")
    _set_units('METRIC', 1.0, 'METERS')
    check(abs(mfunits.mm_per_unit(bpy.context.scene) - 1.0) < 1e-9,
          "mm/unit: untouched default metric -> 1 (the 1 unit = 1 mm convention, no regression)")
    _set_units('METRIC', 1.0, 'MILLIMETERS')
    check(abs(mfunits.mm_per_unit(bpy.context.scene) - 1000.0) < 1e-6,
          "mm/unit: metric with a real Length unit -> 1000 (1 unit = 1 m)")
    _set_units('METRIC', 0.01, 'METERS')
    check(abs(mfunits.mm_per_unit(bpy.context.scene) - 10.0) < 1e-6,
          "mm/unit: metric Unit Scale 0.01 (1 unit = 1 cm) -> 10")
    _set_units('IMPERIAL', 1.0)
    check(abs(mfunits.mm_per_unit(bpy.context.scene) - 1000.0) < 1e-6,
          "mm/unit: imperial -> 1000 (1 unit = 1 m; was wrongly 1)")

    _set_units('METRIC', 1.0, 'MILLIMETERS')   # real units, mpu = 1000 -> 1 mm = 0.001 units
    bp = mfunits.build_props(_over(default_props(), wall_thickness=3.0, shell_wall=2.0,
                                   parts_count=3), bpy.context.scene)
    check(abs(bp.wall_thickness - 0.003) < 1e-9 and abs(bp.shell_wall - 0.002) < 1e-9,
          f"build_props: 3 mm wall -> {bp.wall_thickness:g} units at scale 1")
    check(bp.parts_count == 3, "build_props leaves non-length fields alone")

    # Geometry invariance: the SAME mm sizes on the SAME real object give a proportional
    # mold whatever the unit scale. A = a 20 mm object at 1 unit = 1 mm; B = the same 20 mm
    # object at 1 unit = 10 mm (metric scale 0.01) -> 2 Blender units. Both want a 3 mm wall.
    usizes = dict(box_style='POUR_BOX', base_style='FOLLOW', wings=False, sprue=False,
                  vent_count=0, contoured=False, split_axis='X',
                  wall_thickness=3.0, shell_wall=2.0)
    reset_scene(); _set_units('NONE', 1.0)
    uA = pipeline.build_mold_system(cube_master(20.0),
            mfunits.build_props(_over(default_props(), **usizes), bpy.context.scene))
    okA = all(mfutil.part_is_valid(p)[0] for p in uA["parts"]); volA = uA["silicone_volume"]
    reset_scene(); _set_units('METRIC', 0.01)
    uB = pipeline.build_mold_system(cube_master(2.0),
            mfunits.build_props(_over(default_props(), **usizes), bpy.context.scene))
    okB = all(mfutil.part_is_valid(p)[0] for p in uB["parts"]); volB = uB["silicone_volume"]
    check(okA and okB, "unit-scaled molds both valid")
    uratio = volB / max(volA, 1e-12)
    check(5e-4 < uratio < 2e-3,
          f"mm sizes give a proportional mold across unit scales "
          f"(silicone ratio {uratio:.2e}, want ~1e-3)")
    _set_units('NONE', 1.0)     # restore for the remaining tests

    # --- UI guards: typing oversized sprue/vent values snaps them to what the
    # active model can actually take (no more silent build-time disagreement).
    print("\n=== UI guards: oversized inputs snap to fit ===")
    reset_scene()
    mg = make_master("Model")
    bpy.context.view_layer.objects.active = mg
    mg.select_set(True)
    sp = bpy.context.scene.moldforge
    sp.box_style = 'POUR_BOX'
    sp.sprue_radius = 500.0
    check(sp.sprue_radius < 20.0,
          f"sprue radius typed 500 -> snapped to fit ({sp.sprue_radius:.2f})")
    sp.vent_radius = 99.0
    check(sp.vent_radius < 10.0,
          f"vent radius typed 99 -> snapped to fit ({sp.vent_radius:.2f})")
    # Oversized Throat = fully manual: the typed value is kept exactly, and turning
    # the toggle back off re-clamps it to the auto fit.
    sp.big_throat = True
    sp.sprue_radius = 500.0
    check(sp.sprue_radius > 499.0,
          f"Oversized Throat keeps the typed 500 exactly ({sp.sprue_radius:.1f})")
    sp.big_throat = False
    check(sp.sprue_radius < 20.0,
          f"turning Oversized Throat off re-clamps to fit ({sp.sprue_radius:.2f})")

    print("\n=== Transformed master (rotate + non-uniform scale + translate) ===")
    reset_scene()
    m = make_master("Model")
    m.location = Vector((37, -12, 5)); m.rotation_euler = Euler((0.6, 0.3, 1.1)); m.scale = Vector((1.4, 0.7, 1.1))
    res = pipeline.build_mold_system(m, default_props())
    for i, part in enumerate(res["parts"]):
        check(nonmanifold_edges(part) == 0, f"transformed: half {i} watertight")
        check(island_count(part) == 1, f"transformed: half {i} single piece")

    print("\n=== Regenerate replaces ===")
    reset_scene()
    m = make_master("Model")
    pipeline.build_mold_system(m, default_props())
    pipeline.build_mold_system(m, default_props())
    check(len([o for o in bpy.data.objects if o.name.startswith("MF_Mold_")]) == 2,
          "regenerate replaces, no accumulation")

    # --- Bad input must fail cleanly; detailed/thin models must auto-recover --- #
    expect_clean_error("NaN coordinates", make_nan_master)
    expect_clean_error("Multi-island master", make_multi_island_master)
    expect_clean_error("MF_-named master", lambda: _rename(make_master("Model"), "MF_Positive"))
    # A non-manifold/degenerate mesh is now recovered (Safe Remesh turns it into a
    # moldable solid) rather than rejected — auto-recovery + the alternate split axis
    # find a valid mold, and the user still gets the "remeshed" warning.
    expect_success("Non-manifold (degenerate) master", make_nonmanifold_master)
    expect_success("Thin-fin model", make_thin_fin_master,
                   {"box_style": 'POUR_BOX', "base_style": 'FLAT'})
    expect_success("Concave union (bird-like)", make_bird_master,
                   {"box_style": 'POUR_BOX', "base_style": 'FLAT', "base_flange": True})
    expect_success("Contoured parting on asymmetric model", make_bird_master,
                   {"box_style": 'POUR_BOX', "base_style": 'FLAT', "base_flange": True, "contoured": True})
    expect_success("Concave crescent (pour box, open bottom, wings)", make_crescent_master,
                   {"box_style": 'POUR_BOX', "base_style": 'OPEN', "contoured": True,
                    "wings": True})

    # Island helpers used by the last-resort fragment trim (e.g. a severed
    # open-bottom rim): a big body plus a tiny stray island.
    print("\n=== Island trim helpers ===")
    reset_scene()
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=3, radius=10)
    bmesh.ops.create_icosphere(bm, subdivisions=1, radius=1,
                               matrix=Matrix.Translation((40, 0, 0)))
    hme = bpy.data.meshes.new("two"); bm.to_mesh(hme); bm.free()
    helper = bpy.data.objects.new("two", hme)
    bpy.context.scene.collection.objects.link(helper)
    frac = mfutil.largest_island_fraction(helper)
    check(frac > 0.9, f"largest_island_fraction finds the dominant body ({frac:.2f})")
    dropped = mfutil.keep_largest_island(helper)
    check(dropped > 0.0 and island_count(helper) == 1,
          f"keep_largest_island leaves a single solid (dropped {dropped:.0%})")

    print("\n=== User data preserved + scene-aware collection ===")
    reset_scene()
    mf = bpy.data.collections.new("MoldForge"); bpy.context.scene.collection.children.link(mf)
    mf.objects.link(bpy.data.objects.new("MyArt", bpy.data.meshes.new("MyArt")))
    pipeline.build_mold_system(make_master("Model"), default_props())
    check("MyArt" in bpy.data.objects, "user's object in MoldForge collection NOT deleted")

    print("\n=== Orphaned MoldForge collection ===")
    reset_scene()
    bpy.data.collections.new("MoldForge")   # exists, unlinked
    try:
        pipeline.build_mold_system(make_master("Model"), default_props())
        check(True, "orphaned collection: built without crash")
    except Exception as exc:
        check(False, f"orphaned collection raised: {exc}")

    print("\n=== Solid flat-base keeps a closed floor ===")
    reset_scene()
    res = pipeline.build_mold_system(make_master("Model"),
                                     _over(default_props(), box_style='SOLID', base_style='FLAT'))
    floor = mfutil.world_bbox(res["positive"])[0].z - min(
        mfutil.world_bbox(p)[0].z for p in res["parts"])
    check(floor > 0.05, f"solid flat base keeps a floor (thk {floor:.2f})")

    print("\n=== Open bottom (cut at master base, no floor) ===")
    reset_scene()
    res = pipeline.build_mold_system(make_master("Model"),
                                     _over(default_props(), box_style='POUR_BOX', base_style='OPEN'))
    master_bottom = mfutil.world_bbox(res["positive"])[0].z
    box_bottom = min(mfutil.world_bbox(p)[0].z for p in res["parts"])
    check(abs(box_bottom - master_bottom) < 0.05 * char_size(res["positive"]),
          f"open bottom cut at master base (box {box_bottom:.2f} vs master {master_bottom:.2f})")
    for part in res["parts"]:
        check(mfutil.part_is_valid(part)[0], "open-bottom half is a valid solid")

    # --- Suction-cup former: a printed dome-on-legs (MF_Mold_Cup) that seats over the
    # open bottom; pressed into the pour it leaves a suction bell in the cast's base.
    print("\n=== Suction-cup former (open bottom) ===")
    reset_scene()
    rsc = pipeline.build_mold_system(make_master("Model"),
            _over(default_props(), box_style='POUR_BOX', base_style='OPEN', wings=False,
                  suction_cup=True, cup_depth=8.0))
    scnames = [q.name for q in rsc["parts"]]
    check(any(n.startswith("MF_Mold_Cup") for n in scnames),
          f"suction-cup former part present ({scnames})")
    check(all(mfutil.part_is_valid(q)[0] for q in rsc["parts"]),
          "former + shells all valid solids")
    sc_cup = next(q for q in rsc["parts"] if q.name.startswith("MF_Mold_Cup"))
    sc_shells = [q for q in rsc["parts"] if not q.name.startswith("MF_Mold_Cup")]
    shmn, _shmx = combined_bbox(sc_shells)
    cmn, cmx = mfutil.world_bbox(sc_cup)
    check(cmx.z > shmn.z + 4.0 and cmn.z < shmn.z - 1.0,
          f"former plugs the opening: dome {cmx.z - shmn.z:.1f} up into the mold, "
          f"plate/legs below the rim")
    check(len(sc_cup.data.polygons) > 6000,
          f"former is HIGH-POLY ({len(sc_cup.data.polygons)} faces - ring-built dome, "
          f"no icosphere faceting)")
    # Diameter control, on a FLAT-based master (a rounded base has a tiny true opening,
    # so both sizes would clamp to the same minimum — correctly).
    reset_scene()
    rca = pipeline.build_mold_system(cube_master(20.0),
            _over(default_props(), box_style='POUR_BOX', base_style='OPEN', wings=False,
                  suction_cup=True, cup_depth=8.0))
    v_auto = volume.mesh_volume(
        next(q for q in rca["parts"] if q.name.startswith("MF_Mold_Cup")))
    reset_scene()
    rsc2 = pipeline.build_mold_system(cube_master(20.0),
            _over(default_props(), box_style='POUR_BOX', base_style='OPEN', wings=False,
                  suction_cup=True, cup_diameter=17.0, cup_depth=8.0))
    sc_cup2 = next(q for q in rsc2["parts"] if q.name.startswith("MF_Mold_Cup"))
    check(volume.mesh_volume(sc_cup2) > v_auto + 1.0,
          f"Cup Diameter scales the former ({v_auto:.0f} -> "
          f"{volume.mesh_volume(sc_cup2):.0f} u³)")
    # On a closed bottom the option is ignored (no cup part, no crash).
    reset_scene()
    rscf = pipeline.build_mold_system(make_master("Model"),
            _over(default_props(), box_style='POUR_BOX', base_style='FLAT',
                  suction_cup=True))
    check(not any(q.name.startswith("MF_Mold_Cup") for q in rscf["parts"])
          and all(mfutil.part_is_valid(q)[0] for q in rscf["parts"]),
          "suction cup ignored on a closed bottom (no crash)")

    # SNAP LOCK + HOLLOW: the pour FLOATS the former (buoyancy), so each hook carries a
    # bead that clicks into a groove cut into the shells' outer wall — and the former is
    # hollowed from the underside (dome shell + riser tube) to save filament.
    reset_scene()
    rsn = pipeline.build_mold_system(cube_master(20.0),
            _over(default_props(), box_style='POUR_BOX', base_style='OPEN', wings=False,
                  suction_cup=True, cup_depth=8.0))
    sn_shells = [q for q in rsn["parts"] if not q.name.startswith("MF_Mold_Cup")]
    sn_cup = next(q for q in rsn["parts"] if q.name.startswith("MF_Mold_Cup"))
    check(all(mfutil.part_is_valid(q)[0] for q in rsn["parts"]),
          "snap + hollow: all parts valid")
    snmn, _snmx = combined_bbox(sn_shells)

    def _shell_wall_r(theta, z):
        d = Vector((math.cos(theta), math.sin(theta), 0.0))
        o = Vector((d.x * 200.0, d.y * 200.0, z))
        best = None
        for q in sn_shells:
            qinv = q.matrix_world.inverted()
            hit, loc, _n, _i = q.ray_cast(qinv @ o, (qinv.to_3x3() @ -d).normalized())
            if hit:
                w = q.matrix_world @ loc
                rr = math.hypot(w.x, w.y)
                best = rr if best is None else max(best, rr)
        return best

    a45 = math.pi * 0.25
    r_ref = _shell_wall_r(a45, snmn.z + 0.5)
    r_grv = _shell_wall_r(a45, snmn.z + 1.5)          # the bead height
    check(r_ref is not None and r_grv is not None and r_grv < r_ref - 0.35,
          f"snap groove cut into the shells at the leg position "
          f"(wall r {r_ref:.2f} -> {r_grv:.2f} at bead height)")
    # Hollow underside: a ray up the former's centreline flies through the open plate
    # and riser tube, landing only on the dome's inner ceiling. (Measured BEFORE the
    # fastening-mode rebuilds below wipe this scene.)
    scmn, scmx = mfutil.world_bbox(sn_cup)
    sccx, sccy = (scmn.x + scmx.x) * 0.5, (scmn.y + scmx.y) * 0.5
    scinv = sn_cup.matrix_world.inverted()
    hitc, locc, _nc, _ic = sn_cup.ray_cast(
        scinv @ Vector((sccx, sccy, scmn.z - 3.0)),
        (scinv.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized())
    hit_up = ((sn_cup.matrix_world @ locc).z - scmn.z) if hitc else -1.0
    check(hitc and hit_up > 6.0,
          f"former is HOLLOW from the underside (first surface {hit_up:.1f} above its "
          f"bottom = the dome's inner ceiling, not a solid plate)")

    # Fastening modes: the default PIN cuts a pin channel (groove asserted above with
    # zero flex needed); SNAP instead ADDS beads (more material than PIN's channel);
    # BAND adds cleat lips and cuts NOTHING into the shells.
    v_pin = volume.mesh_volume(sn_cup)
    reset_scene()
    rsnap = pipeline.build_mold_system(cube_master(20.0),
            _over(default_props(), box_style='POUR_BOX', base_style='OPEN', wings=False,
                  suction_cup=True, cup_depth=8.0, cup_lock='SNAP'))
    v_snap = volume.mesh_volume(
        next(q for q in rsnap["parts"] if q.name.startswith("MF_Mold_Cup")))
    check(v_snap > v_pin + 0.5,
          f"SNAP adds beads where PIN cuts channels (cup {v_pin:.0f} -> {v_snap:.0f} u³)")
    reset_scene()
    rband = pipeline.build_mold_system(cube_master(20.0),
            _over(default_props(), box_style='POUR_BOX', base_style='OPEN', wings=False,
                  suction_cup=True, cup_depth=8.0, cup_lock='BAND'))
    band_shells = [q for q in rband["parts"] if not q.name.startswith("MF_Mold_Cup")]
    check(all(mfutil.part_is_valid(q)[0] for q in rband["parts"]),
          "BAND cleats: all parts valid")
    bnmn, _bn = combined_bbox(band_shells)

    def _band_wall_r(z):
        d = Vector((math.cos(a45), math.sin(a45), 0.0))
        o = Vector((d.x * 200.0, d.y * 200.0, z))
        best = None
        for q in band_shells:
            qinv = q.matrix_world.inverted()
            hit, loc, _n, _i = q.ray_cast(qinv @ o, (qinv.to_3x3() @ -d).normalized())
            if hit:
                w = q.matrix_world @ loc
                rr = math.hypot(w.x, w.y)
                best = rr if best is None else max(best, rr)
        return best

    rb0 = _band_wall_r(bnmn.z + 0.5)
    rb1 = _band_wall_r(bnmn.z + 1.5)
    check(rb0 is not None and rb1 is not None and abs(rb0 - rb1) < 0.25,
          f"BAND cuts nothing into the shells (wall r {rb0:.2f} vs {rb1:.2f})")

    # An EXPLICIT Cup Diameter may exceed the cast opening (the bell truncates there) —
    # the only hard cap is the insertion limit (the Locking Base's socket). On a cube-20
    # LOCK (opening r = 10, socket ~ 10 + margin 4) a Ø60 request must yield a dome
    # clearly WIDER than the opening, not clamped to it.
    reset_scene()
    rbig = pipeline.build_mold_system(cube_master(20.0),
            _over(default_props(), box_style='POUR_BOX', base_style='LOCK', wings=False,
                  suction_cup=True, lock_height=8.0, lock_margin=4.0,
                  cup_diameter=60.0, cup_depth=12.0))
    big_cup = next(q for q in rbig["parts"] if q.name.startswith("MF_Mold_Cup"))
    check(all(mfutil.part_is_valid(q)[0] for q in rbig["parts"]),
          "wide-cup LOCK build: all parts valid")
    bcmn, bcmx = mfutil.world_bbox(big_cup)
    bccx, bccy = (bcmn.x + bcmx.x) * 0.5, (bcmn.y + bcmx.y) * 0.5
    dome_probe_z = bcmx.z - 12.0 + 1.0            # 1 mm above the dome's base plane
    binv = big_cup.matrix_world.inverted()
    hitd, locd, _nd, _id = big_cup.ray_cast(
        binv @ Vector((bccx, bccy, dome_probe_z)),
        (binv.to_3x3() @ Vector((1.0, 0.0, 0.0))).normalized())
    dome_r = ((big_cup.matrix_world @ locd).x - bccx) if hitd else 0.0
    check(dome_r > 11.0,
          f"explicit Cup Ø exceeds the cast opening, capped only by the socket "
          f"(dome r {dome_r:.1f} > opening r 10)")

    # A model that BULGES right above a narrow base (the abomination case): the legs
    # must size to the mold's BOTTOM BAND, never to the flared wall above it.
    def _bulge_master(name):
        bm = bmesh.new()
        bmesh.ops.create_icosphere(bm, subdivisions=4, radius=13.0)
        for v in bm.verts:
            v.co.z += 6.0                       # bulge sits above the base
            if v.co.z < 0.0:
                v.co.z *= 0.35                  # squash the underside
        tmp = bmesh.new()
        bmesh.ops.create_cone(tmp, cap_ends=True, segments=48,
                              radius1=5.5, radius2=6.5, depth=8.0)
        for v in tmp.verts:
            v.co.z -= 5.0                       # narrow neck/base below the bulge
        m2 = bpy.data.meshes.new("t"); tmp.to_mesh(m2); tmp.free()
        bm.from_mesh(m2); bpy.data.meshes.remove(m2)
        me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
        o = bpy.data.objects.new(name, me); bpy.context.scene.collection.objects.link(o)
        from moldforge.core import meshprep as _mp
        _mp.voxel_remesh(o, 0.5)
        zmin = min(v.co.z for v in o.data.vertices)
        for v in o.data.vertices:
            if v.co.z < zmin + 1.5:
                v.co.z = zmin + 1.5             # flat base
        o.data.update()
        return o

    reset_scene()
    rbg = pipeline.build_mold_system(_bulge_master("Bulge"),
            _over(default_props(), box_style='POUR_BOX', base_style='LOCK', wings=False,
                  suction_cup=True, lock_height=5.0, cup_depth=6.0))
    bg_cup = next(q for q in rbg["parts"] if q.name.startswith("MF_Mold_Cup"))
    bg_shells = [q for q in rbg["parts"] if not q.name.startswith("MF_Mold_Cup")]
    bsmn, _b = combined_bbox(bg_shells)
    # the shells' bottom-band footprint (verts within 3 of the rim), in world XY radius
    band_r = 0.0
    for q in bg_shells:
        mwq = q.matrix_world
        for v in q.data.vertices:
            w = mwq @ v.co
            if w.z <= bsmn.z + 3.0:
                band_r = max(band_r, math.hypot(w.x, w.y))
    cgmn, cgmx = mfutil.world_bbox(bg_cup)
    cup_r = max(cgmx.x - cgmn.x, cgmx.y - cgmn.y) * 0.5   # the former's own half-extent
    check(all(mfutil.part_is_valid(q)[0] for q in rbg["parts"]),
          "bulge + former: all parts valid")
    check(cup_r <= band_r + 3.5,
          f"legs stay at the BOTTOM BAND, not the flared bulge "
          f"(former reach {cup_r:.1f} vs band {band_r:.1f} - no more floating tabs)")

    # Locking Base + former: the shells' bottom sits Base Height LOWER (at the plinth
    # bottom), and the former must follow it — seated at that lower rim, dome rising
    # Cup Depth into the sawtooth socket.
    reset_scene()
    rlk = pipeline.build_mold_system(make_master("Model"),
            _over(default_props(), box_style='POUR_BOX', base_style='LOCK', wings=False,
                  suction_cup=True, lock_height=12.0, cup_depth=8.0))
    lknames = [q.name for q in rlk["parts"]]
    check(any(n.startswith("MF_Mold_Cup") for n in lknames),
          f"lock + former: cup part present ({lknames})")
    check(all(mfutil.part_is_valid(q)[0] for q in rlk["parts"]),
          "lock + former: all parts valid")
    lk_cup = next(q for q in rlk["parts"] if q.name.startswith("MF_Mold_Cup"))
    lk_shells = [q for q in rlk["parts"] if not q.name.startswith("MF_Mold_Cup")]
    lsmn, _l = combined_bbox(lk_shells)
    pmn_, pmx_ = mfutil.world_bbox(rlk["positive"])   # positive = model + plinth
    check(abs(lsmn.z - pmn_.z) < 1.5,
          f"lock shells open at the PLINTH bottom (shells {lsmn.z:.1f} vs plinth "
          f"{pmn_.z:.1f} - Base Height lower than the model)")
    lcmn, lcmx = mfutil.world_bbox(lk_cup)
    check(lcmn.z < lsmn.z - 1.0, "lock former: plate/legs seat below the shells' rim")
    # The cast begins at the MODEL's base (Base Height above the rim) — the socket below
    # is empty (it holds the printed base, not the cast). The dome must ride the riser
    # THROUGH the socket and press into the pour at that plane: apex = rim + Base Height
    # + Cup Depth.
    want_top = 12.0 + 8.0
    check(abs((lcmx.z - lsmn.z) - want_top) < 2.0,
          f"dome presses into the cast at the MODEL's base, not the rim "
          f"(apex {lcmx.z - lsmn.z:.1f} above the rim; want Base Height 12 + Cup Depth 8 "
          f"= {want_top:.0f})")

    print("\n=== Flange base widens the footprint + stays valid ===")
    def footprint(parts):
        mn, mx = combined_bbox(parts)
        return max(mx.x - mn.x, mx.y - mn.y)
    reset_scene()
    rflat = pipeline.build_mold_system(make_master("Model"),
                                       _over(default_props(), box_style='POUR_BOX', base_style='FLAT'))
    fp_flat = footprint(rflat["parts"])
    reset_scene()
    rfl = pipeline.build_mold_system(make_master("Model"),
                                     _over(default_props(), box_style='POUR_BOX', base_style='FLAT', base_flange=True))
    fp_flange = footprint(rfl["parts"])
    check(fp_flange > fp_flat * 1.1, f"flange widens the base ({fp_flange:.1f} > {fp_flat:.1f})")
    check(all(mfutil.part_is_valid(p)[0] for p in rfl["parts"]), "flange halves are valid solids")

    print("\n=== Export scoped to the MoldForge collection ===")
    reset_scene()
    m = make_master("Model")
    bpy.context.view_layer.objects.active = m
    m.select_set(True)
    with bpy.context.temp_override(active_object=m, selected_objects=[m], object=m):
        bpy.ops.moldforge.generate()
    decoy = bpy.data.objects.new("MF_Mold_DECOY", bpy.data.meshes.new("MF_Mold_DECOY"))
    bpy.context.scene.collection.objects.link(decoy)
    coll = bpy.data.collections.get("MoldForge")
    exportable = [o.name for o in coll.objects if o.name.startswith("MF_Mold_")]
    check("MF_Mold_DECOY" not in exportable, f"export ignores decoy outside collection ({exportable})")

    # The Export button must write EVERY print: the shells AND the positive (with a
    # Locking Base the positive+plinth is a print too) — the positive used to be
    # silently skipped by the MF_Mold_* name filter. The positive is hidden after
    # generate; hide_set keeps it in the depsgraph, so its STL must still carry
    # real geometry (a depsgraph-excluded hide would export 0 triangles).
    out_btn = os.path.join("/tmp", "mf_out", "button")
    if os.path.isdir(out_btn):
        for f in os.listdir(out_btn):
            os.remove(os.path.join(out_btn, f))
    os.makedirs(out_btn, exist_ok=True)
    bpy.ops.moldforge.export(directory=out_btn)
    files = sorted(os.listdir(out_btn))
    shells = [f for f in files if f.startswith("MF_Mold_")]
    check("MF_Positive.stl" in files and len(shells) >= 2,
          f"Export button writes the shells AND the positive ({files})")
    pcnt, _psize, pok = verify_stl(os.path.join(out_btn, "MF_Positive.stl"))
    check(pok and pcnt > 100,
          f"hidden positive exports real geometry ({pcnt} triangles)")
    check("MF_Mold_DECOY.stl" not in files, "button export ignored the decoy")

    print("\n" + "=" * 40)
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} FAILURE(S)")
        for f in FAILURES:
            print("  - " + f)
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
