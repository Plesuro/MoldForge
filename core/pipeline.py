"""Top-level orchestration: mesh -> two-part silicone mold system.

Sizes are absolute (scene units). The mold is built centered at the origin for
clean geometry, then moved back onto the master's location so it lines up with
the model. If a detailed/thin model shards the offset shell, we retry once with
a coarse Safe Remesh.
"""

import math
import types

import bpy
from mathutils import Matrix, Vector

from . import constants as C
from . import util, meshprep, build, sprue, split, volume, channel
from . import label as mflabel
from . import legs as mflegs


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
    if (master.name.startswith("Core_Master")
            and getattr(props, "base_style", None) == 'LOCK'):
        out.append("this Core_Master already carries its socket cross - use "
                   "Bottom: Flat for its mold, or a plinth stacks under it and "
                   "the cast core won't clamp into the main mold's grooves")
    if (master.name.startswith("Core_Master")
            and getattr(props, "base_style", None) == 'OPEN'):
        out.append("this Core_Master already carries its legs - use Bottom: Flat "
                   "for its mold, so the legs are molded with it")
    if not getattr(props, "voxel_safe", False) and util.has_nonmanifold(master):
        out.append("model isn't watertight — the build works from an auto-remeshed "
                   "copy (the positive keeps full detail; a Direct mold's cavity "
                   "is smoothed; enable Safe Remesh to control the voxel size)")
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
        if getattr(props, "tray_mode", 'EMBED') == 'STAMP':
            return (yield from _build_stamp(master, props))
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
            res = yield from _build_once(
                master, _trial_props(props, remesh, drop_wings, axis))
            if drop_wings and getattr(props, "wings", False):
                res["wings_dropped"] = True       # the user asked for wings: say so
            return res
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


MAX_STACK_LEVELS = 8      # Printer Fit backstop: beyond this the mold is absurd anyway
MIN_LEVEL_HEIGHT = 25.0   # a smaller usable height is a typo (cm/inches?), not a printer


def _fit_seams(z0, z1, manual, maxh, z_hi, z_lo=None):
    """Printer Fit seam heights: keep the ``manual`` seams and equally subdivide
    any level that still spans more than ``maxh``, so every stacked level fits
    the printer. Generated seams are clamped to ``z_hi`` (below the funnel, so
    each mating ring has model wall to weld onto) and above ``z_lo`` (e.g. the
    sawtooth plinth of a united positive), kept a ring-width apart, and the
    stack is capped at MAX_STACK_LEVELS. Returns ``(seams, capped)``."""
    z_hi = max(z_hi, z0 + 6.0)
    seams = sorted(manual)
    bounds = [z0] + seams + [z1]
    needs = [max(int(math.ceil((bounds[i + 1] - bounds[i]) / maxh - 1e-6)), 1)
             for i in range(len(bounds) - 1)]
    budget = max((MAX_STACK_LEVELS - 1) - len(seams), 0)
    capped = sum(n - 1 for n in needs) > budget
    while sum(n - 1 for n in needs) > budget:
        # Cap FAIRLY: trim the most-subdivided region first. The old greedy fill
        # let the region below a manual seam eat the whole budget — the bottom
        # half came out in bits while the top half wasn't cut at all.
        i = max(range(len(needs)), key=lambda k: needs[k])
        if needs[i] <= 1:
            break
        needs[i] -= 1
    out = list(seams)
    for i, n in enumerate(needs):
        lo, hi = bounds[i], bounds[i + 1]
        for k in range(1, n):
            z = min(lo + (hi - lo) * k / n, z_hi)
            if z_lo is not None:
                z = max(z, z_lo)
            out.append(z)
    merged = []
    for z in sorted(out):
        if merged and z - merged[-1] < 8.0:
            continue
        merged.append(z)
    return merged, capped


def _build_once(master, props, trim_ok=False):
    _validate_master(master)

    # Where the master actually sits — the result is moved here at the end.
    omn, omx = util.world_bbox(master)
    home = (omn + omx) * 0.5

    coll = util.ensure_collection()
    # Dual-density second run: the master IS the Core_Master from the first run,
    # and the user still needs that first mold - rename its parts to Kept_*
    # instead of deleting them (a normal build wipes MF_* and stale kit copies).
    keep_prev = master is not None and master.name.startswith("Core_Master")
    kept_prev = 0
    for obj in list(coll.objects):
        if obj is master:
            continue
        if obj.name.startswith("MF_"):
            if keep_prev:
                obj.name = "Kept_" + obj.name
                kept_prev += 1
            else:
                util.remove_object(obj)
        elif obj.name.startswith("Core_Master") and not keep_prev:
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
        # User markers are placed in WORLD space around the master; the build is
        # centred at the origin, so shift them into build space here.
        p.vent_marks = ([m - home for m in util.marker_points("MF_VentMark")]
                        if getattr(p, "vent_place", 'AUTO') == 'MARKERS' else [])
        p.pour_marks = [m - home for m in util.marker_points("MF_PourMark")]
        # A Locking Base keeps MF_Positive (and the plinth) at the model's FULL detail,
        # so the working copy must NOT be voxel-remeshed — the jacket dilations clean
        # themselves instead. (This is why the old version showed a low-poly positive.)
        locking = (p.base_style == 'LOCK' and p.box_style == 'POUR_BOX')
        # An Open bottom carries the base formers (suction cup, Vac-U-Lock plug,
        # dual core) on legs resting on the shells' rim instead of a socket.
        open_base = p.base_style == 'OPEN' and p.box_style in ('POUR_BOX', 'SOLID')

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
        # A Locking Base builds its JACKET from a cleaned proxy — so a multi-island,
        # non-manifold or heavy sculpt molds robustly (the remesh merges stray islands
        # and makes it watertight), exactly like a normal pour box. But the displayed
        # positive stays HIGH-POLY: snapshot the original BEFORE any remesh. (Skipping
        # the remesh for locking was what made messy models fail the island gate and the
        # recovery ladder loop.)
        # EVERY mold type keeps the displayed/exported positive at the master's
        # full detail: snapshot BEFORE any voxel pass (this used to be Locking
        # Base only - a heavy or non-manifold sculpt came back faceted).
        hi_positive = util.duplicate_object(work, "MF_hipos", coll)
        dual_src = (util.duplicate_object(work, "MF_dualsrc", coll)
                    if (getattr(p, "dual_density", False) and (locking or open_base))
                    else None)
        dual_plinth = None
        plug_plinth = None
        if props.voxel_safe:
            meshprep.voxel_remesh(work, min(p.voxel_size, vox_cap))
        elif need_clean:
            meshprep.voxel_remesh(work, min(p.detail_voxel, vox_cap))
            auto_remeshed = True

        # The locking jacket is built from `work` (a proxy; the displayed positive stays
        # high-poly), so merge stray touching/overlapping islands into one moldable solid
        # here — a sculpt's separate sub-parts are typically meant as one object. A remesh
        # only fuses islands within a voxel of each other, so genuinely far-apart pieces
        # still fail the gate below (correctly: the user must join them).
        if (locking and not props.voxel_safe and not auto_remeshed
                and util.island_count(work) > 1):
            meshprep.voxel_remesh(work, min(p.detail_voxel, vox_cap))

        if util.island_count(work) > 1:
            raise MoldGeometryError(
                "The model is in separate pieces. Join them into one object, "
                "or it can't be molded as one."
            )

        yield (0.30, "building the shell")
        # ``shape_ref`` is the geometry every mold-shaping stage (funnel, axis, wings,
        # split) follows. For a Locking Base that's the cleaned jacket proxy.
        plinth_bottom_z = None
        lock_base_part = None
        base_united = None                # None = not applicable (no united base)
        wing_pocket = None                # Locking Base: socket pocket the wings avoid
        skin_cut = None                   # solid the silicone can't reach (the plinth)
        if locking:
            # Build the plinth AND its matching socket from the HIGH-POLY original (its
            # extruded base slices clean and high-res), NOT the blocky proxy — so the
            # sawtooth is crisp, and the socket's teeth match the plinth at any base height.
            plinth, socket = build.build_locking_plinth(hi_positive, p, coll)
            dual_plinth = (util.duplicate_object(plinth, "MF_dualplinth", coll)
                           if dual_src is not None else None)
            plug_plinth = (util.duplicate_object(plinth, "MF_plugplinth", coll)
                           if getattr(p, "anchor_plug", False) else None)
            plinth_bottom_z = util.world_bbox(plinth)[0].z
            plinth_top_z = util.world_bbox(plinth)[1].z
            # Build the jacket BEFORE joining the plinth into the positive: its smooth
            # outer skirt is sliced from hi_positive (still just the model here), while the
            # model-body cavity uses the merged proxy.
            mold, info = build.build_locking_jacket(work, hi_positive, plinth, socket, p, coll)
            # The silicone preview is the gap around the model; below the model's
            # base that gap is the plinth, where no silicone can go.
            skin_cut = (util.duplicate_object(plinth, "MF_skinplinth", coll)
                        if info.get("skin") is not None else None)
            # The air pocket above the socket under a hollow underside: the clamp
            # wings must never bridge the socket mouth through it (a bar along the
            # seam, flush with the socket top). Needs the plinth, so build it now.
            if p.wings:
                wing_pocket = build.socket_pocket_cutter(work, plinth, p, coll)
            work.name = "MF_proxy"
            hi_positive.name = "MF_Positive"
            if getattr(p, "lock_unite", True):
                # Displayed positive = HIGH-POLY original boolean-UNITED with the plinth:
                # ONE manifold solid, so resin slicers' hollowing treats the base as part
                # of the model (a plain join nests two shells and the base stays solid).
                # Same plinth as the jacket socket, so the master seats into the lock
                # exactly. On a messy sculpt the union can fail validation — then fall
                # back to the old overlapping join (prints the same) and report it.
                base_united = build.unite_plinth(hi_positive, plinth, coll)
                util.remove_object(plinth)
            else:
                # Unite Base with Model is OFF: keep the base as its own printable part
                # (exported/validated with the shells) and leave the positive pristine —
                # e.g. to print the base separately and attach the master to it.
                plinth.name = "MF_Mold_Base"
                # The plinth reaches 2 mm up INTO the model (the band that welds it
                # on when united). Separate, that band would sit inside the printed
                # master: carve the master out of it, so the base's top is a
                # cradle the master's underside drops into.
                build.cradle_base(plinth, hi_positive, coll)
                lock_base_part = plinth
            shape_ref = work
            positive = hi_positive
        else:
            mold, info = build.build_shell(work, p, coll, detail=detail)
            work.name = "MF_proxy"
            hi_positive.name = "MF_Positive"
            shape_ref = work
            positive = hi_positive

        cavity_volume = volume.mesh_volume(shape_ref)
        skin = info.get("skin")           # glove-mold silicone-skin preview, if any
        # Both a pour box AND a direct mold now come back SOLID with their cavity
        # cutter stashed: union the funnel/wings on first, then carve the cavity LAST
        # so the cut trims the funnel base flush (no wall lip / floating gap in the
        # opening). For a direct mold the cutter is the full-detail model.
        cavity_cutter = info.get("cavity_cutter")
        if cavity_cutter is not None:
            # A hollow sealed inside the cutter (the pocket between a Locking
            # Base plinth and a hollow underside) would leave plastic floating
            # in the silicone space: fill it.
            util.fill_voids(cavity_cutter)
        util.remove_small_islands(mold)   # drop solidify/boolean slivers up front

        # Add the solid funnel spouts first so the wings can run up them; the funnels
        # are bored open AFTER the wings, so wing material can never clog them.
        yield (0.45, "adding the pour funnel")
        funnels = sprue.add_funnel_spouts(mold, shape_ref, p, coll)

        axis = split.resolve_axis(p, shape_ref)   # AUTO picks the best-releasing pull axis
        p.parting_flat = None
        if locking and getattr(p, "contoured", True):
            # A contoured seam follows a curved model (a flat one can leave a
            # half in two pieces) but stays a flat plane through the plinth and
            # its socket - up to the socket shoulder's top - so each half's
            # share of the socket still pulls straight off the teeth.
            p.parting_flat = split.flat_zone(
                mold, axis, plinth_bottom_z, plinth_top_z,
                plinth_top_z + max(getattr(p, "lock_tolerance", 0.2), 0.0) + p.shell_wall)
            p.parting_blend = min(max(0.1 * (util.world_bbox(shape_ref)[1].z
                                             - util.world_bbox(shape_ref)[0].z), 8.0), 20.0)
        undercut = util.undercut_fraction(shape_ref, axis)
        multipart = getattr(p, "parts_count", 2) >= 3
        radial_center = None        # centre the radial wings used; reused by the split
        wing_bolt_zs = None         # bolt heights on 2-part wings; wing keys go between
        parting = None              # contoured parting shared by the wings and the split
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
                        mold, coll, shape_ref, _offset(p),
                        p.wing_width, p.wing_thickness,
                        p.bolt_radius, p, p.parts_count,
                        funnels=funnels, under=wing_pocket)
                else:
                    p.wings = False
            else:
                # The split follows the model's mid-depth (contoured parting); the
                # wings must be cut along that same surface or each height's wing
                # lands in one half only.
                parting = split.parting_surface(
                    mold, shape_ref, axis, p, coll,
                    pad=_offset(p) + p.wing_width + 10.0)
                wing_bolt_zs = build.add_wings(
                    mold, axis, coll, shape_ref, _offset(p), p.wing_width,
                    p.wing_thickness, p.bolt_radius, p,
                    block=p.block, funnels=funnels, cavity=cavity_cutter,
                    under=wing_pocket, parting=parting)
        if wing_pocket is not None:
            util.remove_object(wing_pocket)
            wing_pocket = None

        # Horizontal seams: the manual Horizontal Split, plus Printer Fit — extra
        # seams so every stacked level fits the printer's build height. Weld a
        # bolted mating ring on at every seam while the mold is still one solid
        # (before the bores, like the wings); cut the stacks after the vertical
        # split. Block molds get plain cuts (their thick flat walls are the lip).
        hmn, hmx = util.world_bbox(mold)
        seam_zs = []
        stack_capped = False
        if getattr(p, "split_horizontal", False):
            hcap = (hmx.z - hmn.z) * 0.35
            seam_zs.append((hmn.z + hmx.z) * 0.5
                           + max(min(getattr(p, "split_z_offset", 0.0), hcap), -hcap))
        maxh = (max(getattr(p, "max_print_height", 0.0), 0.0)
                if getattr(p, "printer_fit", False) else 0.0)
        sup = max(getattr(p, "support_clearance", 5.0), 0.0)
        # Supports/raft lift the print off the plate (resin especially), so a
        # level must fit WITH its supports: reserve that height out of the limit
        # (clamped so a silly value can never eat more than half the printer).
        usable = max(maxh - sup, maxh * 0.5) if maxh > 0.0 else 0.0
        # Sanity floor: a usable height under 25 mm is a typo (15 typed for a
        # 150 mm printer, cm instead of mm ...), not a real machine. Never shred
        # a mold into confetti over it — size levels to the floor and SAY SO.
        stack_floored = 0.0 < usable < MIN_LEVEL_HEIGHT
        if stack_floored:
            usable = MIN_LEVEL_HEIGHT
        if usable > 0.0 and (hmx.z - hmn.z) > usable:
            # Generated seams stay at or below the model's top so every mating
            # ring has real wall to weld onto (a seam through the bare funnel
            # neck would stack with no ring).
            smx_z = util.world_bbox(shape_ref)[1].z
            seam_zs, stack_capped = _fit_seams(
                hmn.z, hmx.z, seam_zs, usable,
                z_hi=min(smx_z - 3.0, hmx.z - 6.0))
        if seam_zs and not p.block:
            if not getattr(p, "bolt_auto", True) and getattr(p, "bolt_count", 0) == 0:
                angles = []                       # bolts explicitly disabled
            elif multipart:
                angles = [2.0 * math.pi * (k + 0.5) / p.parts_count
                          for k in range(p.parts_count)]
            else:
                angles = [math.pi * 0.25 + k * math.pi * 0.5 for k in range(4)]
            for sz in seam_zs:
                build.add_horizontal_flange(mold, coll, shape_ref, _offset(p),
                                            p.wing_width, p.wing_thickness,
                                            p.bolt_radius, p, sz, angles)

        # Bottom-fill channel: a pipe on the parting seam with a gate into the
        # cavity at its floor. Welded on now (the mold is still solid, so the
        # cavity carve trims anything inside it) and bored after the carve.
        chan = None
        chan_note = ""
        if getattr(p, "pour_channel", False) and cavity_cutter is not None:
            if parting is None and not multipart:
                parting = split.parting_surface(
                    mold, shape_ref, axis, p, coll,
                    pad=_offset(p) + p.wing_width + 10.0)
            if multipart and radial_center is None:
                cmn_, cmx_ = util.world_bbox(mold)
                radial_center = (cmn_ + cmx_) * 0.5   # the split must cut the pipe
            mmin_z = util.world_bbox(shape_ref)[0].z
            open_below = False
            if p.base_style == 'OPEN':
                if p.base_plate:
                    floor_z = build.plate_levels(p, mmin_z)[1]
                else:
                    floor_z, open_below = mmin_z, True
            elif locking:
                floor_z = mmin_z
            else:
                floor_z = util.world_bbox(cavity_cutter)[0].z
            try:
                frame = channel.seam_frame(mold, axis, p, parting=parting,
                                           radial_center=radial_center,
                                           flip=getattr(p, "channel_flip", False))
                chan, chan_note = channel.build_channel(
                    mold, cavity_cutter, shape_ref, p, frame, floor_z, coll,
                    open_below=open_below)
            except Exception as exc:                  # never fails the mold
                chan, chan_note = None, f"the bottom-fill channel failed ({exc})"
                for o in list(coll.objects):
                    if o.name.startswith(("MF_chan",)):
                        util.remove_object(o)

        # Stand legs: the cured silicone stays in these shells for casting, turned
        # over onto the funnel's mouth - the legs keep it from tipping.
        legs_built, legs_note, legs_info = 0, "", None
        if getattr(p, "stand_legs", False):
            if p.box_style == 'POUR_BOX' and (p.base_style == 'OPEN' or locking):
                if multipart and radial_center is None:
                    lmn_, lmx_ = util.world_bbox(mold)
                    radial_center = (lmn_ + lmx_) * 0.5   # wedges cut between the legs
                keep = [(f["x"], f["y"], f["mouth_out"]) for f in funnels]
                if chan is not None:
                    keep.append(chan["cup"])
                try:
                    legs_built, legs_note, legs_info = mflegs.add_stand_legs(
                        mold, shape_ref, p, coll, axis, radial_center=radial_center,
                        keep_out=keep)
                except Exception as exc:              # never fails the mold
                    legs_built, legs_note = 0, f"stand legs skipped ({exc})"
            else:
                legs_note = ("stand legs skipped - they are for a Pour Box with an Open "
                             "Bottom or Locking Base (cast through the open base, the "
                             "mold turned over)")

        if (getattr(p, "wing_band_notches", False) and p.wings and not multipart
                and wing_bolt_zs is not None):
            # after the channel, and never on its side: its fin is measured
            # against the uncut wing, and the band rests on the pipe there
            skip = None
            if chan is not None and not chan["frame"].multipart:
                skip = chan["frame"].d[chan["frame"].h]
            build.cut_band_notches(mold, shape_ref, axis, p, coll, parting=parting,
                                   offset=_offset(p), bolt_zs=wing_bolt_zs, skip_side=skip)
        elif (getattr(p, "wing_band_notches", False) and p.wings and multipart
              and radial_center is not None):
            # one row up each seam's flange; not the channel's seam (the first)
            build.cut_radial_band_notches(
                mold, shape_ref, p, coll, radial_center, p.parts_count,
                offset=_offset(p), bolt_zs=getattr(p, "radial_bolt_zs", None),
                skip=(0,) if chan is not None else ())

        # Carve the cavity now — after the funnel and wings are part of the body —
        # so the cut trims the funnel base flush with the cavity ceiling instead
        # of leaving the spout wall hanging into the opening as a lip.
        yield (0.70, "carving the cavity")
        label_cav = None
        if getattr(p, "label_on", False) and cavity_cutter is not None:
            label_cav = util.duplicate_object(cavity_cutter, "MF_labelcav", coll)
        if cavity_cutter is not None:
            util.boolean(mold, cavity_cutter, 'DIFFERENCE')
            util.remove_object(cavity_cutter)

        yield (0.80, "boring funnels & vents")
        sprue.bore_funnels_and_vents(mold, shape_ref, p, funnels, coll)
        if chan is not None:
            channel.bore_channel(mold, chan, coll)


        yield (0.86, "shaping the base")
        plate = None
        cup = None
        rim = None                        # Open bottom: where the formers' legs sit
        screw = None
        skin_floor = None                 # the cast's opening plane: no silicone below
        if locking:
            # Open the shells across the footprint at the plinth's bottom, so the base
            # plugs up into the socket (the bottom prints open — no floor).
            build.cut_below_z(mold, plinth_bottom_z, coll)
            if getattr(p, "suction_cup", False):
                # The former seats at the shells' ACTUAL bottom (the plinth bottom,
                # Base Height below the model) and its dome is sized to the sawtooth
                # socket opening — both measured off the mold itself.
                cup = build.build_suction_cup(mold, shape_ref, p, coll)
        elif p.base_style == 'FLAT':
            build.flatten_base(mold, p, coll, _floor_max_cut(p))
            if p.base_flange:
                build.add_flange(mold, coll, p.flange_width, p.flange_thickness, p.bolt_radius)
        elif p.base_style == 'OPEN':
            screw_on = bool(p.base_plate and getattr(p, "plate_screw", False))
            if p.base_plate:
                # Detachable keyed bottom: cuts the shell open and returns the
                # separate plate (model pocket + pins; the rim gets the sockets
                # pre-split). Thickened for the countersunk screw when asked.
                plate = build.add_base_plate(
                    mold, work, p, coll,
                    min_floor=build.screw_floor(p) if screw_on else 0.0,
                    pocket_src=positive)
                open_z = plate.get("mf_plate_top", util.world_bbox(work)[0].z)
                if screw_on:
                    # Screw the master to the plate instead of gluing it.
                    screw = build.add_plate_screw(plate, positive, p, coll)
            else:
                open_z = util.world_bbox(work)[0].z
                build.cut_below_z(mold, open_z, coll)
            skin_floor = open_z
            want_plug = bool(getattr(p, "anchor_plug", False) and open_base)
            if open_base and (getattr(p, "suction_cup", False) or want_plug
                              or dual_src is not None):
                # The formers' four legs rest on the shells' bottom rim: measure
                # it once, before the split, and cut ONE set of fastening grooves
                # wide enough for every former that seats there.
                fcx, fcy = build.former_centre(shape_ref, open_z)
                rim = build.rim_layout(mold, fcx, fcy, open_z, p)
                leg_w = 0.0
                if getattr(p, "suction_cup", False):
                    # Suction-cup former: a printed dome-on-legs that seats over the
                    # open base; pressed into the pour it leaves a suction bell in
                    # the cast. A separate part, exported with the shells.
                    cup = build.build_suction_cup(mold, shape_ref, p, coll,
                                                  base_z=open_z, lay=rim)
                    leg_w = float(cup.get("mf_leg_w", build.FORMER_LEG_W))
                if want_plug:
                    leg_w = max(leg_w, build.FORMER_LEG_W)
                if leg_w > 0.0:
                    build.cut_rim_grooves(mold, rim, coll, leg_w)

        # Park the former BESIDE the mold in the scene. Built in place it nests inside
        # the socket — interpenetrating the positive/plinth on screen, which reads as
        # broken geometry (it isn't: the positive is out when you cast). Scene position
        # is display only; the exported STL is identical.
        if cup is not None:
            bmn_, _bmx_ = util.world_bbox(mold)
            cmn_, cmx_ = util.world_bbox(cup)
            cup.data.transform(Matrix.Translation((bmn_.x - 8.0 - cmx_.x, 0.0, 0.0)))
            cup.data.update()

        # The skin preview must equal the real pourable silicone: the funnel
        # spout dips into the gap, and the plate's chin/ring displace silicone
        # too — cut all shell geometry out of the preview (best-effort, never
        # fails the build) and re-measure the pour from the corrected solid.
        if skin is not None:
            try:
                util.boolean(skin, mold, 'DIFFERENCE')
                if plate is not None:
                    util.boolean(skin, plate, 'DIFFERENCE')
                if skin_cut is not None:
                    util.boolean(skin, skin_cut, 'DIFFERENCE')
                # The gap around the model also wraps under its base. With an open
                # bottom the master stands on the table (or the key plate) there, so
                # that part hung below the case in the preview and was counted in
                # the silicone to mix.
                build.cut_below_z(skin, skin_floor if skin_floor is not None
                                  else util.world_bbox(mold)[0].z, coll)
                util.remove_small_islands(skin)
                if skin.data.polygons:
                    info["silicone_volume"] = volume.mesh_volume(skin)
                else:
                    util.remove_object(skin)
                    skin = None
            except Exception:
                pass
        if skin_cut is not None:
            util.remove_object(skin_cut)
            skin_cut = None

        # Label: debossed into a flat pad on one shell, now that the silicone
        # figure is final. Best-effort: a label never fails the mold.
        label_note = ""
        if getattr(p, "label_on", False):
            try:
                sil = info.get("silicone_volume", volume.mesh_volume(mold))
                fields = mflabel.label_fields(p, master.name, sil, cavity_volume)
                text = getattr(p, "label_text", "")
                if p.box_style != 'POUR_BOX' and text == mflabel.DEFAULT_TEXT:
                    text = "{cast_g} g cast"      # a direct mold pours no silicone
                lines = mflabel.format_label(text, fields)
                if multipart:
                    a = math.pi / max(getattr(p, "parts_count", 3), 3)
                    out_dir = Vector((math.cos(a), math.sin(a), 0.0))
                else:
                    out_dir = Vector((0.0, 0.0, 0.0))
                    out_dir[{'X': 0, 'Y': 1}.get(axis, 0)] = 1.0
                if lines:
                    _ok, label_note = mflabel.add_label(mold, shape_ref, label_cav,
                                                      out_dir, lines, p, coll)
            except Exception as exc:
                label_note = f"label skipped ({exc})"
        if label_cav is not None:
            util.remove_object(label_cav)
            label_cav = None

        yield (0.92, "splitting into parts")
        key_wall = p.shell_wall if p.box_style == 'POUR_BOX' else p.wall_thickness
        _smn, _smx = util.world_bbox(mold)       # where a flat parting will cut
        _sai = {'X': 0, 'Y': 1}.get(axis, 0)
        _scap = (_smx[_sai] - _smn[_sai]) * 0.4
        seam_flat = (_smn[_sai] + _smx[_sai]) * 0.5 + max(
            min(getattr(p, "split_offset", 0.0), _scap), -_scap)
        parts = split.split_parts(mold, shape_ref, axis, p, coll, key_wall,
                                  radial_center=radial_center,
                                  wing_bolt_zs=wing_bolt_zs, parting=parting)
        if parting is not None and parting.used is False and wing_bolt_zs is not None:
            build.wing_note(p, "the contoured parting did not cut cleanly and fell "
                               "back to a flat cut, so the clamp wings may be split "
                               "unevenly between the halves - turn Contoured off for "
                               "this model")
        if (p.wings and not multipart and not p.block and wing_bolt_zs is not None
                and len(parts) == 2):
            # Measure it rather than trust it: both halves need a wing at every
            # height, or that part of the seam has nothing to clamp.
            used = parting if (parting is not None and parting.used) else None
            frac, n_chk = build.wing_coverage(parts, shape_ref, axis, _offset(p),
                                              p.wing_width, p.wing_thickness,
                                              parting=used, seam_flat=seam_flat)
            if n_chk and frac < 0.85:
                build.wing_note(p, f"the clamp wings are missing on one or both halves "
                                   f"along about {100 * (1 - frac):.0f}% of the seam - "
                                   f"try another Split axis or a wider Wing Width")
        if parting is not None:
            util.remove_object(parting.cutter)
            parting = None
        if seam_zs:
            parts = split.cut_stack(parts, coll, seam_zs)
        # Printer Fit honesty: did every level actually land under the limit
        # (with the support reserve)? A huge funnel spout above the last ring
        # can keep the top level tall.
        stack_over = bool(usable > 0.0 and any(
            (lambda b: b[1].z - b[0].z)(util.world_bbox(q)) > usable + 0.5
            for q in parts))
        # Paper-blade shave: a vent bore that grazed a seam plane or a sloped
        # outer wall leaves a sub-millimetre blade standing on the shell (the
        # needle at a Locking Base's open bottom). Shave any off the shells.
        blades_shaved = 0
        for q in parts:
            try:
                blades_shaved += util.shave_thin_blades(q)
            except Exception:
                pass                          # cosmetic pass - never fail a build

        if plate is not None:
            parts.append(plate)              # validated/moved/exported like any part
        if lock_base_part is not None:
            parts.append(lock_base_part)     # separate sawtooth base, same treatment
        if cup is not None:
            parts.append(cup)                # suction-cup former, same treatment
        util.remove_object(work)            # the build proxy — never a displayed output

        # Zero-area slits the fast boolean leaves where it merges coplanar
        # pieces: their triangles fold back (black in a render, inverted
        # facets in the STL). Weld them shut before the parts are checked.
        for part in parts:
            util.fix_folded_faces(part)

        trimmed = False
        split_notes = []
        checked = []
        for label, part in zip("ABCDEFGH", parts):
            ok, reason = util.part_is_valid(part)
            if not ok and "disconnected" in reason and not multipart:
                # A flat cut through a model that curves away from it can leave
                # a half in two real pieces (one height has all the model on the
                # other side of the plane). Those pieces are mold wall: keep each
                # as its own printable part instead of throwing one away. Only
                # slivers are dropped.
                pieces, dropped = util.split_islands(part, coll)
                if all(util.part_is_valid(q)[0] for q in pieces):
                    ok, reason = True, ""
                    trimmed = trimmed or dropped > 0
                    if len(pieces) > 1:
                        split_notes.append(
                            f"half {label} came out in {len(pieces)} pieces "
                            f"({', '.join(q.name for q in pieces)}): the model curves "
                            f"away from the parting at some height - print them all, or "
                            f"try the other Split axis")
                    checked.extend(pieces)
                    continue
            # Last resort: if a part lost a *minor* fragment (e.g. an open-bottom rim
            # the split severed), keep the main solid rather than fail the whole mold.
            if not ok and trim_ok and "disconnected" in reason:
                discarded = util.keep_largest_island(part)
                if discarded <= 0.02:
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
            checked.append(part)
        parts = checked

        parts_volume = sum(volume.mesh_volume(part) for part in parts)
        if "silicone_volume" in info:
            silicone_volume = info["silicone_volume"]
            plastic_volume = parts_volume
        else:
            silicone_volume = parts_volume
            plastic_volume = 0.0

        # Dual-density kit: Core_Master = shrunk model on a socket cross carved
        # from the same sawtooth plinth - the cast core's lips clamp into the
        # shells' grooves when the halves bolt shut. Parked to the +x side.
        dual_core = None
        if dual_src is not None and (dual_plinth is not None or rim is not None):
            if dual_plinth is not None:
                dual_core = build.build_dual_kit(dual_src, dual_plinth, p, coll)
                util.remove_object(dual_plinth)
            else:
                dual_core = build.build_dual_kit_open(dual_src, rim, p, coll)
            kxmx = max(util.world_bbox(q)[1].x for q in parts)
            dmn, dmx = util.world_bbox(dual_core)
            dual_core.data.transform(Matrix.Translation((
                kxmx + 12.0 - dmn.x, 0.0, 0.0)))

        # Vac-U-Lock-style anchor former: a ribbed plug on its own socket
        # cross; seat it instead of the core when casting a toy that needs the
        # attachment channel in its base. Parked to the -x side.
        plug_obj = None
        plug_skipped = False
        plug_note = ""
        plug_offset = (0.0, 0.0)
        plug_wall = None
        plug_open = (rim is not None and open_base
                     and getattr(p, "anchor_plug", False))
        if plug_plinth is not None or plug_open:
            if plug_plinth is not None:
                plug_obj, plug_note = build.build_anchor_plug(
                    plug_plinth, util.world_bbox(hi_positive)[1].z, p, coll,
                    model=hi_positive)
            else:
                plug_obj, plug_note = build.build_anchor_plug_open(
                    hi_positive, rim, open_z, p, coll)
            if plug_obj is None:
                plug_skipped = True          # too small, or no spot inside the toy
            else:
                plug_offset = tuple(plug_obj.get("mf_plug_offset", (0.0, 0.0)))
                plug_wall = plug_obj.get("mf_plug_wall")
                pxmn = min(util.world_bbox(q)[0].x for q in parts)
                amn_, amx_ = util.world_bbox(plug_obj)
                plug_obj.data.transform(Matrix.Translation((
                    pxmn - 10.0 - amx_.x, 0.0, 0.0)))
                parts.append(plug_obj)       # exported like any printed part


        # Printer Fit for the POSITIVE too — it's a print (the Locking Base one
        # especially). Nothing may be added to its OUTSIDE (the cast forms
        # against it), so the joinery lives on the SECTION FACES: raised pegs on
        # one face seat into clearance sockets in the next — stack, glue, done.
        # Best-effort with a full snapshot: if cutting a messy mesh fails, the
        # whole positive is restored and the too-tall warning stands.
        pos_parts = [positive]
        pos_keys = 0
        if usable > 0.0 and getattr(p, "fit_positive", True):
            pmn2, pmx2 = util.world_bbox(positive)
            if (pmx2.z - pmn2.z) > usable + 0.5:
                # An already-messy positive (multi-island sculpt, non-manifold
                # source that defeated the plinth union) can never yield cleaner
                # sections than itself — demand strict validity only from a
                # clean source, else accept plausible sections (they print the
                # same as the whole did).
                src_clean = (util.island_count(positive) == 1
                             and util.nonmanifold_count(positive) == 0)
                pbak = positive.data.copy()
                pmw = positive.matrix_world.copy()
                try:
                    z_lo = (pmn2.z + p.lock_height + 3.0) \
                        if (locking and getattr(p, "lock_unite", True)) else None
                    # Sections carry a ~6 mm peg above their face, so size them
                    # to the usable height MINUS the peg — the printed piece
                    # (body + peg) still fits the plate.
                    pos_seams, pcap = _fit_seams(pmn2.z, pmx2.z, [],
                                                 max(usable - 6.0, 15.0),
                                                 z_hi=pmx2.z - 4.0, z_lo=z_lo)
                    if pos_seams:
                        sections = split.cut_stack([positive], coll, pos_seams)
                        ok = (len(sections) == len(pos_seams) + 1
                              and all(len(s.data.polygons) > 12 for s in sections))
                        if ok and src_clean:
                            ok = all(util.part_is_valid(s)[0] for s in sections)
                        if ok:
                            pos_parts = sections
                            positive = sections[0]
                            stack_capped = stack_capped or pcap
                            pos_keys = build.add_section_keys(
                                sections, pos_seams, p.bolt_radius,
                                max(getattr(p, "fit_clearance", 0.2), 0.0), coll)
                        else:
                            raise MoldGeometryError("positive sections came out broken")
                except Exception:
                    pos_keys = 0
                    for o in list(coll.objects):
                        if o.name.startswith(("MF_seg", "MF_hcut", "MF_key",
                                              "MF_Positive")):
                            util.remove_object(o)
                    positive = bpy.data.objects.new("MF_Positive", pbak)
                    positive.matrix_world = pmw
                    coll.objects.link(positive)
                    pbak = None
                    pos_parts = [positive]
                finally:
                    if pbak is not None and pbak.users == 0:
                        bpy.data.meshes.remove(pbak)

        # Move the result onto the master's location so it lines up with the model.
        for o in (*pos_parts, *parts):
            o.location = home
        if dual_core is not None:
            dual_core.location = home
        # The printable positive stays VISIBLE - it replaces the model on
        # screen. The ORIGINAL master is what gets hidden: both occupy the
        # same spot, and the parts you print are the ones you should see.
        _try_hide(master)
        if skin is not None:
            skin.location = home          # visible: MF_Skin (the silicone you'll
                                          # pour) is a primary output
            util.apply_viewport_color(skin, "MF_Skin", (0.2, 0.7, 0.35, 0.9))

        return {
            "parts": parts,
            "positive": positive,
            "skin": skin,
            "cavity_volume": cavity_volume,
            "silicone_volume": silicone_volume,
            "plastic_volume": plastic_volume,
            # The positive is always the hi-poly snapshot now, and a pour box
            # captures detail from the master you nest — a proxy remesh only
            # costs detail on a DIRECT mold, whose cavity is carved from it.
            "remeshed": (auto_remeshed or p.voxel_safe) and p.box_style == 'SOLID',
            "base_united": base_united,
            "stack_levels": (len(seam_zs) + 1) if seam_zs else None,
            "stack_capped": stack_capped,
            "stack_floored": stack_floored,
            "stack_over": stack_over,
            "dual_core": dual_core.name if dual_core is not None else None,
            "kept_prev": kept_prev,
            "blades_shaved": blades_shaved,
            "plug_skipped": plug_skipped,
            "plug_note": plug_note,
            "plug_offset": plug_offset,
            "plug_wall": plug_wall,
            "wing_notes": list(getattr(p, "wing_report", [])),
            "label_note": label_note,
            "legs": legs_built,
            "legs_note": legs_note,
            "legs_tip": legs_info["tip_deg"] if legs_info else None,
            "channel": chan is not None,
            "channel_note": chan_note,
            "screw_ok": None if screw is None else screw["ok"],
            "screw_note": "" if screw is None else screw["note"],
            "screw_spot": (None if not (screw and screw["ok"])
                           else (screw["x"], screw["y"])),
            "positive_parts": pos_parts,
            "positive_sections": len(pos_parts) if len(pos_parts) > 1 else None,
            "positive_keys": pos_keys,
            "positive_over": bool(usable > 0.0 and any(
                (lambda b: b[1].z - b[0].z)(util.world_bbox(q)) > usable + 0.5
                for q in pos_parts)),
            "trimmed": trimmed,
            "split_notes": split_notes,
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


def _svg_curves(path):
    """Import an SVG and return its curve objects (best effort across bundled
    importer states)."""
    before = set(bpy.data.objects)
    try:
        bpy.ops.import_curve.svg(filepath=path)
    except Exception:
        try:
            import addon_utils
            addon_utils.enable("io_curve_svg")
            bpy.ops.import_curve.svg(filepath=path)
        except Exception as exc:
            raise ValueError(f"Could not import the SVG: {exc}")
    new = [o for o in bpy.data.objects if o not in before and o.type == 'CURVE']
    if not new:
        raise ValueError("The SVG imported no curves - export the artwork with "
                         "FILLED paths (in Inkscape: select all, Path > "
                         "Object to Path, and Stroke to Path for outlines).")
    return new


def _design_to_solid(sources, thickness, coll):
    """Fill + extrude Curve/Text objects into ONE mesh solid of ``thickness``
    (centred on z=0), consuming nothing (works on evaluated copies)."""
    deps = bpy.context.evaluated_depsgraph_get()
    solid = None
    half = thickness * 0.5
    for src in sources:
        if src.type not in ('CURVE', 'FONT'):
            continue
        dup = src.copy()
        dup.data = src.data.copy()
        coll.objects.link(dup)
        if src.type == 'CURVE':
            try:
                dup.data.dimensions = '2D'
                dup.data.fill_mode = 'BOTH'
            except Exception:
                pass
        dup.data.extrude = half
        dup.data.bevel_depth = 0.0
        deps = bpy.context.evaluated_depsgraph_get()
        me = bpy.data.meshes.new_from_object(
            dup.evaluated_get(deps), preserve_all_data_layers=False, depsgraph=deps)
        util.remove_object(dup)
        if not me.polygons:
            bpy.data.meshes.remove(me)
            continue
        me.transform(src.matrix_world)      # bake the source transform in place -
        ob = bpy.data.objects.new("MF_StampRelief", me)   # NEVER recentre per
        coll.objects.link(ob)               # shape (that piles an SVG's shapes
        if solid is None:                   # on top of each other)
            solid = ob
            solid.name = "MF_StampRelief"
        else:
            util.join_meshes(solid, ob)
    if solid is None:
        raise ValueError("The design has no filled area to engrave - use FILLED "
                         "shapes (a Text object, or an SVG with filled paths).")
    # Curve-to-mesh leaves the extrusion CAPS as separate faces duplicating the
    # wall vertices (6 islands, hundreds of non-manifold edges on a 2-shape
    # SVG): weld the duplicates into one closed shell per shape.
    import bmesh as _bmesh
    bm = _bmesh.new()
    bm.from_mesh(solid.data)
    _bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-5)
    _bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(solid.data)
    bm.free()
    # SVG curves often import with reversed winding; an inside-out solid used as
    # a boolean cutter consumes the whole pan. Make every shell face outward.
    meshprep.ensure_outward_normals(solid)
    # Auto-flatten: an imported SVG often carries a baked -90 degree X rotation
    # (and wild scales), leaving the design standing VERTICALLY - the engrave
    # would be garbage. The extrusion axis is the one whose extent matches the
    # known cutter thickness; rotate it onto Z.
    fmn, fmx = util.world_bbox(solid)
    ext = (fmx.x - fmn.x, fmx.y - fmn.y, fmx.z - fmn.z)
    thin = min(range(3), key=lambda i: abs(ext[i] - thickness))
    if thin == 0:
        solid.data.transform(Matrix.Rotation(math.radians(90.0), 4, 'Y'))
    elif thin == 1:
        solid.data.transform(Matrix.Rotation(math.radians(90.0), 4, 'X'))
    return solid


def _build_stamp(design, props):
    """Silicone INK-STAMP mold: engrave the artwork (selected Text/Curve object,
    or the SVG file) into a pour-ready pan floor. Generator like the tray."""
    coll = util.ensure_collection()
    for obj in list(coll.objects):
        if obj is not design and obj.name.startswith("MF_"):
            util.remove_object(obj)

    yield (0.15, "loading the design")
    imported = []
    if design is not None and design.type in ('CURVE', 'FONT'):
        # A manually imported SVG is often MANY curve objects - take every
        # selected Curve/Text so multi-path logos come through whole.
        try:
            sel = [o for o in bpy.context.selected_objects
                   if o.type in ('CURVE', 'FONT')]
        except Exception:
            sel = []
        sources = sel if (design in sel and len(sel) > 1) else [design]
    else:
        path = bpy.path.abspath(getattr(props, "stamp_svg", "") or "")
        if not path:
            raise ValueError("Select a Text/Curve object, or set an SVG file, "
                             "for the stamp design.")
        imported = _svg_curves(path)
        sources = imported

    try:
        yield (0.35, "extruding the relief")
        depth = max(getattr(props, "stamp_relief", 2.0), 0.3)
        relief = _design_to_solid(sources, depth + 1.2, coll)

        # Scale uniformly to the requested stamp width, optional mirror, centre.
        rmn, rmx = util.world_bbox(relief)
        w = max(rmx.x - rmn.x, 1e-4)
        sc = max(getattr(props, "stamp_width", 60.0), 1.0) / w
        sx = -sc if getattr(props, "stamp_mirror", False) else sc
        relief.data.transform(Matrix.Diagonal((sx, sc, 1.0, 1.0)))
        if sx < 0:
            relief.data.flip_normals()
        # The import scale also shrank the extruded THICKNESS (an SVG imports at
        # ~1/38 scale, leaving a paper-thin wafer that seals inside the floor):
        # normalize the z-span back to the intended cutter thickness.
        rmn2, rmx2 = util.world_bbox(relief)
        zs = max(rmx2.z - rmn2.z, 1e-5)
        relief.data.transform(Matrix.Diagonal((1.0, 1.0, (depth + 1.2) / zs, 1.0)))
        meshprep.center_object(relief)

        yield (0.65, "engraving the pan")
        pan, info = build.build_stamp_pan(relief, props, coll)

        yield (0.90, "finishing")
        ok, reason = util.part_is_valid(pan)
        if not ok:
            raise MoldGeometryError(f"Generated stamp pan {reason}.")
        relief.name = "MF_Positive"
        _try_hide(relief)
        return {
            "parts": [pan],
            "positive": relief,
            "skin": None,
            "cavity_volume": info["recess_volume"],
            "silicone_volume": info["silicone_volume"],
            "plastic_volume": volume.mesh_volume(pan),
            "remeshed": False,
            "trimmed": False,
            "undercut": 0.0,
            "axis": 'Z',
        }
    finally:
        for o in imported:
            try:
                util.remove_object(o)
            except Exception:
                pass


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
        _try_hide(work)       # fused into the pan already - reference only
        _try_hide(master)     # the original would overlap the pan
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
        plate_screw=getattr(props, "plate_screw", False),
        screw_diameter=getattr(props, "screw_diameter", 3.4),
        screw_countersink=getattr(props, "screw_countersink", True),
        screw_head_diameter=getattr(props, "screw_head_diameter", 6.5),
        insert_diameter=getattr(props, "insert_diameter", 4.0),
        insert_depth=getattr(props, "insert_depth", 6.5),
        suction_cup=getattr(props, "suction_cup", False),
        cup_diameter=getattr(props, "cup_diameter", 0.0),
        cup_depth=getattr(props, "cup_depth", 8.0),
        cup_lock=getattr(props, "cup_lock", 'PIN'),
        fit_clearance=getattr(props, "fit_clearance", 0.2),
        lock_height=getattr(props, "lock_height", 10.0),
        lock_margin=getattr(props, "lock_margin", 4.0),
        lock_teeth=getattr(props, "lock_teeth", 3),
        lock_tooth_depth=getattr(props, "lock_tooth_depth", 2.0),
        lock_tolerance=getattr(props, "lock_tolerance", 0.2),
        lock_unite=getattr(props, "lock_unite", True),
        split_axis=props.split_axis,
        split_offset=getattr(props, "split_offset", 0.0),
        dual_density=getattr(props, "dual_density", False),
        anchor_plug=getattr(props, "anchor_plug", False),
        core_wall=getattr(props, "core_wall", 5.0),
        split_horizontal=getattr(props, "split_horizontal", False),
        split_z_offset=getattr(props, "split_z_offset", 0.0),
        printer_fit=getattr(props, "printer_fit", False),
        max_print_height=getattr(props, "max_print_height", 0.0),
        support_clearance=getattr(props, "support_clearance", 5.0),
        fit_positive=getattr(props, "fit_positive", True),
        # A Locking Base's seam is flat through the plinth and its socket and
        # contoured above (``parting_flat``, set once the split axis is known).
        contoured=getattr(props, "contoured", True),
        parting_flat=None,
        parting_blend=12.0,
        key_count=props.key_count,
        parts_count=getattr(props, "parts_count", 2),
        registration=getattr(props, "registration", 'KEYS'),
        sprue=props.sprue,
        label_on=getattr(props, "label_on", False),
        label_text=getattr(props, "label_text", "{silicone_g} g silicone|{cast_g} g cast"),
        label_size=getattr(props, "label_size", 5.0),
        label_depth=getattr(props, "label_depth", 0.6),
        label_style=getattr(props, "label_style", 'CUT'),
        mm_per_unit=getattr(props, "mm_per_unit", 1.0),
        silicone_density=getattr(props, "silicone_density", 1.15),
        cast_density=getattr(props, "cast_density", 1.10),
        stand_legs=getattr(props, "stand_legs", False),
        stand_tilt=getattr(props, "stand_tilt", 0.0),
        wing_band_notches=getattr(props, "wing_band_notches", False),
        radial_bolt_zs=None,
        pour_channel=getattr(props, "pour_channel", False),
        channel_radius=getattr(props, "channel_radius", 4.0),
        channel_flip=getattr(props, "channel_flip", False),
        vent_place=getattr(props, "vent_place", 'AUTO'),
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
        wing_report=[],
        wing_width=getattr(props, "wing_width", 8.0),
        wing_keys=getattr(props, "wing_keys", 'NONE'),
        wing_key_size=getattr(props, "wing_key_size", 6.0),
        wing_key_height=getattr(props, "wing_key_height", 0.0),
        wing_key_spacing=getattr(props, "wing_key_spacing", 40.0),
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
