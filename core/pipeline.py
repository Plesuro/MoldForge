"""Top-level orchestration: mesh -> two-part silicone mold system.

Sizes are absolute (scene units). The mold is built centered at the origin for
clean geometry, then moved back onto the master's location so it lines up with
the model. If a detailed/thin model shards the offset shell, we retry once with
a coarse Safe Remesh.
"""

import math
import types

from mathutils import Matrix

from . import constants as C
from . import util, meshprep, build, sprue, split, volume


class MoldGeometryError(RuntimeError):
    """A geometry failure a coarse remesh might fix (shards, separate pieces,
    non-watertight output) — as opposed to bad input (NaN, no faces).

    ``largest_frac`` is the size of the biggest piece when a half came out
    disconnected; ~1.0 means only a minor fragment broke off (trimmable)."""

    def __init__(self, message, largest_frac=0.0):
        super().__init__(message)
        self.largest_frac = largest_frac


# A geometry-stage failure on one recovery rung shouldn't abort the ladder: split
# can raise RuntimeError ("empty half"), which is still recoverable by a different
# axis/remesh. Bad *input* (NaN, no faces, MF_ object) raises ValueError, which we
# deliberately let propagate.
_RECOVERABLE = (MoldGeometryError, RuntimeError)


def build_mold_system(master, props, auto_recover=True, progress=None):
    """Synchronous build, returning the result dict. If ``progress`` is given it is
    called ``progress(fraction, label)`` per phase. Drives the staged generator to
    completion — used by scripts and the headless tests."""
    gen = staged_build(master, props, auto_recover)
    try:
        while True:
            frac, label = next(gen)
            if progress:
                progress(frac, label)
    except StopIteration as stop:
        return stop.value


def prebuild_warnings(master, props):
    """Cheap checks to surface BEFORE a (possibly long) build, so the user isn't
    surprised by smoothing or a slow build. Returns a list of message strings."""
    out = []
    if master is None or master.type != 'MESH' or not master.data.polygons:
        return out
    faces = len(master.data.polygons)
    if getattr(props, "box_style", None) == 'TRAY':
        mn, mx = util.world_bbox(master)
        d = sorted((mx.x - mn.x, mx.y - mn.y, mx.z - mn.z))
        if d[2] > 1e-6 and d[0] > C.TRAY_FLAT_RATIO * d[2]:
            out.append("this object isn't flat — a Tray captures one face only; a "
                       "Pour Box or Direct Printed Mold suits a chunky 3D object better")
        if faces > C.HEAVY_FACES:
            out.append(f"heavy mesh (~{faces // 1000}k faces) — the build may take a while")
        return out
    if not getattr(props, "voxel_safe", False) and util.has_nonmanifold(master):
        out.append("model isn't watertight — it'll be auto-remeshed and fine surface "
                   "detail will be smoothed (or enable Safe Remesh to control it)")
    if faces > C.HEAVY_FACES:
        out.append(f"heavy mesh (~{faces // 1000}k faces) — the build may take a while")
    return out


def staged_build(master, props, auto_recover=True):
    """The build as a generator: yields ``(fraction, phase-label)`` as it works and
    returns the result dict (StopIteration.value). Drive it across modal ticks for
    live progress, or run it via ``build_mold_system``.

    Recovery ladder, least-destructive first: vary the split axis (a deep undercut
    often releases along one axis but not the other), then a coarse remesh (fixes a
    sharded shell), then drop the wings. First valid mold wins; else re-raise the
    most diagnostic error."""
    # The tray / open-pour type is a one-part build with its own short path — no
    # split, wings, funnel or recovery ladder.
    if getattr(props, "box_style", None) == 'TRAY':
        return (yield from _build_tray(master, props))
    try:
        return (yield from _build_once(master, props))
    except _RECOVERABLE as first_err:
        if not auto_recover or getattr(props, "voxel_safe", False):
            raise
        last_err = first_err

    base = _resolved_axis(master, props)
    alt = 'Y' if base == 'X' else 'X'

    # If only a minor fragment broke off a half (e.g. an open-bottom rim the split
    # severed), trimming is the real fix — go straight to the trim resort.
    tried_trim = getattr(last_err, "largest_frac", 0.0) >= 0.85
    if tried_trim:
        try:
            return (yield from _build_once(
                master, _trial_props(props, True, False, base), trim_ok=True))
        except _RECOVERABLE as e:
            last_err = e

    for remesh, drop_wings, axis in (
        (False, False, alt),
        (True, False, base), (True, False, alt),
        (True, True, base), (True, True, alt),
    ):
        try:
            return (yield from _build_once(
                master, _trial_props(props, remesh, drop_wings, axis)))
        except _RECOVERABLE as e:
            last_err = e

    # Final resort: coarse remesh + trim a *minor* severed fragment, unless that exact
    # trial already ran above.
    if not tried_trim:
        try:
            return (yield from _build_once(
                master, _trial_props(props, True, False, base), trim_ok=True))
        except _RECOVERABLE as e:
            last_err = e
    raise last_err


def _validate_master(master):
    """Reject bad input up front (raises ValueError) — shared by every build path."""
    if master is None or master.type != 'MESH':
        raise ValueError("Select a mesh object first.")
    if master.name.startswith("MF_"):
        raise ValueError("That's a generated MoldForge object — select your model instead.")
    if not master.data.polygons:
        raise ValueError("The selected mesh has no faces.")
    for v in master.data.vertices:
        co = master.matrix_world @ v.co
        if not (math.isfinite(co.x) and math.isfinite(co.y) and math.isfinite(co.z)):
            raise ValueError("The mesh has invalid (NaN/inf) vertex coordinates.")


def _build_once(master, props, trim_ok=False):
    _validate_master(master)

    # Where the master actually sits — the result is moved here at the end.
    omn, omx = util.world_bbox(master)
    home = (omn + omx) * 0.5

    coll = util.ensure_collection()
    for obj in list(coll.objects):
        if obj is not master and obj.name.startswith("MF_"):
            util.remove_object(obj)

    work = util.duplicate_object(master, "MF_Positive", coll)
    try:
        yield (0.05, "preparing mesh")
        # A non-manifold mesh makes the fast boolean solver bail (booleans get
        # silently skipped → a broken mold) and a very heavy mesh is painfully slow;
        # either way we voxel-remesh into a clean, light, watertight solid. When we're
        # going to remesh anyway, skip the heal — it's expensive on a heavy mesh and
        # the remesh cleans it regardless.
        need_clean = (not props.voxel_safe
                      and (util.nonmanifold_count(work) > 0
                           or len(work.data.polygons) > C.HEAVY_FACES))
        if not need_clean:
            if props.heal:
                meshprep.heal(work)
            else:
                meshprep.ensure_outward_normals(work)
        if props.decimate:
            meshprep.decimate(work, props.decimate_ratio)
        meshprep.center_object(work)

        p = _derive_sizes(work, props)

        # Never voxel-remesh at a voxel larger than the model can survive (a voxel ≥
        # the thinnest dimension collapses a small model to a blob/empty mesh); keep
        # at least ~4 voxels across the thinnest side.
        bmn, bmx = util.world_bbox(work)
        vox_cap = max(min(bmx.x - bmn.x, bmx.y - bmn.y, bmx.z - bmn.z) * 0.25, 0.05)

        # A direct (SOLID) printed mold's cavity IS the cast impression, so carve
        # it from a full-detail copy of the model rather than the coarse cleanup
        # remesh applied to `work` below. Only needed when `work` is about to be
        # remeshed (otherwise it still has the detail). Make the copy manifold with
        # a fine remesh only if it isn't already, so the cavity boolean is watertight.
        detail = None
        if props.box_style == 'SOLID' and (props.voxel_safe or need_clean):
            detail = util.duplicate_object(work, "MF_Detail", coll)
            if util.nonmanifold_count(detail) > 0:
                meshprep.voxel_remesh(detail, min(p.detail_voxel * 0.5, vox_cap))

        auto_remeshed = False
        if props.voxel_safe:
            meshprep.voxel_remesh(work, min(p.voxel_size, vox_cap))
        elif need_clean:
            meshprep.voxel_remesh(work, min(p.detail_voxel, vox_cap))
            auto_remeshed = True

        if util.island_count(work) > 1:
            raise MoldGeometryError(
                "The model is in separate pieces. Join them into one object, "
                "or it can't be molded as one."
            )

        cavity_volume = volume.mesh_volume(work)

        yield (0.30, "building the shell")
        mold, info = build.build_shell(work, p, coll, detail=detail)
        skin = info.get("skin")           # glove-mold silicone-skin preview, if any
        # Both a pour box AND a direct mold now come back SOLID with their cavity
        # cutter stashed: union the funnel/wings on first, then carve the cavity LAST
        # so the cut trims the funnel base flush (no wall lip / floating gap in the
        # opening). For a direct mold the cutter is the full-detail model.
        cavity_cutter = info.get("cavity_cutter")
        util.remove_small_islands(mold)   # drop solidify/boolean slivers up front

        # Add the solid funnel spouts first so the wings can run up them; the funnels
        # are bored open AFTER the wings, so wing material can never clog them.
        yield (0.45, "adding the pour funnel")
        funnels = sprue.add_funnel_spouts(mold, work, p, coll)

        axis = split.resolve_axis(p, work)   # AUTO picks the best-releasing pull axis
        undercut = util.undercut_fraction(work, axis)
        multipart = getattr(p, "parts_count", 2) >= 3
        radial_center = None        # centre the radial wings used; reused by the split
        yield (0.55, "adding clamp wings")
        if p.wings:
            if multipart:
                # One flange per radial seam, bolts tangential. The rind hugs the
                # *model's* profile, which a block's bounding box swallows — so a
                # block mold stays wingless in radial mode; clear the flag so the
                # radial split still adds seam pins (the wedges must register
                # somehow).
                if not p.block:
                    radial_center = build.add_radial_wings(
                        mold, coll, work, _offset(p),
                        p.wing_width, p.wing_thickness,
                        p.bolt_radius, p, p.parts_count,
                        funnels=funnels)
                else:
                    p.wings = False
            else:
                build.add_wings(mold, axis, coll, work, _offset(p), p.wing_width,
                                p.wing_thickness, p.bolt_radius, p,
                                block=p.block, funnels=funnels, cavity=cavity_cutter)

        # Horizontal Split: weld the bolted mating ring on while the mold is still
        # one solid (before the bores, like the wings), remember the seam height,
        # and cut the stacked pieces after the vertical split.
        hz = None
        if getattr(p, "split_horizontal", False) and not p.block:
            hmn, hmx = util.world_bbox(mold)
            hcap = (hmx.z - hmn.z) * 0.35
            hz = ((hmn.z + hmx.z) * 0.5
                  + max(min(getattr(p, "split_z_offset", 0.0), hcap), -hcap))
            if not getattr(p, "bolt_auto", True) and getattr(p, "bolt_count", 0) == 0:
                angles = []                       # bolts explicitly disabled
            elif multipart:
                angles = [2.0 * math.pi * (k + 0.5) / p.parts_count
                          for k in range(p.parts_count)]
            else:
                angles = [math.pi * 0.25 + k * math.pi * 0.5 for k in range(4)]
            build.add_horizontal_flange(mold, coll, work, _offset(p), p.wing_width,
                                        p.wing_thickness, p.bolt_radius, p, hz, angles)
        elif getattr(p, "split_horizontal", False):
            hmn, hmx = util.world_bbox(mold)
            hcap = (hmx.z - hmn.z) * 0.35
            hz = ((hmn.z + hmx.z) * 0.5
                  + max(min(getattr(p, "split_z_offset", 0.0), hcap), -hcap))

        # Carve the cavity now — after the funnel and wings are part of the body —
        # so the cut trims the funnel base flush with the cavity ceiling instead
        # of leaving the spout wall hanging into the opening as a lip.
        yield (0.70, "carving the cavity")
        if cavity_cutter is not None:
            util.boolean(mold, cavity_cutter, 'DIFFERENCE')
            util.remove_object(cavity_cutter)

        yield (0.80, "boring funnels & vents")
        sprue.bore_funnels_and_vents(mold, work, p, funnels, coll)

        yield (0.86, "shaping the base")
        plate = None
        if p.base_style == 'FLAT':
            build.flatten_base(mold, p, coll, _floor_max_cut(p))
            if p.base_flange:
                build.add_flange(mold, coll, p.flange_width, p.flange_thickness, p.bolt_radius)
        elif p.base_style == 'OPEN':
            if p.base_plate:
                # Detachable keyed bottom: cuts the shell open and returns the
                # separate plate (model pocket + pins; the rim gets the sockets
                # pre-split).
                plate = build.add_base_plate(mold, work, p, coll)
            else:
                build.cut_below_z(mold, util.world_bbox(work)[0].z, coll)

        # The skin preview must equal the real pourable silicone: the funnel
        # spout dips into the gap, and the plate's chin/ring displace silicone
        # too — cut all shell geometry out of the preview (best-effort, never
        # fails the build) and re-measure the pour from the corrected solid.
        if skin is not None:
            try:
                util.boolean(skin, mold, 'DIFFERENCE')
                if plate is not None:
                    util.boolean(skin, plate, 'DIFFERENCE')
                util.remove_small_islands(skin)
                if skin.data.polygons:
                    info["silicone_volume"] = volume.mesh_volume(skin)
                else:
                    util.remove_object(skin)
                    skin = None
            except Exception:
                pass

        yield (0.92, "splitting into parts")
        key_wall = p.shell_wall if p.box_style == 'POUR_BOX' else p.wall_thickness
        parts = split.split_parts(mold, work, axis, p, coll, key_wall,
                                  radial_center=radial_center)
        if hz is not None:
            parts = split.cut_horizontal(parts, coll, hz)
        if plate is not None:
            parts.append(plate)              # validated/moved/exported like any part

        trimmed = False
        for label, part in zip("ABCDEFGH", parts):
            ok, reason = util.part_is_valid(part)
            # Last resort: if a part lost a *minor* fragment (e.g. an open-bottom rim
            # the split severed), keep the main solid rather than fail the whole mold.
            if not ok and trim_ok and "disconnected" in reason:
                discarded = util.keep_largest_island(part)
                if discarded <= 0.15:
                    ok, reason = util.part_is_valid(part)
                    trimmed = trimmed or ok
            if not ok:
                if "disconnected" in reason:
                    hint = ("Try fewer Mold Pieces, or a closed Bottom (Flat/Follow)."
                            if multipart else
                            "Try a different Split Axis, more Mold Pieces, or a closed "
                            "Bottom (Flat/Follow).")
                    raise MoldGeometryError(
                        f"Generated mold piece {label} {reason} "
                        f"({util.island_report(part)}) — the model likely has a deep "
                        f"undercut or hollow that can't release. {hint}",
                        largest_frac=util.largest_island_fraction(part),
                    )
                raise MoldGeometryError(f"Generated mold piece {label} {reason}.")

        parts_volume = sum(volume.mesh_volume(part) for part in parts)
        if "silicone_volume" in info:
            silicone_volume = info["silicone_volume"]
            plastic_volume = parts_volume
        else:
            silicone_volume = parts_volume
            plastic_volume = 0.0

        # Move the result onto the master's location so it lines up with the model.
        for o in (work, *parts):
            o.location = home
        _try_hide(work)
        if skin is not None:
            skin.location = home          # visible: MF_Skin (the silicone you'll
                                          # pour) is a primary output
            util.apply_viewport_color(skin, "MF_Skin", (0.2, 0.7, 0.35, 0.9))

        return {
            "parts": parts,
            "positive": work,
            "skin": skin,
            "cavity_volume": cavity_volume,
            "silicone_volume": silicone_volume,
            "plastic_volume": plastic_volume,
            "remeshed": auto_remeshed or p.voxel_safe,
            "trimmed": trimmed,
            "undercut": undercut,
            "axis": axis,
        }
    except Exception:
        for obj in list(coll.objects):
            if obj.name.startswith("MF_"):
                util.remove_object(obj)
        raise


def _solid_centroid_z(me):
    """Volume centroid height of a (roughly closed) mesh, via signed tetrahedra about
    the origin. Used to tell which face is the detail side: on a relief-on-a-slab the
    mass sits toward the flat back, so the detail faces AWAY from the centroid."""
    cz = vol = 0.0
    for poly in me.polygons:
        vs = [me.vertices[i].co for i in poly.vertices]
        for k in range(1, len(vs) - 1):          # fan-triangulate the face
            a, b, c = vs[0], vs[k], vs[k + 1]
            v = a.dot(b.cross(c))                 # 6 * tetra volume
            vol += v
            cz += v * (a.z + b.z + c.z)           # 4 * centroid (the /6 and /4 cancel)
    return cz / (4.0 * vol) if abs(vol) > 1e-9 else 0.0


def _orient_tray(work, up):
    """Lay a flat object down so its detail face points +Z (the open pour side),
    baking the rotation into the mesh. Assumes ``work`` is already centred at the
    origin. ``up`` is the axis that should become vertical: AUTO picks the thinnest
    extent (a flat object's flat axis), then flips so the detailed face points up."""
    mn, mx = util.world_bbox(work)
    dims = mx - mn
    axis = min(range(3), key=lambda i: dims[i]) if up == 'AUTO' else {'X': 0, 'Y': 1, 'Z': 2}[up]
    if axis == 0:
        work.data.transform(Matrix.Rotation(math.radians(-90.0), 4, 'Y'))   # +X -> +Z
    elif axis == 1:
        work.data.transform(Matrix.Rotation(math.radians(90.0), 4, 'X'))    # +Y -> +Z
    work.data.update()

    if up == 'AUTO':
        me = work.data
        zs = [v.co.z for v in me.vertices]
        mid = (min(zs) + max(zs)) * 0.5
        # Mass toward the top means the detail (the light side) is at the bottom — flip
        # it up so the open pour face captures the relief.
        if _solid_centroid_z(me) > mid + 1e-6:
            work.data.transform(Matrix.Rotation(math.radians(180.0), 4, 'X'))
            work.data.update()


def _build_tray(master, props):
    """One-part open tray / pan mold for flat & relief objects (see build.build_tray).
    Generator: yields ``(fraction, label)`` and returns the result dict."""
    _validate_master(master)
    omn, omx = util.world_bbox(master)
    home = (omn + omx) * 0.5

    coll = util.ensure_collection()
    for obj in list(coll.objects):
        if obj is not master and obj.name.startswith("MF_"):
            util.remove_object(obj)

    work = util.duplicate_object(master, "MF_Positive", coll)
    try:
        yield (0.10, "preparing mesh")
        if props.heal:
            meshprep.heal(work)
        else:
            meshprep.ensure_outward_normals(work)
        if props.decimate:
            meshprep.decimate(work, props.decimate_ratio)
        if props.voxel_safe:
            bmn, bmx = util.world_bbox(work)
            vox_cap = max(min(bmx.x - bmn.x, bmx.y - bmn.y, bmx.z - bmn.z) * 0.25, 0.05)
            meshprep.voxel_remesh(work, min(props.voxel_size, vox_cap))

        yield (0.35, "orienting the object")
        meshprep.center_object(work)          # bake world transform, centre at origin
        _orient_tray(work, getattr(props, "tray_up", 'AUTO'))
        meshprep.center_object(work)          # re-centre after the reorientation

        yield (0.60, "building the tray")
        pan, info = build.build_tray(work, props, coll)

        yield (0.90, "finishing")
        ok, reason = util.part_is_valid(pan)
        if not ok:
            raise MoldGeometryError(f"Generated tray {reason}.")

        for o in (work, pan):
            o.location = home
        _try_hide(work)
        return {
            "parts": [pan],
            "positive": work,
            "skin": None,
            "cavity_volume": info["cast_volume"],
            "silicone_volume": info["silicone_volume"],
            "plastic_volume": info["plastic_volume"],
            "remeshed": bool(props.voxel_safe),
            "trimmed": False,
            "undercut": 0.0,
            "axis": 'Z',
            "tray_mode": getattr(props, "tray_mode", 'EMBED'),
        }
    except Exception:
        for obj in list(coll.objects):
            if obj.name.startswith("MF_"):
                util.remove_object(obj)
        raise


def _offset(props):
    """The dilation distance that can shard (gap, plus shell for a jacket)."""
    extra = props.shell_wall if props.box_style == 'POUR_BOX' else 0.0
    return props.wall_thickness + extra


def _resolved_axis(master, props):
    """The horizontal split axis that will actually be used ('X' or 'Y')."""
    if props.split_axis in ('X', 'Y'):
        return props.split_axis
    mn, mx = util.world_bbox(master)
    return 'X' if (mx.x - mn.x) >= (mx.y - mn.y) else 'Y'


def _trial_props(props, remesh, drop_wings, axis):
    """A copy of props for one recovery attempt: a forced split axis, plus optional
    coarse remesh (voxel ~0.7x the offset, so the shell stops self-intersecting) and
    optional wings-off."""
    ns = _snapshot(props)
    ns.split_axis = axis
    if remesh:
        ns.voxel_safe = True
        ns.voxel_size = max(0.7 * _offset(props), 0.3)
    if drop_wings:
        ns.wings = False
    return ns


_PROP_DEFAULTS_CACHE = None


def _prop_defaults():
    """Every property's default, read straight from the MoldForgeProperties
    definitions so the recovery snapshot can never drift out of sync with the real
    properties (the bug magnet was maintaining a hand-written copy). Works with or
    without the add-on registered — it reads the deferred property keywords, not the
    runtime RNA. Falls back to a minimal set if introspection ever fails."""
    global _PROP_DEFAULTS_CACHE
    if _PROP_DEFAULTS_CACHE is None:
        defaults = {}
        try:
            from .. import properties
            for name, deferred in properties.MoldForgeProperties.__annotations__.items():
                kw = getattr(deferred, "keywords", None)
                if kw is None and isinstance(deferred, tuple) and len(deferred) > 1:
                    kw = deferred[1]                      # older Blender: (func, kwargs)
                if isinstance(kw, dict) and "default" in kw:
                    defaults[name] = kw["default"]
        except Exception:
            defaults = {}
        if not defaults:                                  # safety net
            defaults = {"box_style": 'POUR_BOX', "wall_thickness": 3.0,
                        "shell_wall": 2.0, "sprue": True, "wings": True,
                        "split_axis": 'AUTO', "parts_count": 2}
        _PROP_DEFAULTS_CACHE = defaults
    return _PROP_DEFAULTS_CACHE


def _snapshot(props):
    return types.SimpleNamespace(
        **{k: getattr(props, k, d) for k, d in _prop_defaults().items()}
    )


def _derive_sizes(work, props):
    """Pass the user's absolute sizes through, and derive the secondary sizes
    (vents, keys, flange/wing details, remesh voxel) from them."""
    mn, mx = util.world_bbox(work)
    dims = mx - mn
    char = max((dims.x + dims.y + dims.z) / 3.0, 1e-4)

    wall = props.wall_thickness
    shell = props.shell_wall
    is_jacket = props.box_style == 'POUR_BOX'
    # The cavity gap around the model (the silicone: pour gap or glove skin).
    # Everything that references the gap (funnel breach, vents, keys) follows
    # from this single value.
    gap = wall
    offset = gap + (shell if is_jacket else 0.0)

    return types.SimpleNamespace(
        box_style=props.box_style,
        block=(props.box_style == 'SOLID'
               and getattr(props, "solid_shape", 'HUG') == 'BLOCK'),
        skin_keys=getattr(props, "skin_keys", False),
        heal=props.heal,
        decimate=props.decimate,
        decimate_ratio=props.decimate_ratio,
        voxel_safe=props.voxel_safe,
        voxel_size=max(props.voxel_size, 0.05),
        base_style=props.base_style,
        base_flange=getattr(props, "base_flange", True),
        base_plate=getattr(props, "base_plate", False),
        fit_clearance=getattr(props, "fit_clearance", 0.3),
        split_axis=props.split_axis,
        split_offset=getattr(props, "split_offset", 0.0),
        split_horizontal=getattr(props, "split_horizontal", False),
        split_z_offset=getattr(props, "split_z_offset", 0.0),
        contoured=getattr(props, "contoured", True),
        key_count=props.key_count,
        parts_count=getattr(props, "parts_count", 2),
        registration=getattr(props, "registration", 'KEYS'),
        sprue=props.sprue,
        funnel_height=getattr(props, "funnel_height", 12.0),
        sprue_flare=getattr(props, "sprue_flare", 2.4),
        big_mouth=getattr(props, "big_mouth", False),
        big_throat=getattr(props, "big_throat", False),
        sprue_count=getattr(props, "sprue_count", 1),
        sprue_place=getattr(props, "sprue_place", 'TOP'),
        sprue_x=getattr(props, "sprue_x", 0.0),
        sprue_y=getattr(props, "sprue_y", 0.0),
        vent_count=props.vent_count,
        wall_thickness=wall,
        silicone_gap=gap,
        shell_wall=shell,
        sprue_radius=props.sprue_radius,
        vent_radius=getattr(props, "vent_radius", 1.0),
        vent_spacing=max(0.3 * char, 2.0 * gap),
        key_radius=gap,
        key_depth=gap,
        flat_base_cut=gap,
        flange_width=props.flange_width,
        flange_thickness=max(wall, 3.0),
        bolt_radius=max(getattr(props, "bolt_diameter", 3.0) * 0.5, 0.5),
        bolt_auto=getattr(props, "bolt_auto", True),
        bolt_count=getattr(props, "bolt_count", 0),
        wings=getattr(props, "wings", True),
        wing_width=getattr(props, "wing_width", 8.0),
        wing_thickness=max(2.0 * shell, wall, 3.0),
        # Cleanup-remesh resolution. A pour box only prints a jacket (the real
        # model captures detail in the silicone), so a coarse, fast voxel keyed to
        # the wall is fine. A direct (SOLID) printed mold's cavity IS the cast
        # impression, so it must keep the model's detail: key the voxel to the
        # model size (~200 voxels across it), not the wall. Voxel remesh rebuilds
        # from surface area, so this stays light and manifold while sharp.
        detail_voxel=(min(max(char / C.DIRECT_VOXEL_DIV, C.DIRECT_VOXEL_MIN),
                          C.DIRECT_VOXEL_MAX)
                      if not is_jacket
                      else max(C.JACKET_VOXEL_FACTOR * offset, 0.2)),
    )


def _floor_max_cut(props):
    floor_wall = (props.shell_wall if props.box_style == 'POUR_BOX'
                  else props.wall_thickness)
    return max(floor_wall * 0.7, 0.0)


def _try_hide(obj):
    try:
        obj.hide_set(True)
    except RuntimeError:
        pass
