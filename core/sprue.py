"""Pour sprue (real raised, adjustable funnels) and air vents.

Two phases so the clamp wings can run up the funnel without clogging it:
  1. ``add_funnel_spouts`` unions the *solid* spouts onto the mold (before wings).
  2. ``bore_funnels_and_vents`` bores the funnels through and cuts the vents (after
     wings), so the bores are clear even though wings were unioned over the top.
"""

import math

from mathutils import Vector

from . import constants as C
from . import util


def _funnel_at(mold, master, pt, props):
    """Funnel sizing + geometry for a pour point ``pt`` (world). Returns a dict.

    The cavity is carved AFTER the funnel is unioned on (see the pipeline), so the
    throat opens by construction — no auto-narrowing, no model-clearance juggling.
    The throat (bottom) radius is exactly the user's Throat Radius, capped only by
    the mold's size; the mouth is throat x Flare. The straight neck makes a clean
    circular hole, and the flare starts above the cavity ceiling's high point so it
    never cuts the shell off-round."""
    mn, mx = util.world_bbox(mold)
    mold_half_min = max(min(mx.x - mn.x, mx.y - mn.y) * 0.5, 1e-4)
    big_throat = getattr(props, "big_throat", False)
    throat_cap = (C.THROAT_CAP_BIG if big_throat else C.THROAT_CAP) * mold_half_min
    sprue_r = min(props.sprue_radius, throat_cap)                # throat (bottom)
    flare = max(getattr(props, "sprue_flare", 2.4), 1.0)        # 1.0 = straight tube
    big_mouth = getattr(props, "big_mouth", False)
    # Mouth auto-caps at MOUTH_CAP * half-width so it stays on the mold; Oversized
    # Mouth lifts that (to a generous safety limit) and lets it overhang (UI warns).
    mouth_cap = (C.MOUTH_CAP_BIG if big_mouth else C.MOUTH_CAP) * mold_half_min
    mouth_r = min(sprue_r * flare, mouth_cap)
    jacket = props.box_style == 'POUR_BOX'   # hollow shell (gap around model)
    gap = getattr(props, "silicone_gap", props.wall_thickness)
    wall = max(props.shell_wall if jacket else props.wall_thickness, 1.2)
    offset = gap + (props.shell_wall if jacket else 0.0)
    breach = max(gap * 0.5, 1e-4)

    # Keep the mouth on the mold edge unless Oversized Mouth allows overhang.
    edge = min(pt.x - mn.x, mx.x - pt.x, pt.y - mn.y, mx.y - pt.y)
    if not big_mouth:
        mouth_r = min(mouth_r, edge - wall)
    mouth_r = max(mouth_r, sprue_r)

    # The cavity ceiling follows the model surface raised by ``gap``. Probe the
    # MODEL (always present; the box is still solid here) under the neck for the
    # spout base, and under the wider mouth for where the flare may start without
    # cutting the shell off-round.
    # Probe the model under the neck/mouth (the body is still solid here; the cavity
    # is carved LAST) so the spout base can be dropped to weld to the shell all the
    # way round its footprint — a base parked at the peak leaves the neck's far edge
    # floating where the dome drops away (the funnel-doesn't-connect gap).
    mw = master.matrix_world
    inv = mw.inverted()
    dl = (inv.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized()
    start_z = mx.z + 50.0

    def model_span(radius):
        """(max, min) model-surface Z under a disk of ``radius`` at the pour point.

        The min ignores hits far below the local peak: a CURVED model puts a distant
        lower body part under the funnel's vertical column, and the spout must NOT be
        dropped down to it (that drove the neck cone all the way to the base). Only
        surface within ``max_drop`` of the local high point counts as "under" the
        funnel — enough for a steep lean, but not a separate body part below."""
        zs = []
        pts = [(pt.x, pt.y)]
        for ring in (0.34, 0.67, 1.0):
            rr = radius * ring
            for k in range(12):
                a = 2.0 * math.pi * (k + 0.2 * ring) / 12.0
                pts.append((pt.x + math.cos(a) * rr, pt.y + math.sin(a) * rr))
        for px, py in pts:
            hit, loc, _n, _i = master.ray_cast(inv @ Vector((px, py, start_z)), dl)
            if hit:
                zs.append((mw @ loc).z)
        if not zs:
            return (pt.z, pt.z)
        hi = max(zs)
        max_drop = radius * C.FUNNEL_LOCAL_DROP + gap + wall
        return (hi, min(z for z in zs if z >= hi - max_drop))

    local_top, neck_floor = model_span((sprue_r + wall) * 1.05)   # under the neck
    wide_top, _ = model_span(mouth_r + wall)                      # under the mouth
    # Drop the base below the LOWEST cavity ceiling under the neck so the spout welds
    # to the shell all the way round even where the body curves/leans away under a
    # wide throat (the cause of a one-sided gap). The cavity is carved LAST, so the
    # plunge is trimmed; clamp only against the mold bottom so it can't overrun it.
    if jacket:
        # Cavity ceiling = model + gap.
        throat_top = wide_top + gap + 0.8                         # flare above ceiling
        base_z = max(neck_floor + gap - max(1.0, 0.5 * gap), mn.z + 0.5)
    else:
        # Direct mold: cavity = model. The shell's outer surface is the model raised
        # by the wall; drop the base below the lowest model point under the neck so
        # the spout welds to the solid body all round, and start the flare above the
        # shell's high point so it never cuts the rim.
        throat_top = wide_top + wall + 0.8
        base_z = max(neck_floor - max(1.0, 0.5 * wall), mn.z + 0.5)

    fh = max(getattr(props, "funnel_height", 12.0), 1.0)
    apex_z = max(local_top + offset + fh, throat_top + max(fh * 0.5, 2.0))
    return {
        "x": pt.x, "y": pt.y, "pt_z": pt.z, "base_z": base_z,
        "apex_z": apex_z, "throat_top": throat_top, "clear": 0.0,
        "sprue_r": sprue_r, "mouth_r": mouth_r, "wall": wall, "breach": breach,
        "neck_out": sprue_r + wall, "mouth_out": mouth_r + wall,
    }


def add_funnel_spouts(mold, master, props, coll):
    """Phase 1: union the solid funnel spout(s) (narrow neck -> wide mouth) onto the
    mold and return their geometry (a list) so the wings can run up them."""
    if not props.sprue:
        return []
    funnels = []
    for pt in _pour_points(mold, master, props):
        f = _funnel_at(mold, master, pt, props)
        jacket = props.box_style == 'POUR_BOX'
        t_z = f.get("throat_top", f["base_z"]) if jacket else f["base_z"]
        t_z = min(max(t_z, f["base_z"] + 0.5), f["apex_z"] - 0.5)
        # Tapered neck CONE (not a straight cylinder): a wide cylinder plunging to a
        # deep base juts out past the contour on a narrow/leaning side. The cone is
        # full width at the throat (where it welds to the shell and opens the hole) and
        # narrows going down, so the deep part stays slim and inside the cavity, which
        # is carved away. Paired with the flared cup, the spout is a pair of cones.
        neck_base = max(f["neck_out"] * C.NECK_TAPER, f["wall"])
        neck = util.add_cone(
            "MF_funnel", Vector((f["x"], f["y"], (f["base_z"] + t_z) * 0.5)),
            neck_base, f["neck_out"], t_z - f["base_z"], 'Z', coll,
        )
        util.boolean(mold, neck, 'UNION')
        util.remove_object(neck)
        if f["apex_z"] - t_z > 0.1:
            cup = util.add_cone(
                "MF_funnel", Vector((f["x"], f["y"], (t_z + f["apex_z"]) * 0.5)),
                f["neck_out"], f["mouth_out"], f["apex_z"] - t_z, 'Z', coll,
            )
            util.boolean(mold, cup, 'UNION')
            util.remove_object(cup)
        funnels.append(f)
    return funnels


def bore_funnels_and_vents(mold, master, props, funnels, coll):
    """Phase 2: bore each funnel through into the cavity and cut the air vents — done
    after the wings so all end up clear. Vents are kept on the body, well away from
    the funnel mouths (by *lateral* distance, since the channels are vertical)."""
    mn, mx = util.world_bbox(mold)
    top = mx.z
    mold_half_min = max(min(mx.x - mn.x, mx.y - mn.y) * 0.5, 1e-4)
    vent_r = min(props.vent_radius, C.VENT_CAP * mold_half_min)
    gap = getattr(props, "silicone_gap", props.wall_thickness)
    breach = max(gap * 0.5, 1e-4)
    # Jacket vents end ABOVE their surface point (clear of the model, still well
    # inside the gap void); solid-mold vents must breach INTO the cavity.
    vent_drop = -gap * 0.5 if props.box_style == 'POUR_BOX' else breach

    for funnel in funnels:
        # Two-piece bore like a real funnel (both box styles now build a solid body
        # with the cavity carved LAST): a STRAIGHT neck (radius sprue_r — exactly the
        # probed footprint) pierces the body so the hole is a clean circle, then the
        # flare opens above the throat. The neck overshoots BELOW the spout base:
        # ending a cutter exactly on the spout's own bottom plane is coplanar boolean
        # input that can leave a zero-thickness membrane sealing the throat. The
        # cutter only removes mold (the cavity model is separate), so it costs no clearance.
        bore_top = funnel["apex_z"] + 0.5
        bore_bottom = funnel["base_z"] - max(1.0, 0.3 * gap)
        t_z = min(max(funnel["throat_top"], bore_bottom + 0.5), bore_top - 0.5)
        bore_base = max(funnel["sprue_r"] * C.NECK_TAPER, 0.4)
        neck = util.add_cone(
            "MF_funnelbore", Vector((funnel["x"], funnel["y"],
                                     (bore_bottom + t_z + 0.2) * 0.5)),
            bore_base, funnel["sprue_r"], (t_z + 0.2) - bore_bottom, 'Z', coll,
        )
        util.boolean(mold, neck, 'DIFFERENCE')
        util.remove_object(neck)
        bore = util.add_cone(
            "MF_funnelbore", Vector((funnel["x"], funnel["y"], (t_z + bore_top) * 0.5)),
            funnel["sprue_r"], funnel["mouth_r"], bore_top - t_z, 'Z', coll,
        )
        util.boolean(mold, bore, 'DIFFERENCE')
        util.remove_object(bore)

    if props.vent_count <= 0:
        return
    verts = [master.matrix_world @ v.co for v in master.data.vertices]
    if not verts:
        return
    verts.sort(key=lambda p: p.z, reverse=True)

    def clears_funnels(p):
        for f in funnels:
            if math.hypot(p.x - f["x"], p.y - f["y"]) <= f["mouth_out"] + vent_r + 2.0:
                return False
        return True

    chosen = []
    for p in verts:
        if not all((p - q).length > props.vent_spacing for q in chosen):
            continue
        if funnels and not clears_funnels(p):
            continue
        chosen.append(p)
        if len(chosen) >= props.vent_count:
            break
    for i, p in enumerate(chosen):
        vent = _vertical_channel(f"MF_vent_{i}", p, top, vent_r, vent_drop, vent_r, vent_r, coll)
        util.boolean(mold, vent, 'DIFFERENCE')
        util.remove_object(vent)


def _pour_points(mold, master, props):
    """The world points where pour funnels go. The primary one follows Sprue
    Placement (XY/X/Y centre, or the model's highest point); extra Pour Points are
    the next-highest vertices spaced apart, to help fill tall figures."""
    verts = [master.matrix_world @ v.co for v in master.data.vertices]
    if not verts:
        return []
    verts.sort(key=lambda p: p.z, reverse=True)
    mn, mx = util.world_bbox(master)

    place = getattr(props, "sprue_place", 'TOP')
    if place == 'XY':
        primary = _center_point(master, mn, mx) or verts[0]
    elif place in ('X', 'Y'):
        primary = _axis_center_point(master, verts, mn, mx, place)
    elif place == 'MANUAL':
        primary = _manual_point(master, mn, mx,
                                getattr(props, "sprue_x", 0.0),
                                getattr(props, "sprue_y", 0.0))
    else:   # 'TOP'
        primary = _top_center_point(master, verts)
    points = [primary]

    n = max(1, getattr(props, "sprue_count", 1))
    if n > 1:
        spacing = (mx - mn).length * 0.22 + 1.0
        for v in verts:
            if all((v - q).length > spacing for q in points):
                points.append(v)
            if len(points) >= n:
                break
    return points


def _center_point(master, mn, mx):
    """The model surface straight down the (x,y) centre, or None if the column misses."""
    cx, cy = (mn.x + mx.x) * 0.5, (mn.y + mx.y) * 0.5
    span = (mx - mn).length + 10.0
    mw = master.matrix_world
    inv = mw.inverted()
    hit, loc, _n, _i = master.ray_cast(
        inv @ Vector((cx, cy, mx.z + span)),
        (inv.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized(),
    )
    return (mw @ loc) if hit else None


def _manual_point(master, mn, mx, dx, dy):
    """Funnel at a user X/Y offset from the model's footprint centre (0 = centre),
    clamped to the footprint so it stays on the model, dropped onto the surface."""
    margin = max(mx.x - mn.x, mx.y - mn.y) * 0.02 + 0.5
    x = min(max((mn.x + mx.x) * 0.5 + dx, mn.x + margin), mx.x - margin)
    y = min(max((mn.y + mx.y) * 0.5 + dy, mn.y + margin), mx.y - margin)
    span = (mx - mn).length + 10.0
    mw = master.matrix_world
    inv = mw.inverted()
    hit, loc, _n, _i = master.ray_cast(
        inv @ Vector((x, y, mx.z + span)),
        (inv.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized(),
    )
    return (mw @ loc) if hit else Vector((x, y, mx.z))


def _axis_center_point(master, verts_desc, mn, mx, axis):
    """Funnel centred on one axis and following the model's highest point on the
    other: ``axis='X'`` centres X and keeps the peak's Y (``'Y'`` is the mirror).
    Returns the model surface straight down that (x, y), falling back to the peak
    height if the column misses the model."""
    top = _top_center_point(master, verts_desc)
    cx = (mn.x + mx.x) * 0.5
    cy = (mn.y + mx.y) * 0.5
    x = cx if axis == 'X' else top.x
    y = cy if axis == 'Y' else top.y
    span = (mx - mn).length + 10.0
    mw = master.matrix_world
    inv = mw.inverted()
    hit, loc, _n, _i = master.ray_cast(
        inv @ Vector((x, y, mx.z + span)),
        (inv.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized(),
    )
    return (mw @ loc) if hit else Vector((x, y, top.z))


def _top_center_point(master, verts_desc):
    """The model's highest point: the most central vertex right at the peak, so the
    funnel sits on the actual high point without landing on a lone spike or a corner.

    The centre column is used only when it is genuinely AT the peak (a flat or
    symmetric top, e.g. a cube/sphere — otherwise a cube's funnel would land on a
    corner). The tolerance is tight, so a leaning model's off-centre peak is honoured
    instead of being snapped to centre. (Use Center XY/X/Y placement to override.)"""
    mn, mx = util.world_bbox(master)
    cx = (mn.x + mx.x) * 0.5
    cy = (mn.y + mx.y) * 0.5
    zmax = verts_desc[0].z
    nt_tol = max((mx.z - mn.z) * 0.01, 0.5)
    world = _center_point(master, mn, mx)
    if world is not None and world.z >= zmax - nt_tol:
        return world
    near_top = [v for v in verts_desc if v.z >= zmax - nt_tol]
    return min(near_top, key=lambda v: (v.x - cx) ** 2 + (v.y - cy) ** 2)


def _vertical_channel(name, point, mold_top, above, drop, radius_bottom, radius_top, coll):
    """A vertical cone from above the mold down to ``point.z - drop`` (air vent).
    A negative ``drop`` ends the channel ABOVE the point — used by jackets so the
    vent opens into the gap void without grazing the model."""
    z_top = mold_top + above
    z_bottom = point.z - drop
    depth = z_top - z_bottom
    center = Vector((point.x, point.y, (z_top + z_bottom) * 0.5))
    return util.add_cone(name, center, radius_bottom, radius_top, depth, 'Z', coll)
