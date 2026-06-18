# MoldForge

A Blender add-on that turns a 3D model into a **printable mold system** — a 2‑to‑4
piece mold with an auto‑oriented, self‑registering split, sprue + air vents, a
mounting base and silicone/cast/plastic volume **and weight** estimates, or a
one‑part open tray for flat & relief objects. Exports STL.

Three output types (**Mold Type** in the panel):

- **Silicone Pour Box** (default) — prints a thin-walled **jacket** that covers
  your master with a gap; you nest the master inside and pour liquid silicone
  into the gap — the silicone is the mold. Controls: **Silicone Gap** and
  **Printed Shell Wall**. An `MF_Skin` object always shows exactly the silicone
  you'll pour. Tick **Glove Skin Keys** for the glove / mother-mold workflow
  (set a thin gap, e.g. 3 mm): registration **bumps on the skin** seat into
  matching **pockets in the jacket** so the thin skin can't shift or slump.
- **Direct Printed Mold** — the printed pieces *are* the mold; cast
  resin/wax/plaster straight in. One control: **Wall Thickness**, plus a
  **Shape**: *Hugging* (pieces follow the model — least material) or *Block*
  (rectangular — easiest to clamp and stand).
- **Tray / Open Pour** — a one‑part open **pan** for FLAT or relief objects
  (text, logos, coins, medallions). The object is laid flat with its detailed
  face up and the top left open: **Embed** it into the floor and pour silicone
  over it for a flexible stamp/mold, or print a **Frame** to drop a real object
  in and pour around it. Pick a **Rectangular** or material‑saving **Hug
  (rounded)** outline. No split, wings or funnel.

> Original, GPL-licensed implementation built on Blender's public Python API
> (`bmesh`, modifiers, depsgraph). It does not contain or derive from any other
> mold tool's code.

Requires **Blender 5.1+** (`blender_version_min = 5.1.0`); built and tested against Blender 5.1.

## Install

1. In Blender: **Edit ▸ Preferences ▸ Get Extensions ▸ ⌄ ▸ Install from Disk…**
   (or **Add-ons ▸ Install from Disk…**) and pick the MoldForge zip.
2. Make sure **MoldForge** is enabled.
3. Open the 3D viewport sidebar (press **N**) → **MoldForge** tab.

## Use

1. Select the mesh you want to mold (make it the active object).
2. Set your parameters in the panel.
3. Click **Generate Mold**.

The mold pieces (`MF_Mold_A`, `MF_Mold_B`, …) and a hidden copy of your model
(`MF_Positive`) land **on the model's location** in a `MoldForge` collection.
Export with **Export Mold Parts**, or tick **Export after generate**. A heavy
build runs with a wait cursor and a progress note so it never looks frozen.

## Sizes are in scene units (mm)

All sizes are **absolute, in scene units** — set your scene unit to millimetres
and the fields read as mm (e.g. a 3 mm silicone wall, a 2 mm printed shell). The
defaults suit print‑scale models (~20–200 mm); the sprue/vents are auto‑capped so
they can't blow out a small mold.

## Parameters

| Group | Option | What it does |
| --- | --- | --- |
| Mold | **Mold Type** | `Silicone Pour Box` (print a jacket, pour silicone), `Direct Printed Mold` (the print is the mold), or `Tray / Open Pour` (one‑part open pan for flat objects) |
| Mold | **Shape** | *(Direct mold)* `Hugging` (least material) or `Block` (easiest to clamp) |
| Tray | **Tray Mode** | *(Tray)* `Embed` (fuse the object in, pour silicone over it for a stamp) or `Frame` (print the open box, drop a real object in) |
| Tray | **Capture Face** + **Outline** | *(Tray)* which face points up (`Auto`/Z/X/Y) and a `Rectangular` or material‑saving `Hug (rounded)` pan outline |
| Tray | **Pan Wall / Pan Floor / Border / Pour Depth** | *(Tray)* printed wall & floor thickness, the silicone border around the object, and how much silicone stands above it |
| Mold | **Silicone / Wall Thickness** | Silicone thickness (pour gap / glove skin, or the direct mold's wall), in mm |
| Mold | **Printed Shell Wall** | *(Pour Box)* printed jacket wall |
| Mold | **Glove Skin Keys** | *(Pour Box)* glove/mother-mold workflow: bumps on the silicone skin seat into pockets in the jacket |
| Mold | **Bottom** | `Flat (closed)` (+ optional **Mounting Flange** with bolt holes) · `Open Bottom` (+ optional **Detachable Key Plate**: a pocket registers the model; a ring tongue on the shell drops into a groove around the plate's chin collar) · `Follow Model` |
| Split | **Mold Pieces** | 2–4. Two = a normal split; 3–4 splits into radial wedges around the vertical axis so undercuts on every side can release (each wedge pulls straight out) |
| Split | **Horizontal Split** + **Seam Height** | Also split the shell horizontally (XL molds print shorter pieces). The seam gets a profile‑hugging bolted flange ring — size the vertical holes for threaded inserts via **Bolt Diameter** (inserts in the lower lip, screws from the top) |
| Split | **Split Axis** | `Auto` picks the axis the model **releases best** along (fewest undercuts), falling back to the wider footprint when equal · or force `X` / `Y` |
| Split | **Parting Offset** | Slide the parting plane off‑centre along the split axis (auto‑clamped so neither half vanishes) |
| Split | **Contoured Parting** | Parting surface follows the model's mid‑profile and self‑registers (falls back to a flat plane) |
| Split | **Alignment Keys** + **Registration** | 0–4 keys on a flat parting: `Cone Keys` (pins into sockets) or `Interlocking Teeth` (a castellated row). For 3+ pieces this becomes **Seam Pins** between wedges |
| Clamp | **Clamp Wings** + **Wing Width** | Full‑height flanges down each side of the parting line (hugging the model's profile, running up the funnel) to clamp the halves together |
| Clamp | **Bolt Diameter** + **Auto Bolts** / **Bolts / Side** | Size of the clamp/flange bolt holes. **Auto Bolts** places them by flange height; untick it to set an exact count per side/seam — and 0 means none at all |
| Sprue | **Sprue** + **Throat Radius** + **Funnel Height** + **Mouth Flare** | A real raised pour **funnel** that opens into the cavity. **Throat Radius** is the narrow bottom (the hole into the mold); the mouth is throat × **Mouth Flare** (1.0 = straight tube, up to 4× = wide catch funnel); **Funnel Height** is how far it stands proud. The panel shows the exact throat Ø / mouth Ø being built |
| Sprue | **Pour Points** + **Center Sprue** | 1–4 funnels (more helps fill tall figures); centre them on the seam instead of the model's high point |
| Sprue | **Air Vents** + **Vent Radius** | 0–8 thin channels from the cavity's high points to the outside |
| Prep | **Heal / Decimate / Safe Remesh** | Clean up messy, heavy, or non‑manifold meshes |
| Material | **Silicone / Cast / Print density** | g/ml, used for the weight estimate (RTV silicone ≈ 1.1–1.2, resin ≈ 1.1, PLA ≈ 1.24) |

The panel reports the **silicone** (the pour amount, or the thin skin for a glove
mold), the **printed plastic**, and the **cast material** volume — in millilitres
**and grams** (using the densities above; assuming 1 unit = 1 mm).

## How it works

1. **Prep** — duplicate the model, optionally heal/decimate, and center it. A
   non‑manifold or very heavy mesh is voxel‑remeshed into a clean watertight solid.
2. **Shell**
   - *Pour Box*: `dilate(model, gap + shell) − dilate(model, gap)` (two `Solidify`
     passes + a boolean) gives a hollow jacket whose cavity is the model plus a
     uniform silicone gap; the `inner − model` solid is kept as the `MF_Skin`
     preview. With **Glove Skin Keys**, registration bumps are raised on the
     silicone (matching pockets end up in the jacket).
   - *Direct mold, Hugging*: one `Solidify` wraps the model in a uniform shell
     whose enclosed void is the casting cavity.
   - *Direct mold, Block*: a bounding box minus the model.
   - *Tray*: an open box around the object's footprint (rectangular, or a rounded
     hug of the outline); the object is laid flat, unioned into the floor (Embed)
     or left out (Frame), and the top is left open — no split, wings or funnel.
3. **Sprue & vents** — the solid funnel spout(s) are unioned on first (so wings can
   run up them), then bored through into the cavity *after* the wings, so the bore
   is always clear. Vents are cut from the cavity's high points, kept clear of the
   funnel mouths.
4. **Bottom** — `Flat` cuts a Z‑plane keeping a closed floor (the **Mounting
   Flange** adds a bolted skirt); `Open` cuts at the master's base so the cavity
   is open — its **Detachable Key Plate** instead emits a separate `MF_Mold_Base`
   whose pocket registers the model (and seals the pour) while a ring tongue on
   every shell piece's rim drops into a groove around the plate's chin collar; `Follow` leaves the bottom
   shaped to the model.
5. **Split**
   - *2 pieces*: pick the pull axis (Auto = least undercut), then either a
     ray‑cast **contoured** mid‑profile parting (self‑registering) or a flat plane
     with cone keys / interlocking teeth, plus optional clamp wings.
   - *3–4 pieces*: intersect the mold with **radial pie‑slice prisms** to get
     wedges that each pull straight out, with a best‑effort vertical seam pin
     (ridge + groove) registering neighbours.
6. **Undercut check** — rays along the pull axis flag trapped pockets; you get a
   warning (with the % and axis) if the model may not release cleanly.
7. **Volume & weight** — `bmesh.calc_volume` on the silicone, the printed parts,
   and the model, scaled by your material densities.
8. **Export** — a small binary‑STL writer (works in any context, incl. headless).

## Test

```bash
blender --background --python moldforge/tests/test_headless.py
```

Builds molds across every mold type, piece count, base, and split mode; checks the
parts are watertight single solids with sane proportions at 2/20/200‑unit scales;
verifies the glove skin uses far less silicone than a thick pour and that radial
wedges tile the same mold; exercises auto‑orientation and the undercut metric; and
round‑trips STL export. Exits non‑zero on failure.

## Robustness

MoldForge validates its result (finite, watertight, single connected solid) and
never writes a broken STL. So:

- **Detailed / thin‑feature models** (fine surface texture, fins) make the offset
  shell shatter. MoldForge detects this and **auto‑remeshes once and retries** —
  Generate just works, with a warning that fine detail was smoothed.
- **Messy / heavy meshes**: a non‑manifold mesh makes the fast boolean solver bail,
  and a million‑poly mesh is painfully slow — so MoldForge voxel‑remeshes the model
  into a clean, light, watertight solid first. A 1M‑poly non‑manifold scan molds in
  ~30 s instead of failing.
- **Deep undercuts**: `Auto` already orients the split to release best; if a half
  still won't separate, MoldForge retries the other axis (and a coarse remesh)
  before giving up, and as a last resort trims a *minor* severed fragment to keep
  each piece one solid. For undercuts on every side, raise **Mold Pieces** to 3–4
  for a radial split. If it genuinely can't make a clean mold, the error reports how
  the piece broke up (e.g. "3 pieces: 88%, 7%, 5%") and suggests what to change.
- **Deeply concave models** (a hook, horseshoe, C‑shape) shed a few tiny slivers
  along the concavity; MoldForge strips those few‑face artifacts so the parts come
  out as clean single solids.
- **Clamp wings and seam pins** are best‑effort: each is welded only where it
  actually overlaps the body and rolled back if it would ever leave a piece broken,
  so registration features can never wreck an otherwise‑good mold.
- **Separate‑piece, NaN, and degenerate** models are rejected up front or caught at
  the output stage with a clear message and no leftover objects.
- Regenerating only ever clears MoldForge's own `MF_*` objects — your own objects
  are never deleted, even if they live in a "MoldForge" collection.

## Limitations

- Contoured 2‑part parting works best on convex‑ish, centered models; for trickier
  shapes use `Auto` orientation, interlocking teeth, or 3–4 pieces.
- Very thin walls relative to model size limit how large alignment keys can be
  (keys are auto‑clamped to the wall thickness).
- Sprue/vents are placed at the model's highest vertices; complex tops may want
  manual touch‑up.
- Radial multi‑part registration is a light seam pin plus the shared flange — band
  or clamp the wedges together when casting.

## License

GPL-3.0-or-later.
