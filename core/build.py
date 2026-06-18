"""Build the mold body.

Returns ``(object, info)`` where ``info`` may carry a pre-computed
``silicone_volume`` (for the pour box, the silicone is the gap, not the part
you print).
"""

import math
import types

import bpy
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


def _dilate_solid(master, distance, name, props, coll):
    """A solid the shape of the model grown outward by ``distance`` (the closed
    layer between the model surface and its outward offset)."""
    obj = util.duplicate_object(master, name, coll)
    mod = obj.modifiers.new("mf_solidify", 'SOLIDIFY')
    mod.thickness = distance
    mod.offset = 1.0
    mod.use_even_offset = True
    mod.use_quality_normals = True
    util.apply_all_modifiers(obj)
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
    util.remove_object(cutter)


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


def add_base_plate(mold, master, props, coll):
    """Detachable bottom: cut the mold open just above the master's base and close
    it with a separate one-piece printed plate (``MF_Mold_Base``).

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

    fit = max(getattr(props, "fit_clearance", 0.3), 0.0)   # clearance per mating face
    pocket = max(min(gap * 0.4, 2.5), 1.0)
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
    t = max(shell * 1.5, 3.0)
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
    if fit > 0.05:
        pocket_cut = _dilate_solid(master, fit, "MF_ppk", props, coll)
        util.boolean(plate, pocket_cut, 'DIFFERENCE')
        util.remove_object(pocket_cut)
    else:
        util.boolean(plate, master, 'DIFFERENCE')

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
    return plate


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


def add_wings(mold, axis, coll, master, outer_offset, width, thickness,
              bolt_radius, props, block=False, funnels=None, cavity=None):
    """Full-height clamp flange along the parting seam, with bolt holes.

    For a profile-hugging mold we build a continuous lip that follows the body's
    silhouette at the parting plane — a thin slice of the shell just *outside* the
    mold wall ("rind"), grown out by ``width`` — so it stays welded to the body
    from top to bottom whatever the model's shape. For a block mold (flat sides) a
    plain flat ear on each side is enough.

    Wings are best-effort: the mold mesh is snapshotted first, and if anything
    would leave a floating piece (or errors out) the snapshot is restored, so
    clamp wings can never break an otherwise-good mold. Added before the split, so
    each half keeps its share of the flange and holes."""
    backup = mold.data.copy()
    try:
        mn, mx = util.world_bbox(mold)
        ai = {'X': 0, 'Y': 1}[axis]
        h = next(i for i in range(3) if i != ai and i != 2)
        center = (mn + mx) * 0.5
        cap = (mx[ai] - mn[ai]) * 0.4
        off = max(min(getattr(props, "split_offset", 0.0), cap), -cap)

        if block:
            _flat_wings(mold, ai, h, center, mn, mx, width, thickness, coll)
        else:
            _profile_flange(mold, master, ai, h, center, mn, mx,
                            outer_offset, width, thickness, props, coll, funnels, off,
                            cavity)
        _drill_wing_bolts(mold, ai, h, center, mn, mx, width, thickness,
                          bolt_radius, axis, coll, _effective_bolts(props), off)
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
    finally:
        if backup is not None:
            bpy.data.meshes.remove(backup)


def _wing_rind(master, outer_offset, width, props, coll, funnels=None):
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
    return rind


def _profile_flange(mold, master, ai, h, center, mn, mx, outer_offset, width,
                    thickness, props, coll, funnels=None, seam_off=0.0, cavity=None):
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
    zc = (mmn.z + mmx.z) * 0.5
    zsz = (mmx.z - mmn.z) + 4.0

    rind = _wing_rind(master, outer_offset, width, props, coll, funnels)

    # One thin slab across the parting plane, spanning the body + flange on both
    # h sides, so a contoured lip is left on each half of the seam.
    c = center.copy(); c[ai] = center[ai] + seam_off; c[h] = center[h]; c[2] = zc
    size = Vector((0.0, 0.0, 0.0))
    size[ai] = thickness                              # thin across the parting plane
    size[h] = big                                     # full width on both sides
    size[2] = zsz
    clip = util.add_box("MF_wslab", c, size, coll)
    util.boolean(rind, clip, 'INTERSECT')
    util.remove_object(clip)
    util.remove_small_islands(rind)
    if rind.data.polygons and _overlaps(rind, mold, coll):
        util.boolean(mold, rind, 'UNION')
        util.remove_small_islands(mold)               # drop any boolean sliver fragments
    util.remove_object(rind)


def add_radial_wings(mold, coll, master, outer_offset, width, thickness,
                     bolt_radius, props, n, funnels=None):
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
        zc = (mn.z + mx.z) * 0.5
        zsz = mx.z - mn.z

        rind = _wing_rind(master, outer_offset, width, props, coll, funnels)

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

        _drill_radial_bolts(mold, center, mn, mx, width, thickness, bolt_radius,
                            coll, n, _effective_bolts(props))
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
    finally:
        if backup is not None:
            bpy.data.meshes.remove(backup)
    return center


def _drill_radial_bolts(mold, center, mn, mx, width, thickness, bolt_radius,
                        coll, n, bolt_count=None):
    """Bolt holes down each seam flange. Each hole runs tangentially — perpendicular
    to the seam plane — centred on the seam, so after the radial split the two
    neighbouring wedges carry matching half-channels and a bolt pulls them together.
    ``bolt_count`` is per seam: ``None`` = automatic by height, 0 = no holes."""
    if bolt_count == 0:
        return                                     # bolts explicitly disabled
    if width < 2.2 * bolt_radius:
        return                                     # too narrow to fit a hole safely
    height = mx.z - mn.z
    nb = bolt_count or max(1, int(round(height / (max(width, bolt_radius) * 5.0))))
    big = (mx - mn).length * 2.0 + 10.0
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
                      bolt_radius, axis, coll, bolt_count=None, seam_off=0.0):
    """Bolt holes down each side, seated just inside the flange's outer edge and
    running along the split axis so a bolt pulls the two halves together.
    ``bolt_count`` per side: ``None`` = automatic by height, 0 = no holes."""
    if bolt_count == 0:
        return                                     # bolts explicitly disabled
    if width < 2.2 * bolt_radius:
        return                                     # too narrow to fit a hole safely
    height = mx.z - mn.z
    n = bolt_count or max(1, int(round(height / (max(width, bolt_radius) * 5.0))))
    big = (mx - mn).length * 2.0 + 10.0
    for sgn in (1.0, -1.0):
        for k in range(n):
            z = mn.z + height * (k + 1) / (n + 1)
            o = center.copy(); o[ai] = center[ai] + seam_off; o[2] = z; o[h] = center[h] + sgn * big
            d = Vector((0.0, 0.0, 0.0)); d[h] = -sgn
            hit, loc, _nrm, _idx = mold.ray_cast(o, d)
            if not hit:
                continue
            c = center.copy(); c[ai] = center[ai] + seam_off; c[2] = z
            c[h] = loc[h] - sgn * (bolt_radius + width * 0.3)
            hole = util.add_cone("MF_wbolt", c, bolt_radius, bolt_radius,
                                 thickness + 2.4, axis, coll)   # just pierce the flange
            util.boolean(mold, hole, 'DIFFERENCE')
            util.remove_object(hole)
