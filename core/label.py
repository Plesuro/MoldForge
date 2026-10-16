"""Label on the case: text on a plaque - the silicone and cast weights, the
model's name, the date, or anything else.

The shell hugs the model, so its outside is curved everywhere. The label is a
thin plaque with rounded corners that follows that curve like a label on a
bottle: every point of it stands the same height off the shell, so the text
can run ACROSS a round shell and still reads straight from the front. A fine
frame line runs just inside its edge, its rim is chamfered down into the shell,
and the text is raised or cut in (Label Style). Across the shell is tried
first, at the set letter height down to three quarters of it; then up the shell
too, and smaller, down to 3 mm, until a spot is found where the shell faces
the front closely enough (turned away by MAX_SLOPE at most) all over the
plaque.
"""

import datetime
import math

import bpy
import bmesh
from mathutils import Matrix, Vector

from . import util, meshprep

MIN_LETTER = 3.0
DEFAULT_TEXT = "{silicone_g} g silicone|{cast_g} g cast"


def label_fields(props, name, silicone_vol, cast_vol):
    """The values the ``{field}`` placeholders take. Volumes are in Blender
    units cubed; ``props.mm_per_unit`` converts them to millilitres."""
    mpu = getattr(props, "mm_per_unit", 1.0) or 1.0
    s_ml = silicone_vol * mpu ** 3 / 1000.0
    c_ml = cast_vol * mpu ** 3 / 1000.0

    def num(v):
        return f"{v:.1f}" if v < 10.0 else f"{v:.0f}"
    return {
        "silicone_ml": num(s_ml),
        "silicone_g": num(s_ml * getattr(props, "silicone_density", 1.15)),
        "cast_ml": num(c_ml),
        "cast_g": num(c_ml * getattr(props, "cast_density", 1.10)),
        "name": name or "",
        "date": datetime.date.today().isoformat(),
    }


def format_label(template, fields):
    """Fill ``{field}`` placeholders; an unknown one is left as typed. ``|``
    starts a new line. Returns the list of non-empty lines."""
    out = template
    for k, v in fields.items():
        out = out.replace("{" + k + "}", str(v))
    return [ln.strip() for ln in out.split("|") if ln.strip()]


def text_solid(lines, height, depth, coll):
    """The text as a closed solid: letters ``height`` tall (cap height), lines
    centred, lying in local XY (reading along +X, letters up along +Y) and
    extruded from z = 0 to z = ``depth``. None if the text has no shapes."""
    cu = bpy.data.curves.new("MF_labeltext", 'FONT')
    cu.body = "\n".join(lines)
    cu.size = height / 0.72                  # the built-in font's caps are ~0.72 em
    cu.align_x = 'CENTER'
    cu.align_y = 'CENTER'
    cu.space_line = 1.25                     # air between the lines
    cu.extrude = depth * 0.5
    ob = bpy.data.objects.new("MF_labeltext", cu)
    coll.objects.link(ob)
    deps = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(ob.evaluated_get(deps),
                                         preserve_all_data_layers=False, depsgraph=deps)
    bpy.data.objects.remove(ob, do_unlink=True)
    bpy.data.curves.remove(cu)
    if not me.polygons:
        bpy.data.meshes.remove(me)
        return None
    me.transform(Matrix.Translation((0.0, 0.0, depth * 0.5)))
    solid = bpy.data.objects.new("MF_labeltext", me)
    coll.objects.link(solid)
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-5)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(me)
    bm.free()
    meshprep.ensure_outward_normals(solid)
    return solid


def _frame(out, vertical):
    """Text axes on a face looking out along ``out``: reading direction, letter
    up, and the face normal - readable from outside."""
    z = Vector((0.0, 0.0, 1.0))
    if vertical:
        x = z.copy()                              # reads bottom to top
        y = out.cross(z).normalized()
    else:
        x = z.cross(out).normalized()             # reads left to right
        y = z.copy()
    return x, y, out.normalized()


MAX_SLOPE = math.radians(55.0)
MAX_SPREAD = 6.0          # mm: dips under the plaque deeper than this are a step


def _outline(w, h, r, nx, ny, seg=6):
    """Points (x, y) round a ``w`` x ``h`` rectangle centred on the origin with
    corners rounded to ``r``: ``nx``/``ny`` steps along the straight sides,
    ``seg`` round each corner, counter-clockwise. Outlines made with the same
    step counts match point for point."""
    r = max(min(r, w * 0.5 - 1e-3, h * 0.5 - 1e-3), 1e-3)
    pts = []
    corners = ((w * 0.5 - r, h * 0.5 - r, 0.0), (-w * 0.5 + r, h * 0.5 - r, 90.0),
               (-w * 0.5 + r, -h * 0.5 + r, 180.0), (w * 0.5 - r, -h * 0.5 + r, 270.0))
    for k, (cx, cy, a0) in enumerate(corners):
        for i in range(seg):
            a = math.radians(a0 + 90.0 * i / seg)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        # the straight side from this corner's end to the next corner's start
        a1 = math.radians(a0 + 90.0)
        p0 = (cx + r * math.cos(a1), cy + r * math.sin(a1))
        nxt = corners[(k + 1) % 4]
        p1 = (nxt[0] + r * math.cos(a1), nxt[1] + r * math.sin(a1))
        steps = nx if k % 2 == 0 else ny
        for i in range(steps):
            t = i / steps
            pts.append((p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t))
    return pts


class _Surface:
    """The shell's outside under the label, smoothed: a gentle curved surface
    (a quadratic in the label's frame) fitted through ray samples of it and
    lifted to the tops of its bumps, so a ridged or knobbly shell still gets a
    clean, smooth plaque that bridges the ridges. ``depth(u, v)`` is how far
    out along ``n`` that surface is at (u, v) (``centre`` + u x + v y);
    ``spread`` how deep the shell's dips go below it."""

    def __init__(self, mold, centre, x, y, n):
        self.mold, self.centre, self.x, self.y, self.n = mold, centre, x, y, n
        mn, mx = util.world_bbox(mold)
        self.far = (mx - mn).length + 20.0
        self.c = None
        self.spread = 0.0
        self.su = self.sv = 1.0

    def _ray(self, u, v):
        o = self.centre + self.x * u + self.y * v + self.n * self.far
        hit, loc, _nrm, _i = self.mold.ray_cast(o, -self.n)
        return (loc - self.centre).dot(self.n) if hit else None

    # u^i v^j, i <= 4, j <= 3: round across the label (even near the steep
    # sides of a round shell), free to swell and narrow along it (a bulb)
    _POW = [(i, j) for i in range(5) for j in range(4)]

    def _basis(self, u, v):
        a, b = u / self.su, v / self.sv            # scaled to -1..1: a well-posed fit
        return tuple(a ** i * b ** j for i, j in self._POW)

    def fit(self, w, h, step=None):
        """Fit over a ``w`` x ``h`` footprint (samples every ``step`` mm, or
        a coarse 9 x 6 grid). False when the plaque cannot sit there: no
        surface somewhere, the surface turning away by more than MAX_SLOPE,
        or dips deeper than MAX_SPREAD (a step - a wing, the pipe)."""
        import numpy as np
        if step:
            nu = min(max(int(w / step) + 1, 9), 61)
            nv = min(max(int(h / step) + 1, 6), 31)
        else:
            nu, nv = 9, 6
        self.su, self.sv = max(w * 0.5, 1e-3), max(h * 0.5, 1e-3)
        rows, ds = [], []
        for j in range(nv):
            for i in range(nu):
                u = w * (i / (nu - 1) - 0.5)
                v = h * (j / (nv - 1) - 0.5)
                d = self._ray(u, v)
                if d is None:
                    return False
                rows.append(self._basis(u, v))
                ds.append(d)
        A, b = np.array(rows), np.array(ds)
        c = np.linalg.lstsq(A, b, rcond=None)[0]
        r = b - A @ c
        self.c = c
        self.lift = float(r.max())          # the plaque rests on the bumps' tops
        self.spread = float(r.max() - r.min())
        if self.spread > max(MAX_SPREAD, 0.15 * min(w, h)):
            return False
        tan_max = math.tan(MAX_SLOPE)
        for u in (-w * 0.5, 0.0, w * 0.5):
            for v in (-h * 0.5, 0.0, h * 0.5):
                gu, gv = self._grad(u, v)
                if math.hypot(gu, gv) > tan_max:
                    return False
        return True

    def _grad(self, u, v):
        a, b = u / self.su, v / self.sv
        gu = gv = 0.0
        for ck, (i, j) in zip(self.c, self._POW):
            if i:
                gu += ck * i * a ** (i - 1) * b ** j
            if j:
                gv += ck * j * a ** i * b ** (j - 1)
        return gu / self.su, gv / self.sv

    def depth(self, u, v):
        return float(sum(ci * bi for ci, bi in zip(self.c, self._basis(u, v)))) + self.lift

    def point(self, u, v, off):
        """The point ``off`` above the smoothed surface at (u, v), out along
        its own normal - so the plaque and the letters are equally thick where
        the shell turns away, not thinner."""
        gu, gv = self._grad(u, v)
        su = self.x + self.n * gu
        sv = self.y + self.n * gv
        nrm = su.cross(sv).normalized()
        if nrm.dot(self.n) < 0.0:
            nrm = -nrm
        return (self.centre + self.x * u + self.y * v + self.n * self.depth(u, v)
                + nrm * off)


def _over_model(model, centre, x, y, n, w, h, far):
    """Is the model right behind the label's middle - so it sits on the case
    around the model, not on a flat fin or web beside it (the bottom-fill
    channel's, a wing)?"""
    for u, v in ((0.0, 0.0), (-0.3 * w, 0.0), (0.3 * w, 0.0)):
        o = centre + x * u + y * v + n * far
        hit, _loc, _nrm, _i = model.ray_cast(o, -n)
        if not hit:
            return False
    return True


def _plaque(bm, shell, top, foot, off_top, off_mid, off_foot, rings):
    """A closed solid on the smoothed surface: its top is the outline ``top``
    filled with ``rings`` concentric rings, ``off_top`` above the surface; the
    rim slopes out to the matched outline ``foot`` at ``off_mid`` and drops
    straight down from there to the underside at ``off_foot`` (inside the
    shell, below its deepest dip)."""
    def sheet(outline, off, flip):
        cen = bm.verts.new(shell.point(0.0, 0.0, off))
        prev = None
        for k in range(1, rings + 1):
            s = k / rings
            ring = [bm.verts.new(shell.point(px * s, py * s, off)) for px, py in outline]
            m = len(ring)
            for i in range(m):
                j = (i + 1) % m
                if prev is None:
                    f = (cen, ring[i], ring[j])
                else:
                    f = (prev[i], ring[i], ring[j], prev[j])
                bm.faces.new(tuple(reversed(f)) if flip else f)
            prev = ring
        return prev
    t = sheet(top, off_top, False)
    b = sheet(foot, off_foot, True)
    mid = [bm.verts.new(shell.point(px, py, off_mid)) for px, py in foot]
    m = len(t)
    for i in range(m):
        j = (i + 1) % m
        bm.faces.new((mid[i], mid[j], t[j], t[i]))
        bm.faces.new((b[i], b[j], mid[j], mid[i]))


def _ring(bm, shell, outer, inner, off_lo, off_hi):
    """A closed band draped on the shell between the matched outlines
    ``outer`` and ``inner``, from ``off_lo`` to ``off_hi`` above the surface."""
    def loop(pts, off):
        return [bm.verts.new(shell.point(px, py, off)) for px, py in pts]
    oa, ia = loop(outer, off_lo), loop(inner, off_lo)
    ob, ib = loop(outer, off_hi), loop(inner, off_hi)
    m = len(oa)
    for i in range(m):
        j = (i + 1) % m
        bm.faces.new((oa[i], oa[j], ob[j], ob[i]))
        bm.faces.new((ia[j], ia[i], ib[i], ib[j]))
        bm.faces.new((ob[i], ob[j], ib[j], ib[i]))
        bm.faces.new((oa[j], oa[i], ia[i], ia[j]))


def _object(name, coll, build):
    bm = bmesh.new()
    build(bm)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return util.new_mesh_object(name, bm, coll)


def _drape_text(txt, shell, base):
    """Lay the flat text solid (local x/y on the label, z up out of it) onto
    the shell: each vertex goes to ``base`` + its z above the surface under
    it. The letters are small, so their faces follow the curve closely."""
    me = txt.data
    bm = bmesh.new()                       # flat triangles first: they stay true
    bm.from_mesh(me)                       # when the letters bend with the shell
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bm.to_mesh(me)
    bm.free()
    for v in me.vertices:
        p = shell.point(v.co.x, v.co.y, base + v.co.z)
        v.co = p
    me.update()


def add_label(mold, model, cavity, out, lines, props, coll):
    """Put a plaque that follows the shell on the outside of ``mold`` facing
    ``out`` (a horizontal unit vector: the middle of one shell's face),
    carrying ``lines`` raised or cut in. ``cavity`` is the cavity solid, so
    the plaque never reaches into it. The mold is restored if anything goes
    wrong. Returns ``(ok, note)``."""
    H0 = max(getattr(props, "label_size", 5.0), MIN_LETTER)
    depth = max(getattr(props, "label_depth", 0.6), 0.2)
    raised = getattr(props, "label_style", 'CUT') == 'RAISED'
    mmn, mmx = util.world_bbox(model)
    c0 = (mmn + mmx) * 0.5
    zspan = mmx.z - mmn.z
    inset, line_w = 1.0, 0.5                       # the frame line, in from the edge
    margin = inset + line_w + 1.6                  # edge to text
    probe = text_solid(lines, H0, 1.0, coll)
    if probe is None:
        return False, "label skipped - the text has no letters"
    pmn, pmx = util.world_bbox(probe)
    util.remove_object(probe)
    tw0, th0 = pmx.x - pmn.x, pmx.y - pmn.y        # text size at H0
    side = Vector((0.0, 0.0, 1.0)).cross(out).normalized()
    half_w = 0.5 * (abs(side.x) * (mmx.x - mmn.x) + abs(side.y) * (mmx.y - mmn.y))
    proud = 0.8 if raised else max(1.0, depth + 0.5)
    chamfer = proud + 1.0                          # the rim: 45 degrees, to 1 mm under the base
    rim = chamfer + 0.5

    def search(H, layouts):
        """The best spot for letters ``H`` tall: candidates from a quick
        coarse look, best first, each confirmed by the fine fit the plaque is
        built on. ``(H, vertical, centre, w, h, surface)`` or None."""
        k = H / H0
        w, h = tw0 * k + 2 * margin, th0 * k + 2 * margin
        cands = []
        for vertical in layouts:
            x, y, n = _frame(out, vertical)
            for zf in (0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8):
                for sf in (0.0, -0.15, 0.15, -0.3, 0.3, -0.5, 0.5):
                    # across the shell too: on a curved toy the bounding box's
                    # middle can be empty air
                    centre = (Vector((c0.x, c0.y, mmn.z + zspan * zf))
                              + side * (half_w * sf))
                    sh = _Surface(mold, centre, x, y, n)
                    if not sh.fit(w + 2 * rim, h + 2 * rim):
                        continue
                    if not _over_model(model, centre, x, y, n, w, h, sh.far):
                        continue        # a flat fin or web beside the model
                    # prefer the middle, and a smooth spot
                    score = abs(zf - 0.5) + abs(sf) + 0.05 * sh.spread
                    cands.append((score, vertical, centre))
        cands.sort(key=lambda c: c[0])
        for _score, vertical, centre in cands[:12]:
            x, y, n = _frame(out, vertical)
            # The fine fit, over the face and its rim. The rim slopes 45
            # degrees from the face to 1 mm under the smoothed base, then
            # drops straight to 1.5 mm below the deepest dip under it.
            sh = _Surface(mold, centre, x, y, n)
            if sh.fit(w + 2 * rim, h + 2 * rim, step=2.0):
                return H, vertical, centre, w, h, sh
        return None

    # Across the shell reads best: try that first, down to 3/4 of the size,
    # then up the shell too, down to MIN_LETTER.
    sizes = []
    H = H0
    while H >= MIN_LETTER - 1e-6:
        sizes.append(H)
        H -= 0.5
    best = None
    for H in sizes:
        if H < max(MIN_LETTER, 0.75 * H0) - 1e-6:
            break
        best = search(H, (False,))
        if best:
            break
    if best is None:
        for H in sizes:
            best = search(H, (False, True))
            if best:
                break
    if best is None:
        return False, ("label skipped - no spot on the shell is smooth enough and faces "
                       "the front closely enough for it (shorten the text or make the "
                       "letters smaller)")
    H, vertical, centre, w, h, shell = best
    x, y, n = _frame(out, vertical)
    sink = max(shell.spread + 1.5, 1.0)
    rc = min(0.25 * min(w, h), 4.0)                # corner radius
    step = 1.0
    nx = max(int(math.ceil((w - 2 * rc) / step)), 1)
    ny = max(int(math.ceil((h - 2 * rc) / step)), 1)
    rings = max(int(math.ceil(min(w, h) * 0.5 / step)), 3)
    top = _outline(w, h, rc, nx, ny)
    foot = _outline(w + 2 * chamfer, h + 2 * chamfer, rc + chamfer, nx, ny)
    fo = _outline(w - 2 * inset, h - 2 * inset, max(rc - inset, 0.4), nx, ny)
    fi = _outline(w - 2 * (inset + line_w), h - 2 * (inset + line_w),
                  max(rc - inset - line_w, 0.3), nx, ny)
    frame_d = min(depth, 0.5)
    bak = mold.data.copy()
    isl0, nm0 = util.island_count(mold), util.nonmanifold_count(mold)
    try:
        # Everything is laid on the smoothed surface first, then joined on.
        pad = _object("MF_labelpad", coll,
                      lambda bm: _plaque(bm, shell, top, foot, proud, -1.0, -sink, rings))
        # the frame line just inside the edge
        if raised:
            line = _object("MF_labeltext", coll, lambda bm: _ring(
                bm, shell, fo, fi, proud - 0.1, proud + frame_d))
        else:
            line = _object("MF_labeltext", coll, lambda bm: _ring(
                bm, shell, fo, fi, proud - frame_d, proud + 0.5))
        # the text: standing ``depth`` proud of the face, or cut ``depth`` in
        txt = text_solid(lines, H, depth + (0.1 if raised else 0.5), coll)
        if txt is None:
            raise RuntimeError("no text")
        _drape_text(txt, shell, proud - 0.1 if raised else proud - depth)
        if cavity is not None:
            util.boolean(pad, cavity, 'DIFFERENCE')
        util.boolean(mold, pad, 'UNION')
        util.remove_object(pad)
        util.boolean(mold, line, 'UNION' if raised else 'DIFFERENCE')
        util.remove_object(line)
        util.boolean(mold, txt, 'UNION' if raised else 'DIFFERENCE')
        util.remove_object(txt)
        util.remove_small_islands(mold)    # crumbs the fine cuts can leave on a ridge
        if util.island_count(mold) > isl0 or util.nonmanifold_count(mold) > nm0:
            raise RuntimeError("label broke the shell")
    except Exception:
        cur = mold.data
        mold.data = bak
        if cur.users == 0:
            bpy.data.meshes.remove(cur)
        for o in list(coll.objects):
            if o.name.startswith(("MF_labelpad", "MF_labeltext")):
                util.remove_object(o)
        return False, "label skipped - it could not be cut cleanly into this shell"
    if bak.users == 0:
        bpy.data.meshes.remove(bak)
    how = "up the shell" if vertical else "across the shell"
    small = f" at {H:.1f} mm (shrunk to fit)" if H < H0 - 1e-6 else ""
    kind = "raised" if raised else "cut in"
    return True, f"label {kind} {how}{small}: " + " / ".join(lines)

