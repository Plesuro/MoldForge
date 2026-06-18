# MoldForge changelog

All notable changes. Versions are the add-on `version` in `blender_manifest.toml`.

## 0.23.2 — require Blender 5.1

- Raised `blender_version_min` to **5.1.0** (was 4.5.0). MoldForge is built and
  tested on Blender 5.1 and 4.5 is not supported.

## 0.23.1 — submit-ready housekeeping

- Updated the bundled `README.md` to cover all three mold types (it still listed
  only two) and dropped a stale install example.
- Added the `Object` tag alongside `Mesh` and `Modeling`.

No functional code changes.

## 0.23.0 — Tray refinements

- **Hug (rounded) outline** for tray molds: the walls follow the object's outline
  with rounded corners instead of a rectangle, so a round or irregular object uses
  noticeably less silicone and plastic (~20-25% on a round disc). Choose it with the
  new Outline option; Rectangular stays the default, and Hug falls back to a
  rectangle if a shape can't be hugged cleanly.
- Removed the **Carve** tray mode. The two remaining modes — Embed (silicone stamp)
  and Frame (real object) — are the reliable open-pour workflows; a direct relief
  cast is better served by the Direct Printed Mold type.

## 0.22.0 — Tray / open-pour mold for flat & relief objects

New third mold type, **Tray / Open Pour**, for flat objects — text, logos, coins,
medallions, relief tiles — that only need one face captured. It builds a one-part
open-top pan (no split, wings or funnel) in three modes:

- **Embed → silicone stamp** — fuses the object into the tray floor; pour silicone
  over it for a flexible negative stamp/mold.
- **Carve → direct cast pan** — sinks the relief into the floor as a recess so the
  printed pan IS the mold; pour resin/plaster/wax straight in. The floor is
  auto-thickened so the recess can't break through, and an optional Mirror keeps a
  cast reading the right way round.
- **Frame only** — prints just the open box at the object's footprint, to drop a
  real object in and pour silicone around it.

The object is auto-laid-down on its flattest side with the detailed face up (the
open pour side); a manual Capture Face override (Z/X/Y) is available. The panel
hides the split/clamp/sprue controls in tray mode and shows the pour/cast/plastic
volume estimates for the chosen mode. A pre-build warning flags an object that
isn't actually flat (a wrap-around type suits it better).

## 0.21.0 — robustness, units, and quality-of-life

**Robustness / correctness**
- Booleans now fall back to the **EXACT** solver automatically when an input is
  non-manifold. The fast MANIFOLD solver silently refuses non-manifold input and
  left the mesh untouched — the root of several "broken mold" cases. The solver is
  chosen per operation and verified, so a messy/scanned mesh still cuts correctly.
- **Scene unit scale** is respected. Sizes and volume/weight estimates previously
  assumed 1 Blender unit = 1 mm; a non-mm scene is now honoured (and the panel shows
  the basis), so proportions and ml/g are correct in metric or imperial scenes.

**Funnel**
- **Oversized Throat** toggle (parity with Oversized Mouth): lift the throat past
  its auto-fit cap when you need a wider pour, with a UI warning.

**UX**
- **Staged progress**: a heavy build no longer looks frozen — the cursor progress
  and the status bar report each phase (prep, shell, funnel, wings, split, bore, base).
- **Pre-build warnings**: non-manifold ("detail will be smoothed") and very-heavy
  ("build may be slow") meshes are flagged *before* the build, not only after.
- **Material presets**: pick a common silicone/resin (Dragon Skin, Smooth-Cast, …)
  instead of typing densities by hand.

**Internal**
- Tuning constants (funnel caps, voxel sizes, wing factors) centralised in
  `core/constants.py`.
- The recovery snapshot derives its property defaults from the PropertyGroup, so a
  new build property can't drift out of sync.
- Added `LICENSE` (GPL-3.0) and this changelog.

## 0.20.x — funnel, wings, detail, distribution (highlights)

- 0.20.20 — Oversized Mouth (flare past the fit cap, with warning); self-hosted
  Blender extension repository for in-app updates.
- 0.20.16–0.20.19 — funnel placement modes (Center XY/X/Y, Highest Point, Manual
  X/Y); funnel welds to the shell with no one-sided gap; tapered cone neck so it
  doesn't break the contour; curved-model funnel no longer spikes to the base.
- 0.20.10–0.20.15 — direct printed mold keeps a high-detail cavity (carved from the
  full-detail model, cavity carved last like the pour box); solid contoured clamp
  wings with clean edges; fixed an out-of-memory crash on heavy direct molds.
- 0.20.0–0.20.9 — settable throat; funnel bored before the cavity cut; wings contour
  the body and run up the funnel; detachable keyed base plate follows the contour;
  Fit Clearance for the plate groove; per-vertex panel slowdown fixed.
