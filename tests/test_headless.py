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
        return pr[0]

    p_top, p_xy, p_x = _primary('TOP'), _primary('XY'), _primary('X')
    check(abs(p_xy.x - lcx) < 0.8 and abs(p_xy.y - lcy) < 0.8,
          f"XY sprue centres both axes (dx {p_xy.x - lcx:+.1f}, dy {p_xy.y - lcy:+.1f})")
    check(abs(p_top.x - lcx) > 2.0,
          f"Highest-point sprue follows the leaning peak (dx {p_top.x - lcx:+.1f})")
    check(abs(p_x.x - lcx) < 0.8 and abs(p_x.y - p_top.y) < 0.8,
          f"Center-X sprue centres X, keeps the peak's Y (dx {p_x.x - lcx:+.1f})")
    # Manual placement: the funnel lands at the typed X/Y offset from the centre.
    p_man = mfsprue._pour_points(None, lean, types.SimpleNamespace(
        sprue_place='MANUAL', sprue_count=1, sprue_x=4.0, sprue_y=-3.0))[0]
    check(abs(p_man.x - (lcx + 4.0)) < 1.0 and abs(p_man.y - (lcy - 3.0)) < 1.0,
          f"Manual sprue lands at the typed offset "
          f"(dx {p_man.x - lcx:+.1f}, dy {p_man.y - lcy:+.1f}, want +4.0,-3.0)")
    # A manual offset beyond the footprint is clamped back onto the model.
    p_clamp = mfsprue._pour_points(None, lean, types.SimpleNamespace(
        sprue_place='MANUAL', sprue_count=1, sprue_x=9999.0, sprue_y=0.0))[0]
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
    check(f_on["mouth_r"] > f_off["mouth_r"] + 1.0,
          f"Oversized Mouth grows past the cap (r {f_off['mouth_r']:.1f} -> {f_on['mouth_r']:.1f})")

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
    check(t_on["sprue_r"] > t_off["sprue_r"] + 1.0,
          f"Oversized Throat grows past the cap (r {t_off['sprue_r']:.1f} -> {t_on['sprue_r']:.1f})")

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
                                           wings=True, sprue=False, bolt_auto=True))
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
