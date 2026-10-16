# MoldForge changelog

All notable changes. Versions are the add-on `version` in `blender_manifest.toml`.

## 0.44.2 — the user's model: no stray shells, the label found

Tested on the user's own model (a ridged toy with a hollow underside, on a
Locking Base).

- **Fixed: two extra "shells" (MF_Mold_A2, MF_Mold_B2) under a hollow
  underside.** The Locking Base's plinth closes the hollow, and the cavity
  carved out of the case had a sealed bubble there; the carve left a plastic
  wafer about a millimetre thick floating in the silicone space, which the
  split then cut in two. The contoured parting also failed on it and fell
  back to a flat cut. Hollows sealed inside the cavity are now filled before
  it is carved: two whole halves, and the contoured seam works.
- **Fixed: still no label on the user's model.** The quick look over the
  shell found a spot, the finer fit the plaque is built on turned it down,
  and the label was skipped. The best dozen spots are now each confirmed by
  the fine fit before one is given up.

## 0.44.1 — the label on a ridged or knobbly shell

- **Fixed: no label on a ridged or knobbly model.** The plaque was laid on the
  shell's own surface, bumps and all, and on a detailed sculpt its cuts left
  crumbs, so it was rolled back and skipped. It now sits on a smooth surface
  fitted through the shell under it (round across the label, free to swell
  and narrow along it), resting on the tops of the bumps: a clean, even plaque
  that bridges the ridges, its rim dropping down into the dips. Crumbs the
  fine cuts leave are cleared.

## 0.44.0 — a label that follows the shell, a curved seam on the Locking Base, notches on radial molds

From going back over a user review: the label was "good but not elegant
enough", band notches were missing on radial molds, and a Locking Base could
leave a half of a curved model in two pieces.

- **The label follows the shell.** It used to be a flat rectangular slab glued
  on, and on a round shell the text had to run up the side. It is now a thin
  plaque with rounded corners that bends with the shell like a label on a
  bottle (every point the same height off the surface, along the surface's own
  normal), framed by a fine line just inside its edge, its rim chamfered 45
  degrees down into the shell. The text runs across the shell and reads
  straight from the front; up the shell only when across will not fit at three
  quarters of the letter height. It stays on the case around the model, never
  on the bottom-fill channel's fin or a wing. **Label Style** cuts the letters
  in (default: crisp, nothing to snap off) or raises them. A little more air
  between the lines.
- **A Locking Base follows the contoured parting.** It was always cut on a
  flat plane, so a model that curves away from that plane at some height left
  a half in two pieces (on the user's toy, half B as MF_Mold_B and
  MF_Mold_B2), and that half had no clamp wing along most of its height. The
  seam now follows the model like on every other bottom, but stays an exact
  flat plane through the sawtooth plinth and its socket, up to the socket's
  shoulder, and blends into the contoured surface above it. That plane runs
  through the middle of the base, so each half holds half the socket and pulls
  straight off the teeth (the old flat cut ran through the middle of the whole
  mold, near one edge of the socket on a curved toy). Untick Contoured for the
  old flat seam.
- **Band notches on radial molds.** A 3 or 4 piece mold gets a row of notches
  up every seam's flange, every 30 mm and clear of the bolt holes; none on the
  bottom-fill channel's seam.
- **Fixed: a false "clamp wings are missing" warning** on a steep contoured
  seam: the check read the seam's position far out past the mold and aimed its
  probe beside the wing. It now reads it where the wing is.
- **Fewer folded faces.** The clean-up pass has a third stage for the slivers
  a contoured seam can leave where it meets a wing or the socket.

## 0.43.1 — short, thin stand legs

The user's point: the turned-over mold rests on the pour funnel's mouth, which
carries the weight; the legs only have to keep it from tipping, so they need
not run down to the widest part of the shell, nor be that thick.

- **The feet are level with the funnel's mouth.** One cut planes the mouth and
  the feet together (0.3 mm off the mouth), so the mold stands on all of them.
- **Short legs from the top of the shell.** Each leg starts where it is
  shortest to reach its foot (leaning up to 60° from upright, the shell clear
  of it all the way) instead of at the shell's widest point.
- **Only as wide as the stance needs.** The feet start just clear of the
  funnel's mouth and the channel's cup and move out until the mold's centre of
  gravity (shell, silicone and cast) would have to tip 10° before it falls. On
  a leaning or curved model the feet on its heavy side reach further.
- **Thinner.** Struts 4 to 5 mm across (up to 7 mm where one reaches far out)
  on 9 to 10 mm foot pads, where 0.43.0 had struts up to 10 mm across on 18 mm
  pads.
- **Tilt leans to the side that needs the least leg.** Tilting moves the
  centre of gravity sideways; Generate tries all eight sides and takes the one
  whose feet stay shortest while it still stands steady.
- On the user's curved toy the legs went from about 22 cm³ to 3.3 cm³ (4 cm³
  with a 12° tilt): 20 to 45 mm long. It must tip 10° before it falls.

## 0.43.0 — stand legs instead of a cast box

The user's point: the cured silicone already sits in the pour box shells,
which fit it exactly, so a second printed box mostly prints the same plastic
again (132 cm³ for the cast box against 136 cm³ for the shells, on their toy).

- **Stand legs** (Add > Stand legs, for a Pour Box with an Open Bottom or a
  Locking Base): struts welded to the shells reach past the pour funnel and
  the bottom-fill channel's cup and end in flat feet on one plane. When the
  silicone has cured, take out the positive, turn the mold over onto its legs
  and pour the cast into the open base. Each leg starts where the shell is
  widest in its upper part (above that the shell only narrows, so the strut
  never passes through it) and leans out to the mold's widest outline, so the
  feet stand as wide as the mold. Two legs per half, on the diagonals (one per
  wedge on a radial split), so the seam never cuts a leg. Tilt cuts the feet
  on an angle: the mold stands tilted and air rises out of one side. On the
  user's toy the legs add about 22 cm³.
- **The cast box is removed** (its option, its parts and its code). Saved
  settings that still mention it load without it.

## 0.42.1 — Regenerate works in the viewport; cast box and curved toys

- **Fixed: Regenerate failed with "poll() failed, context is incorrect".** After
  Generate the original model is hidden, and Blender's 3D viewport reports no
  active object at all while the active object is hidden, so the Generate step
  Regenerate called refused to run. Regenerate now builds straight from the
  remembered model. The main panel also offers "Regenerate" right after a build
  (it showed a greyed-out Generate and "Select your model").
- **Fixed: the cast box was not built on a curved toy.** It split on a plane
  through the middle of its own bounding box rather than where the main mold
  was split; it now uses the main mold's parting.
- **Fixed: a half of a curved toy lost a section of wall.** With a flat parting
  (Locking Base) a toy that curves away from the plane can leave one half in
  two real pieces - at some height the whole model lies on the other side. The
  smaller piece (up to 15% of the half) was trimmed away as a "fragment", and
  the mold rebuilt from a coarse remesh to get there. Both pieces are now kept
  as printable parts (for example MF_Mold_B and MF_Mold_B2), the mold keeps
  full detail, and Generate says so. Only slivers are still dropped. The cast
  box does the same.

## 0.42.0 — from a user review: fixes, Regenerate, bottom-fill channel, labels, cast box

A review of 0.41.0 reported the positive clipping into the base, the silicone
preview hanging below the case and clamp wings disappearing in places, and
asked for a way to pour beside the model, a quicker tweak-and-regenerate loop,
text on the case and a box for casting. All of it is in this release.

Fixes:
- **The silicone preview and the silicone to mix were too high on Open Bottom
  and Locking Base.** The preview is the gap around the model, and it was never
  cut where the cast opens: on Open Bottom it hung below the case, on a Locking
  Base it filled the plinth. Both inflated the ml and grams by 16 to 21% on the
  test toys. It is now cut at the cast's opening (the model's base, or the key
  plate's top) and kept out of the plinth.
- **The positive clipped into the base part.** A Locking Base kept separate
  (Unite with model off) still carried the 2 mm band that welds it to the
  model, so it sat 2 mm inside the printed master everywhere; its top is now a
  cradle the master's underside drops into. The key plate's pocket for the
  master was cut from the coarse copy used for models that are not watertight,
  so the detailed master poked into the plate; it is now cut from the master
  itself, to a tenth of a millimetre.
- **Clamp wings disappearing in places.** With Contoured parting (the default)
  on a model that leans along the split axis, each height's wing ended up
  wholly in one half: the wings were cut on a flat plane while the split
  follows the model's mid-depth. Wings, their bolt holes and wing keys now
  follow the same parting surface the split uses, so both halves carry their
  half of every wing. MoldForge also now says so when wings are left off
  (they failed, or the mold only built without them), when the contoured
  split fell back to a flat cut, and when a measurement after the split finds
  either half without a wing along part of the seam.
- **Black patches on shells and formers.** Big flat seam faces could carry a
  zig-zag knot a tenth of a millimetre wide, which made Blender's triangulation
  of the whole face fold over: a black patch in the viewport and inverted
  facets in the STL, even on a plain Locking Base. A clean-up pass now unties
  them on every generated part, and the suite checks a leaning model with
  every option on for folded faces.

New:
- **Regenerate.** Generate remembers the model it built from. Change a
  setting and press Regenerate in the Result panel (or the button that
  replaces Generate when a mold part is selected): the mold is rebuilt from
  the hidden model in place, nothing to unhide or reselect. The eye button
  next to it shows or hides the original.
- **Fill from the base (bottom-fill channel).** A straight pipe on the parting
  seam just outside the case, joined to it by a fin at every height, with a
  gate into the cavity right above its floor. Pour into its cup beside the
  model: the silicone enters at the base and rises evenly, nothing drapes over
  a complex master and air is pushed out ahead of it through the top funnel.
  Its radius is its own setting, not tied to the silicone thickness, and each
  half carries half of it, so the cured sprue lifts out. Works on every
  bottom, direct molds and radial splits; Other Side moves it to the other
  end of the seam.
- **Label on the case.** Debossed text on one shell: the silicone and cast
  weights by default, or anything you type with the fields {silicone_g}
  {silicone_ml} {cast_g} {cast_ml} {name} {date} and | for a new line. It goes
  on a small flat pad on the flattest spot of the shell, across or up it,
  shrunk to fit down to 3 mm letters, and is skipped with a reason when
  nothing fits.
- **Cast box.** For a Pour Box with an Open Bottom or Locking Base: two shells
  that hug the CURED silicone with the fit clearance, clamp wings, a hole for
  the silicone's sprue stub, and a straight ring wall on the closed end with a
  flat rim. Put the silicone in, flip the box onto its rim and pour the cast
  into the open base. Tilt cuts the rim at an angle so air rises out of one
  side; the cast weight is debossed on the side. Parked behind the kit,
  standing on its rims, and exported with the other parts.
- **Band notches** on the clamp wings (Clamp Wings > Band notches): a notch
  across each wing edge every 30 mm, so rubber bands stay put. Also on the
  cast box.

## 0.41.0 — saved settings, a screwed-down master, the base formers on Open bottom

Three requests from a MoldForge user.

- **Saved settings.** The preset icon at the top right of the MoldForge panel
  saves every setting under a name and loads it into any .blend in one click,
  so a new file no longer means rebuilding your setup (or copying an old
  .blend and deleting its contents). Delete and Reset to Defaults sit in the
  same menu, and Open Settings Folder shows the files to back up or share.
  Nothing ships with it; there are only the settings you save. They are plain
  JSON files in the extension's user folder, so an update never wipes them,
  and loading sets values one by one: a setting from another version that no
  longer exists is skipped and named instead of stopping the load.
- **Screw the master to the key plate** (Open Bottom with Key Plate, in
  Advanced > Shell & Base) instead of gluing it: a screw hole through the
  plate, countersunk from below so the plate still sits flat (Countersink and
  Head Diameter, 6.5 mm for M3 by default), and a hole in the master's
  underside for a heat-set threaded insert (Insert Hole 4.0 mm and Insert
  Depth 6.5 mm by default). It goes in the middle of the base; where the
  middle is hollow or thin it moves to the nearest spot with 2 mm of master
  around the insert, and a base too slim for the insert skips the screw with
  a reason. The plate is thickened under the master to hold the countersink.
- **The suction-cup former, the Vac-U-Lock plug and the dual-density core on
  an Open bottom**, with or without the key plate. They hang from four legs
  that rest on the shells' bottom rim, with hooks up the outer wall and the
  same Pin, Snap or Band fastening as the suction cup. The rim is measured
  once and the shells get one set of fastening grooves whatever is built. The
  plug rides a ring hub that keeps the bell's hollow underside open, so no
  resin gets trapped in it, and still goes where the toy is thickest. The
  core is the same eroded, ridge-rounded core as on a Locking Base; mold it in
  a second run with Bottom: Flat, legs and all. With the key plate the formers
  press into the plate's top, which is where the cast's base is.
- Each leg and its hook are now one solid instead of two boxes joined by a
  boolean. The joined boxes shared faces that the fast boolean stitched into
  zero-area slits, whose triangles folded back: black patches in a render and
  inverted facets in the STL. A final clean-up pass now removes any such fold
  from every generated part (it also cleared a few microscopic ones on
  Locking Base shells), and the suite checks for them.
- The suction-cup former's plate, riser and dome are one solid of revolution,
  so the dome can no longer come off its plate when the cast opening sits a
  fraction of a millimetre above the rim (the key plate case).
- The dual core's straight skirt is extruded from a section above its
  rounded bottom edge, so it comes out smooth instead of fluted.
- The suction-cup former centres on the middle of the base outline rather than
  the model's bounding box, which on a leaning toy is off the base.

## 0.40.4 — the dual-density core has no sharp ridges

The user's next screenshot: a scaled sculpt whose core, now smooth in the
large, carried every scale edge as a sharp ridge. That is what an inward
offset does: it fillets the skin's concave creases and keeps its convex
ones, so a vein, a scale edge or a ridge on the skin is a blade on the core,
fragile to print and a tear line in the silicone around it.

- **The core's ridges are rounded** by a morphological opening: the model is
  eroded by Soft Wall plus a rounding radius (a third of the wall, between 1
  and 3 mm) and grown back by that radius. Every convex crease becomes a
  fillet of that radius, fins thinner than twice it vanish, and the core
  never comes closer to the skin than Soft Wall, because the opening only
  ever removes material. On the suite's scaled skin (92 degree crests) the
  sharpest crease on the core drops from 43 degrees to 15 between
  neighbouring faces; on the bumpy one from 70 to 20, with the thinnest
  wall still 4.98 for 5.
- The core's stand-on skirt starts above the rounded bottom edge.

## 0.40.3 — the dual-density core is clean again at any wall

The user's screenshot: a lumpy, stair-stepped Core_Master with a shredded
tip on a textured sculpt at Soft Wall 10 and again at 5, where 0.38.0 had
looked right. The erosion offset the surface inward with Solidify and
voxel-remeshed the result when it self-intersected; on a bumpy sculpt that
inner surface folds over itself at every crease tighter than the wall,
hundreds of times, a folded surface has no consistent inside, and the remesh
returned a blob.

- **The core is now cut on a signed-distance grid** (Blender's SDF grid
  nodes): the model's distance field is sampled at about a twelfth of the
  wall and the core is the surface at exactly one wall below the skin. A
  distance field has no folds, so the core is smooth and the soft layer is
  the same thickness everywhere, on the tip, the flanks and inside a bend.
  The suite's leaning toy measures 5.0 mm on both sides of the bend and on
  top for a 5 mm wall. A model that is not watertight is remeshed at the
  same fine step first; regions thinner than twice the wall still get no
  core, which is physically right.
- The core's stand-on skirt (its lowest band pulled down to the base plane
  so the socket cross welds on) is now a clean extrusion of the core's
  section instead of snapped vertices, and the plinth union retries with the
  exact solver when the fast one leaves two pieces. The core master is one
  solid again, so its own second run molds it cleanly.
- The Result panel no longer counts the suction cup former as a shell: it
  lists "suction cup" on its own.

## 0.40.2 — the plug check starts above the bell

- The plug column is scored from 12 mm above the plinth top, where the ribbed
  plug begins, instead of from the plinth top. The bell and the neck sit in the
  plinth and the toy's base skirt, so a toy with a lobed or slightly recessed
  underside (the user's alien) no longer has its plug refused because of a
  few millimetres of hollow right at the bottom centre.
- Website and ad cards now use the user's own alien, with every printed part
  named under it: Shell A, Shell B, Positive, Suction cup, Vac-U-Lock plug and
  Core. The ad copy is rewritten in plain maker language.

## 0.40.1 — a plug that fits nowhere is refused, not centred

- **Fixed: on a base whose outline is narrower than its bounding box** (a body
  on paws, a tail, a waist) the suction bell fit nowhere on the plinth, and
  0.40.0 quietly fell back to the old centre spot with no note. The plug is
  now skipped with a plain reason: the base outline is too narrow or too
  irregular for the 55 mm bell; widen the model's base or skip the plug.
- The inside tests behind the plug search use ray parity instead of surface
  normals, so a plinth or repaired sculpt with a patch of flipped faces can
  no longer fool them.
- Website: new hero render of the full kit (shells, positive, plug, core) and
  two gallery shots, the plug finding its spot on a tilted toy and a clean
  socket mouth.

## 0.40.0 — the Vac-U-Lock plug finds its own spot

The plug column used to stand at the base centre, which on a curved or
leaning toy can be a thin valley: higher up, the vertical column broke out
through the toy's side (the user's screenshot).

- **The plug slides along the base cross to where the toy is thickest.** Every
  spot on the two arms of the cross (kept far enough in for the suction bell)
  is scored by how deep inside the toy a vertical column stays over the plug's
  full 92 mm; the best one wins. A symmetric toy keeps its plug in the middle.
- **A plug that cannot stay inside is not built.** If the best spot still
  leaves less than 3 mm of toy around the column, the plug is skipped and the
  note says how much was missing and what to do (tilt the model so more of it
  stands over the base, or skip the plug).
- The build summary reports where the plug went and how much toy surrounds
  it ("plug set 8 mm off centre along the base cross, where the toy is
  thickest, 6 mm of toy around it").
- Regression tests: on a toy leaning 20 mm the plug moves along the lean,
  stays inside with wall to spare, and the old centre spot is shown to break
  out; on a symmetric toy the plug stays centred.

## 0.39.2 — the panel carries the maker's name

- The main panel is titled **MoldForge by PLESURO**. The sidebar tab, the
  Result and Advanced panels and every object name stay as they were.

## 0.39.1 — defaults the way the bench works, more printers, no Recipes

- **Clamp wings stay on by default.** The property already defaulted to on;
  what switched them off was a recipe (Glove Mold, Direct Printed, Flat
  Tray), and Recipes is gone.
- **Bolt holes are off by default**: Auto Bolts off and Bolts per side 0, so
  the wings come out plain for clips or bands until you ask for holes in
  Advanced, Clamp Wings.
- **Printer Fit knows the current resin printers**: Anycubic Photon Mono M7,
  M7 Pro and M7 Max, M5s / M5s Pro, M3 / M3 Plus / M3 Max, Mono X; Elegoo
  Mars 4 Ultra / 5 Ultra, Saturn 3 Ultra, Saturn 4 / 4 Ultra, Jupiter SE;
  Phrozen Sonic Mini 8K S, Mighty 8K / 12K, Mega 8K / 8K S; Creality
  Halot-Mage; UniFormation GKtwo and GK3 Ultra; Formlabs Form 4; Peopoly
  Phenom Forge. Grouped by brand in the list, with the build height in the
  name. Heights are the makers' published Z; Custom is still there if yours
  differs.
- **Recipes removed**: the button, the preset menu and the shipped preset
  files. The panel starts on its defaults and keeps what you set.
- **Labels say what they are**: "Silicone thickness" and "Throat radius" in
  full, in the main panel and in Funnel & Vents. The main panel's label column
  is wider so they fit without an ellipsis at the sidebar's normal width.

## 0.39.0 — a sidebar you can read top to bottom

"The menu is still hard to navigate." Fair: one long panel mixed the action
button, the results, a units line, twelve settings and seven collapsible
sections, with clipped labels ("Mold Ty...", "Wing Al...", "Placeme...") and
each section's summary printed BEFORE its title. Rebuilt around the way a mold
is actually made.

- **Three panels instead of one.** *MoldForge*: the model you selected (name
  and size in mm - or "Select your model" when nothing is), a Recipes button,
  Mold type, Bottom, the sizes, the split, a short "Add" list of checkboxes
  (clamp wings, pour funnel, dual-density core, Vac-U-Lock plug, printer fit),
  then a big Generate button. *Result*: only after a build - what was made,
  the silicone and cast figures, Exploded Preview and Export. *Advanced*:
  closed by default, with everything else grouped by the part of the mold.
- **Labels that fit.** Standard Blender label/value columns with short names,
  so nothing is cut off with "..." at the sidebar's normal width. The Split
  choice is one row of buttons with its label.
- **Section headers read properly**: the current setting sits at the RIGHT of
  the title, dimmed, instead of in front of it. Wings, funnel, printer fit and
  export-after keep their header checkboxes.
- **Generate is greyed out until a model is selected**, and the line above it
  says why (nothing selected, or a generated part selected instead of the
  model). Throat is shown once, not twice; the version sits small under
  Generate.
- The Tray mode's settings appear inline in the main panel instead of a
  separate section.
- The registration test now drives every panel's draw logic headlessly with a
  stand-in layout across all mold types, bottoms and selection states, so a
  misspelled layout call or a property read that raises can no longer reach
  a release.

## 0.38.3 — the bar across the bottom of the shell: found on the real model

Built straight from the user's own STL with their panel settings. The model
has a flat base, so 0.38.2's hollow-underside fix (real, but a different case)
could not touch this bar. Skipping the mold's final bottom cut reproduced the
screenshot exactly.

- **Fixed: a 2 mm fin hanging under the shell bottom along the parting line,
  across the whole socket mouth.** The clamp-wing slab overhung the mold's
  bottom by 2 mm, and the wing rind (dilated offset+width from the model)
  reaches far below the base, so the union welded a fin under the shell along
  the seam. The final bottom cut always removed it here, but the fast boolean
  solver can refuse an input silently and leave the mesh untouched - on that
  machine the fin ships, and it reads as an "uncut line" across the mouth,
  flush with the socket, over the silicone skin. Now the wing slab starts a
  hair above the mold bottom and never below it, so there is no fin to remove
  in the first place (two-part and radial wings alike).
- **The bottom cut verifies itself.** If the shells' lowest point is still
  below the cut plane afterwards, the cut is redone with the tolerant EXACT
  solver.
- Regression tests build a Locking Base mold with the bottom cut disabled and
  probe half a millimetre under the plinth bottom along the seam (must be
  empty), and simulate the silent refusal to check the EXACT retry leaves the
  shells flush with the plinth bottom.
- Model files dropped in the add-on folder (`*.stl`, `*.obj`, `*.fbx` at the
  root) are excluded from the extension zip.

## 0.38.2 — the bar across the bottom of a Locking Base shell, for real

0.38.1 removed a seam ledge that was real but was not the bar in the screenshot.
The bar itself is a clamp-wing artefact, and it only appears on a model with a
hollow or domed underside - a body lying on its back, a saucer, anything that
stands on a rim rather than a flat base.

- **Fixed: a straight bar of shell bridging the socket mouth along the parting
  line, flush with the top of the sawtooth socket.** Such a model touches the
  plinth only along its rim, so the socket is rim-sized and an air pocket sits
  above the socket top under the arch. The clamp wings are cut from a rind
  dilated offset+width from the model, and under the arch "outward from the
  model" points down into that pocket: clipped to the seam slab, the rind became
  a bar hanging from the cavity ceiling across the whole mouth. It sat outside
  the silicone gap AND outside the socket, so no cutter ever touched it. The
  wings now subtract the pocket first: the socket column intersected with the
  space directly under the model (an upward-ray heightfield of the underside).
  Only the pocket goes - the flange beside a narrow body on a wide foot is the
  wing proper and is untouched, and nothing is trimmed outside the socket
  column, so a flange hugging a sphere's lower half is never cut.
- The rind is also trimmed by the cavity cutter before it is welded on, so wing
  material can never sit inside the silicone gap or the socket band on a mesh
  the later carve struggles with.
- Regression test on a cap standing on its rim: with the old wings every probe
  along the seam inside the pocket read solid; now none may, while the flange
  outside the mouth, the shell's own ceiling under the arch and part validity
  are all asserted.

## 0.38.1 — no more ledge along the seam at the bottom of a shell

- **Fixed: the "uncut line" across the bottom of a Locking Base shell.** The
  contoured parting surface is sampled by rays through the model; wherever
  they miss - below the base, beyond the silhouette, above the top - the
  cutter snapped to the mold's bounding-box centre. On a symmetric model that
  is where the mid-depth already is, so nothing showed. On a leaning or curved
  sculpt the mid-depth at the base ring is millimetres away from the box
  centre, so the parting surface STEPPED sideways exactly at the base plane: a
  straight ledge along the whole seam width at the bottom of the shell (and a
  larger one along the silhouette edge, which also cut the funnel column
  off-centre). Outside the silhouette the surface now extends the nearest
  in-model value, so it is continuous everywhere.
- Regression test on a leaning model samples the parting surface just below
  and just above the base plane, and inside vs outside the silhouette: the old
  cutter reads ~4 mm and ~15 mm steps there; both must now be under a
  millimetre.

## 0.38.0 — the dual-density core is a true inward offset

The user's screenshot said it all: on a curved sculpt the Core_Master outline
hugged one side of the original and stood far off the other. A copy scaled
about one point can never give an even layer on anything but a symmetric
shape - the inside of a bend gets a thin wall, the outside a thick one.

- **Core_Master is now the model ERODED inward by a fixed distance** - a real
  offset, built the same way the cavity is dilated outward: Solidify inward,
  keep the inner surface, heal. Every point of the core sits exactly that far
  inside the original, on the inside of a bend, the outside, the top and the
  flanks alike. The eroded bottom is pulled back down to the base plane so the
  socket cross still welds on.
- **Core Scale (%) is replaced by Soft Wall (mm)**, default 5 - you type the
  thickness of the soft layer you want, which is what you were guessing at
  with a percentage. Height compensation is gone: an offset is even by nature.
- Features thinner than twice the wall get no core (physically right: a thin
  fin is all soft); a model too thin to erode at all falls back to the old
  shrink so the build never dies.
- New test on a leaning model probes the wall on both sides of the bend at
  three heights and demands they match within a millimetre - the old scaled
  core fails it by design.

## 0.37.2 — the exploded preview is a flat parts layout

- Per the user: **every part is now laid out in one row on a single baseline**,
  same height, nothing overlapping. The skin and the positive occupy the same
  space in the build, so a vertical explosion left them stacked on top of each
  other with the master floating above the shells; a row shows each piece for
  what it is. Order: shells, base, positive, skin, then the printed accessories
  (cup, Vac-U-Lock plug, Core_Master). Press again to snap everything back.

## 0.37.1 — the exploded preview actually opens the mold

- **Fixed: the parts barely moved and the positive stayed buried.** The explode
  pushed every part away from the average of ALL part centres - and the cup,
  the plug and Core_Master are parked far to one side at build time, so that
  average sat off the mold entirely: the halves slid sideways together instead
  of opening apart. The anchor is now the mold's own axis (the positive), the
  throw scales with the mold's own width, the master lifts straight OUT above
  the opened shells, and stacked levels spread vertically so a bolted stack
  reads as a stack.
- The reassemble is now exact to the last bit: the home position is remembered
  rather than the offset (Blender stores locations in float32, so adding an
  offset and subtracting it again left parts a few microns adrift each cycle).
- **Anchor Plug is renamed Vac-U-Lock Plug** everywhere - panel, reports, docs.

## 0.37.0 — the panel stops being overwhelming

The sidebar was one flat panel of eight `box()` groups: about 60 rows and five
screens of scrolling for an ordinary Pour Box + Locking Base mold, with the
numbers you need after a build (what to mix) at the very bottom. Rebuilt:

- **A short body.** Only the decisions that change per model stay on the front
  page: Mold Type, Bottom, Silicone, Throat (+ Oversized), Split Axis, and -
  on a Locking Base - the Dual Density / Anchor Plug toggles. About 15 rows on
  a fresh scene instead of ~60, no scrolling.
- **Seven collapsible sections** (`Shell & Base`, `Parting`, `Clamp Wings`,
  `Funnel & Vents`, `Printer Fit`, `Mesh Prep`, `Export & Estimate`) hold
  everything else. Real Blender sub-panels: they remember open/closed, and each
  shows its current state **dimmed in its own header** (`locking base · united`,
  `2 pieces · contoured`, `top · no vents`), so a closed section still tells you
  what it holds. Wings, Funnel, Printer Fit and Export-after are **checkboxes in
  the section header**, switchable without opening anything.
- **Action and result together.** After a build, `Exploded Preview` and
  `Export STL...` sit on one row directly under Generate, with the silicone and
  cast figures right below them. The Generate report now speaks **ml and grams**
  instead of cubic units.
- **Presets in the panel header.** Five shipped recipes - *Toy (Pour Box,
  Locking Base)*, *Toy + Firm Core & Anchor Plug*, *Glove Mold*, *Direct Printed
  Mold*, *Flat Relief / Stamp* - set the whole structure in one click. Shipped
  recipes set structure only, never your millimetre sizes or your printer;
  **+** saves your own preset, which does carry them into every new .blend.
- Smaller fixes: the Split Axis picker is one segmented row instead of three
  stacked buttons, the version sits on the units line, and about a dozen hint
  labels that duplicated the tooltips are gone.
- New `tests/test_register.py`: 37 checks covering registration order (a
  sub-panel registered before its parent would kill the add-on at load), that
  every property the panel draws exists, that the shipped presets apply, and
  that the header summaries never crash or overflow.

## 0.36.2 — the Dual Density panel text actually explains it now

- The three cryptic label fragments (truncated to "Core_Master's cr...to the
  grooves" in a normal-width sidebar) are replaced by a four-step recipe that
  fits the panel: 1. Core_Master + Generate = core mold, 2. Cast the core in
  FIRM silicone, 3. Flip this mold, fill with SOFT, 4. Press the core in.
- Website refreshed: the same clear steps in the What's-new timeline, and the
  Exploded Preview render joined the showcase gallery.

## 0.36.1 — visibility handoff fixed, Pry Slots removed

- After Generate, **your ORIGINAL model now hides and the printable
  `MF_Positive` stays visible in its place** - it was the other way around:
  the part you actually print sat greyed out in the outliner while the
  original kept covering everything. Same for the Tray (the original would
  overlap the pan). Unhide the original from the outliner any time.
- **Pry Slots are removed entirely** (per the user) - the property, the
  panel field, the cutter and the tests are gone. The bolted wings come
  apart on their own.

## 0.36.0 — pry pockets done right, exploded preview, silicone cast preset

- **Pry slots rebuilt as one-sided pockets.** The old symmetric V bit into
  BOTH halves, so a screwdriver had nothing to lever on - it slipped and
  chewed both edges, and the 14 mm tall notches landed at fractions of the
  whole mold height, sometimes right on a bolt hole. Now each pocket is a
  small 12 x 3.2 x 6 mm recess in ONE half's mating face at the wing edge,
  placed MIDWAY BETWEEN the wing bolts; the other half's face stays an intact
  flat ledge. Slip the blade in, twist, the halves walk apart.
- **Jack Screws removed** - useless in practice, per the user.
- **Exploded assembly preview.** One button under Generate slides all the
  parts apart so you can see how the kit assembles; press again to snap
  everything back exactly. Display only - Export reassembles automatically
  first, so STLs can never ship shifted.
- **Silicone joined the Cast material presets** (≈ 1.10 g/ml) - the one
  material this add-on's own users cast most was missing from the list.

## 0.35.3 — no more cork

- **`MF_Mold_Cork` is removed.** For the inverted pour, the trimmed silicone
  sprue stub from the first pour plugs the funnel channel naturally - no
  printed part needed. All cork mentions are gone from the summaries, panel
  hints and docs.

## 0.35.2 — no plug diameter field: the plug is one size, always

- The **Plug Diameter** field is gone. A Vac-U-Lock plug is a compatibility
  part - there is exactly one right size, the authored file - so there is
  nothing to type. Tick Anchor Plug and you get the original former, period.
- A mold physically too small for the real plug (cavity under ~92 mm, or a
  base that can't take the 55 mm bell) now **skips the plug with a note**
  instead of shrinking it into a size that fits nothing.

## 0.35.1 — the plug is the user's VUL-PLUG.stl, at its authored size

- 0.35.0 upscaled the former by 10% to chase the published 27.44 mm tier spec
  and the result came out 123 mm long - too long. The bundled asset is now
  the user's revised **`VUL-PLUG.stl`** exactly as authored: **54.6 wide,
  91.8 mm total** (shorter neck), widest tier 26.8, nothing rescaled.
- **Plug Diameter** defaults to 26.8 (= the file); it still scales the whole
  former uniformly if changed, and auto-shrinks when the cavity or footprint
  can't take it.

## 0.35.0 — the combined Vac-U-Lock + suction-cup former, at verified size

- **`MF_Mold_Plug` is now built from the user's `VuL + Succ.fbx`**: one former
  that molds BOTH base features into the toy - the Vac-U-Lock channel and a
  suction-cup bell around it. Bottom-up: suction bell (Ø55.6), neck, ridged
  plug, 123.1 mm total, on the same clamped socket cross.
- **Size verified against the published standard and corrected.** The FBX's
  plug tiers measured 24.9 mm; the standard printable Vac-U-Lock spec is
  27.44 x 78.64 mm (retail Doc Johnson plugs are 1.25 in / 32 mm rubber, so a
  27.4 channel grips them snugly). The bundled asset is rebaked so the tiers
  hit 27.44 exactly; **Plug Diameter** (default 27.4 = standard) scales the
  whole former uniformly, and the footprint cap now binds on the wide bell.
- Casting stays inverted: cork the funnel, fill the base, click the former
  in - the bell traps an air cushion that keeps silicone out of it, so the
  toy demolds with a working suction cup around the attachment channel.
- The old bolt-channel fill stub is gone (this mesh needs none).

## 0.34.2 — the anchor plug IS the user's genuine Vac-U-Lock mesh

- The parametric plug silhouette is replaced by the **real
  `Combined_Vac-u-Lock.stl`** the user committed, bundled with the add-on
  (28 x 28 x 78.6 mm - the standard compatible size). It is scaled uniformly:
  **Plug Diameter** (default 28 = standard) sets the widest tier, and the plug
  auto-shrinks further only if the cavity or the model's footprint cannot take
  it. Plug Length is gone - the real mesh defines the proportions.
- The bundled-mesh path is verified by tests (the genuine two-tier flare
  profile is asserted, so a fallback build can't slip through unnoticed).

## 0.34.1 — inverted pour: bare cross, real Vac-U-Lock shape, funnel cork

Three user corrections:

- **The disc is gone - the cross is JUST a +.** The soft pour is done
  INVERTED, the way dual toys are actually cast: bolt the shells (silicone
  mold inside), flip them, fill the open base with soft, then CLICK the cured
  firm core in - its lips clamp into the grooves and the displaced silicone
  burps out freely through the cross's four open quadrants. A sealing disc
  would hydraulic-lock the insertion; open quadrants are the relief.
- **`MF_Mold_Cork`.** Inverted, the funnel points down and its sprue channel
  would drain the pour - the kit now prints a tapered cork that wedges into
  the throat (knob on top to pull it). Tape over the vent exits. Ships with
  Dual Density and with the Anchor Plug.
- **The anchor plug now has the real Vac-U-Lock silhouette** (from the user's
  reference): a straight neck, tiers that flare upward with grip shelves, and
  a bullet dome - not the stacked-bulb column.

## 0.34.0 — the socket cross: dual core clamps in, anchor plug rides it too

Dual Density redesigned once more to the user's spec, and core rods replaced:

- **Core_Master drops the full plinth.** Instead it rides a **socket cross**
  carved FROM the plinth: a thin sealing disc across the top (the cavity
  floor - the soft pour can't leak past it) over a + of two full-height bars
  whose tips keep the plinth's own sawtooth edge. **The lips ARE the teeth**,
  so the cast firm core drops into the shells' existing socket grooves and the
  bolted halves clamp it - held, centred, attached, with a fraction of the
  firm silicone the old slab wasted. No gap to bridge: the bars run the full
  socket height and the disc welds to the body's base.
- The separate stand-on plate (0.33.2's `MF_Mold_Cross`) is gone - clamping
  replaced standing.
- **Core rods are replaced by the Anchor Plug (Vac-U-Lock style).**
  `MF_Mold_Plug` is a printed ribbed bulb column standing base-centre on the
  same socket cross: bolt the shells around it when casting and the cure grips
  the ribs; pull it after demolding and the toy's base carries the attachment
  channel. **Plug Diameter / Plug Length** control it (auto-capped to the
  model's footprint and cavity height). Core Markers and `MF_Mold_Core` rods
  are removed.
- The dual core and the plug fit the SAME socket - seat one or the other per
  cast.

## 0.33.2 — Dual Density: length-compensated core + the stand-on cross

Two user corrections to the reworked kit:

- **The core's height no longer takes the full shrink.** A uniform Core Scale
  on a tall model left a soft cap many times thicker than the side wall (70% of
  a 100 mm toy = a 30 mm blob of soft on top). The width still scales to Core
  Scale, but the height is compensated so the soft layer ABOVE the core equals
  the side wall.
- **`MF_Mold_Cross` - no holes, just a + to hold it.** A printed plus-shaped
  plate the mold STANDS ON during the soft pour: the mold's own weight presses
  the seated firm core flush into the socket, the four bar ends rise as posts
  hugging the shells' outer wall (probed by ray on the real shells) so nothing
  slides, and cleat nubs take rubber bands for carrying the filled mold to a
  pressure pot. Nothing is cut or drilled into the shells.
- Molding a `Core_Master` with Locking Base now warns to use Bottom: Flat (it
  already carries its plinth - a second one would stack underneath).

## 0.33.1 — Dual Density reworked: the core locks into the socket

Rebuilt after research into how dual-density toys are actually cast (firm core
first, then the cured core is REGISTERED IN THE MAIN MOLD and the soft outer
poured around it, bonding as it cures):

- **`Core_Master` = the model shrunk to Core Scale + the SAME full-size sawtooth
  plinth as the positive**, boolean-united. Mold it in a second run, cast it in
  FIRM silicone, and the cured core **locks into MF_Mold_A/B's existing socket
  exactly like the positive does** - held, centred, and attached - then pour
  SOFT silicone around it through the funnel.
- The printed hanger stem (ball + crossbar) is GONE - the socket is the
  registration, no extra hardware, nothing dangling from the funnel.
- Dual Density therefore now requires the **Locking Base** (the panel row lives
  in that section); the second run still keeps the main mold as `Kept_*`.

Also fixed, from a user report:

- **The needle artifact at the bottom of Locking Base shells.** An air-vent bore
  drilled within a millimetre of the parting plane (or tangent to a sloped
  outer wall on a narrowing model) left a paper-thin blade of shell standing on
  the seam face - visible as a fragile needle hanging in the open bottom arch.
  Auto AND marker vents now nudge their bore a printable wall away from every
  seam plane, and a final pass shaves off any sub-millimetre shard a boolean
  still leaves (the build note tells you when it did).

## 0.33.0 — demolding aids, core rods, dual-density casting

Three features straight from the roadmap research:

- **Pry Slots + Jack Screw Holes** (Split & Clamp). V-shaped pry notches on the
  parting seam's outer edges (per side, 0-3): a flat screwdriver slips in and
  twists the halves apart without chewing the mating faces - cut into the wing
  plates, placed by ray at the actual seam edge (needs Clamp Wings).
  Tick **Jack Screws** for one hole per side through HALF A's wing only, midway
  between the bolts: thread a screw in and it jacks the halves apart evenly by
  pressing on half B.
- **Core rods / hole fillers.** Drop **Core Markers** (cursor into the model
  where the channel should END) and each becomes a printed **`MF_Mold_Core`**
  rod hanging from a registered seat in the shell top - flange in a counterbore,
  round tip - down to the marker. Cast around it for a clean round channel; the
  same rod re-inserts through the cured silicone when casting the final part.
  **Core Diameter** sets the size; rods near a funnel bore are skipped.
- **Dual Density (2-pour)** on the Pour Box: the firm-core / soft-shell kit.
  Adds **`Core_Master`** (your model shrunk to **Core Scale**, base-aligned, NOT
  MF_-prefixed - select it and Generate again for its own small core mold; that
  second run KEEPS your main mold, renamed `Kept_*`) plus a printed hanger
  **`MF_Mold_Stem`**: push its ball anchor into the fresh firm pour, cure, then
  hang the cured core in the main mold - the crossbar rests on the funnel mouth,
  leaving it open - and overpour soft silicone around it.

## 0.32.1 — Stamp maker handles real imported SVGs

- **Auto-flatten.** Blender's SVG importer often leaves the curve object with a
  -90 degree X rotation (and wild non-uniform scales) - the design would have
  stood on its edge and engraved garbage. The relief is now laid flat into the
  pan plane automatically, whatever transform the import carried.
- **Multi-object SVGs come through whole.** A logo that imports as several curve
  objects is taken from the whole SELECTION (select them all, A over the imported
  collection works), not just the active one.

## 0.32.0 — Stamp maker: silicone ink stamps from SVG or Text

New **Tray Mode: Stamp from SVG / Text** - make a real silicone ink stamp the way
commercial ones are made (a relief with the artwork raised ~2 mm over a recessed
background):

- Give it artwork: **select a Blender Text or Curve object**, or point the **SVG
  File** field at your logo (shapes must be filled paths - in Inkscape use Path >
  Object to Path / Stroke to Path first). No mesh, no master model needed.
- MoldForge extrudes the design, scales it to **Stamp Width**, and **engraves it
  into the floor of a pour-ready pan** - the printed mold is the negative.
- Pour silicone **Slab** deep, cure, peel: the slab carries the design raised by
  **Relief Depth** and mirrored, so **stamped imprints read correctly**. Glue the
  slab to an acrylic block and stamp away.
- **Mirror Design** is there for the rare case you want the stamp FACE itself to
  read correctly instead. Border, walls and floor sizes are adjustable; the floor
  auto-thickens so the engraving can never pierce it.
- Robust conversion: extrusion caps are welded, winding is fixed (an inside-out
  SVG solid used to eat the whole pan), and multi-shape SVGs keep their layout.

## 0.31.2 — the filler pipe gets brace struts

- **The pipe is no longer a fragile cantilever.** Where the shell narrows away
  from the straight pipe (a model that is widest near the base leaves a long
  free-standing run), short round **brace struts** now tie the pipe back into the
  wall about every 30 mm - each sunk into the shell, built before the cavity
  carve so it can never poke into the silicone. No braces are added where the
  pipe already touches the wall, and the pour channel is bored straight through
  afterwards, so it stays fully open.

## 0.31.1 — the marker is never silently swallowed

Three fixes found by rebuilding the user's exact setup (Locking Base + clamp wings
+ contoured parting + Oversized Throat 20 + marker low on the bulge):

- **Fixed: the marker was silently dropped.** The spout keep-out scaled with
  Throat Radius - with an Oversized Throat of 20 mm it grew to 30 mm and ATE any
  marker placed within 30 mm of the primary funnel's column, so nothing was built
  at all. The keep-out is now a tiny 2 mm coincidence guard: a filler pipe lives
  outside the shell and cannot collide with the central bore anyway.
- **Fixed: a marker under a low shoulder built the old coreable spout.** "Near the
  top" now means under the mold's actual top plateau, not merely close beneath any
  sloped surface - a bulge marker takes the filler pipe.
- **Locking Base: the gate can never open into the socket band** (the region that
  holds the printed base, below the cast) - it is lifted just above the plinth.

## 0.31.0 — side pour rebuilt from scratch: the filler pipe

The side Pour Marker is REBUILT as the simplest geometry that works on any shape,
after the contour-following runner kept mis-routing on real sculpts:

- **One straight, slim vertical pipe** (bore capped at 3.5 mm radius regardless of
  Throat Radius) standing just outside the widest point of the shell above the
  marker. Nothing follows the contour; there is nothing to mis-route.
- **A solid collar bridges pipe to wall at the marker**, and the gate bores through
  it into the pour gap exactly at your spot - the pipe is always welded at the
  widest point AND at the gate.
- **All probing happens on the pristine shell** before any spout is unioned, so the
  pipe can never anchor onto the main funnel (the previous failure).
- **Pour Markers are their own feature now**: they build even with the main Pour
  Funnel switched off. The cup mouth always ends above the silicone fill level.

## 0.30.4 — the runner is slim and hugs the shell

- **Runner size fixed.** 0.30.3's runner inherited the full Throat Radius and stood
  as one straight chimney clearing the mold's WIDEST bulge, with a chunky stub bar
  to the wall - huge on a big mold. The runner bore is now capped (a gate never
  needs the main sprue's flow; up to 7 mm channel), and the tube is built as a
  slim SEGMENTED pipe that follows the shell's contour, welded to the wall the
  whole way with rounded elbows - no stub bar at all. The cup sits compactly just
  above the rim, still safely over the fill level.

## 0.30.3 — side pour markers become an external runner

- **A deep side marker now builds a proper RUNNER instead of butchering the wall.**
  0.30.2's straight shaft to a mid-height side point cut a trench through the thin
  hugging wall and the spout could get cored by the cavity carve (the floating
  ring). The real mold-maker's mechanism is now built instead: a **tube welded up
  the OUTSIDE of the shell**, from a small **radial gate** through the wall at your
  marker to a **funnel mouth above the mold top** - it cannot overflow while
  pouring, and the silicone enters the gap exactly at your spot and fills
  bottom-up (a classic bottom-gate riser, which also traps less air). Markers
  near the top surface keep the plain vertical spout.

## 0.30.2 — pour markers bore all the way to the marker

- **Fixed: a side/mid-height Pour Marker left its spout floating** with a short
  bore that never reached the spot. The auto funnels anchor to the model's local
  top under the spout column (correct for top pours) - a marker on the side of a
  bulgy model broke that assumption. Marker funnels now stand on the MOLD's own
  outer surface straight above the marker and bore a straight channel from the
  mouth ALL the way down to the marker itself, opening into the pour gap exactly
  where you pointed (or breaching the cavity on a direct mold). The marker also
  snaps to the nearest model surface point, so its depth under an overhang is
  honoured; a marker whose column misses the mold entirely is skipped.

## 0.30.1 — marker how-to tips right in the panel

- The panel now teaches the marker workflow inline: with Vents on Manual and no
  markers yet, it says "Shift+RClick the model to place the cursor, then Add Vent
  Marker"; with markers placed it shows the count and the move/duplicate/delete
  keys (G / Shift+D / X). The pour-marker "+" gets the same hint before the first
  marker exists. Full instructions stay in the button tooltips.

## 0.30.0 — place vents and extra pour spouts exactly where you want

New **marker-based manual placement** - markers are ordinary little empties you
move (G), duplicate (Shift+D) and delete (X); they live in a "MoldForge Markers"
collection and survive regeneration, so you tweak and re-generate freely.

- **Vents: Auto or Manual (markers).** Auto stays the default (highest points,
  spaced apart). Switch Vents to **Manual (markers)**, snap the 3D cursor onto the
  model (Shift+Right-Click), press **Add Vent Marker** - Generate drills exactly
  ONE vent at every marker, nowhere else (Vent Count is ignored). The marker's full
  3D position counts, so you can vent a side pocket at mid-height. A marker whose
  channel would collide with a funnel bore is skipped.
- **Extra pour spouts at Pour Markers.** The FIRST spout keeps the Placement
  setting exactly as before. **Add Pour Marker** drops a cone marker; each one adds
  an extra spout at its X/Y (dropped onto the model surface), replacing the
  automatic extra Pour Points. The Pour Points count field disables while markers
  exist, and the panel shows how many are active.

## 0.29.4 — the positive keeps FULL detail on every mold type

- **Fixed: the positive lost its high-poly detail.** On a plain pour box (or any
  non-Locking-Base mold), a heavy or non-watertight sculpt was auto-remeshed for
  the build, and the displayed/exported `MF_Positive` was that smoothed copy - it
  came back faceted. The positive is now ALWAYS a full-detail snapshot taken
  before any voxel pass (the way the Locking Base already worked): the mold is
  built from an internal proxy, and your master keeps every polygon.
- The "auto-remeshed" warning is now honest about what it affects: a pour box
  captures detail from the master you nest, so nothing is lost there; only a
  Direct Printed Mold's cavity is carved from the remeshed copy and still warns.

## 0.29.3 — positive sections self-align: pegs into sockets

- **Simpler, better section joinery.** Instead of loose dowel pins in matched holes
  (plus a separate pin plate to print), each seam of the sectioned positive now
  aligns itself: **pegs are printed directly on the lower section's face** (Bolt
  Diameter, tapered tips) and seat into **clearance sockets** bored into the next
  section. Stack, glue, done - no loose hardware, no `MF_Positive_Pins` part.
  Sections are sized so body + peg still fits under the usable height, key spots
  are verified inside the solid at both the peg root and tip, and the cast
  surface is never touched.

## 0.29.2 — Printer Fit: on/off checkbox + printed alignment pins

- **Printer Fit is now a proper checkbox.** Tick it to reveal Printer / Max Print
  Height / Support Height / Cut Positive Too; untick it and nothing is ever split -
  a typed height alone no longer arms the feature (previously "0 in the field" was
  the only off switch).
- **The dowel sockets get printed pins.** Sectioning the positive used to leave you
  empty holes. Now a small **`MF_Positive_Pins`** plate prints alongside: snip-off
  pins sized to the sockets (Bolt Diameter minus Fit Clearance, tapered tips for
  easy lead-in), two spares included, parked beside the mold and exported with
  everything else. Bamboo skewers still work if you prefer.

## 0.29.1 — Printer Fit fixes: typo guard, fair seams, positive toggle

- **A typo-small Max Print Height can no longer shred the mold.** Typing 15 when you
  meant 150/165 (or thinking in cm) used to slice everything into the maximum number
  of tiny levels. The usable height is now floored at a 25 mm minimum level, the
  panel shows a red warning under the field for values below 60 mm ("field is in
  mm - a Photon is 165, an Ender-3 250"), and the build summary explains what was
  typed and what to set. Real printer heights behave exactly as before.
- **Fixed: only the bottom half was cut.** With a manual Horizontal Split on, the
  8-level cap was spent greedily bottom-up - the region below the manual seam ate
  the whole budget (cut into bits) and the region above got no seams at all. The
  budget is now split fairly across the regions.
- **Fixed: a messy positive silently stayed whole.** On a Locking Base a
  non-manifold sculpt keeps its mess in the positive, and the strict "perfect
  sections" check then quietly rolled the cut back. A messy source can never yield
  cleaner pieces than itself, so plausible sections are accepted there - the
  positive now sections like everything else (a clean positive is still validated
  strictly).
- **New "Cut Positive Too" checkbox** (on by default) next to Support Height -
  untick it to always keep the positive whole and get the too-tall note instead.

## 0.29.0 — Printer Fit: too-tall shells split into bolted stacked levels

New **Printer Fit** in Split & Clamp. Pick your **Printer** from the presets (Photon
Mono/M3/M5s, Mars, Saturn, Jupiter, Halot, Ender-3, Prusa, Bambu ...) or type a custom
**Max Print Height** - and any shell taller than that is automatically cut into as many
**stacked levels** as needed, so every piece fits your build plate.

- **A bolted mating ring at every seam.** Each horizontal seam gets the flange ring all
  around (the XL-mold ring, sized by Bolt Diameter for threaded inserts below / screws
  from above), so the stack bolts back into one rigid shell - easy to hold, easy to
  clamp. Ring seams are kept below the funnel neck so every ring has wall to weld onto.
- **Smart splitting.** Levels are equal (no sliver pieces), the manual Horizontal Split
  seam is respected and only subdivided further if still too tall, and the stack is
  capped at 8 levels. Files are named in print order: `MF_Mold_A_S1` (bottom) upward;
  a single seam keeps the classic `_Bot`/`_Top`.
- **Support Height reserve (default 5 mm).** Printers lift the print on supports or a
  raft, so a level sized to the bare plate height would not actually fit. The reserve
  is subtracted from Max Print Height when sizing levels - every piece fits WITH its
  supports. Adjustable next to the height field.
- **The positive is cut too.** A too-tall positive (the Locking Base one especially)
  comes out as equal glue-up sections - `MF_Positive_S1` upward - with internal
  **dowel sockets** bored across every seam: drop short pins (Bolt Diameter, e.g.
  3 mm bamboo skewer) in, glue, and the master reassembles perfectly aligned.
  Nothing is ever added to its OUTSIDE - the cast surface stays exactly the model's -
  and on a united Locking Base the seams stay above the sawtooth plinth. If a messy
  mesh defeats the cut, the whole positive is restored and the build says so.
- **Honest reporting.** The build summary says how many levels you got; you are warned
  if a level still exceeds the limit (a tall funnel spout can), and if the POSITIVE
  itself is taller than the printer (print it tilted or split it yourself).
- Works with the pour box and direct molds, radial wedges, the Locking Base and the
  suction-cup former. 0 (or Custom with no height) = off, exactly as before.

## 0.28.3 — Export includes the positive

- **Fixed: Export skipped the positive.** Both the Export Mold Parts button and
  "Export STL after generating" wrote only the MF_Mold_* parts (shells, separate
  base, suction-cup former) - MF_Positive was never exported, even though with a
  Locking Base the positive+plinth is a print too. Export now writes every
  generated print: the shells, base/former parts, AND the positive. The MF_Skin
  silicone preview stays out (it is the pour, not a print).

## 0.28.2 — Locking Base positive is now a TRUE union (fixes resin hollowing)

- **Fixed: slicer hollowing left the base solid.** Since 0.25.2 the "united" positive
  was a plain mesh join - the model and the sawtooth plinth were two overlapping
  closed shells inside one object. That prints fine, but a resin slicer's hollow tool
  (Anycubic Photon Workshop, Lychee, Chitubox) computes an offset surface: with two
  nested shells it hollows the model and treats the plinth's own closed shell as solid
  material, wasting resin. The plinth is now **boolean-united** into the high-poly
  positive and the result is verified - one island, fully manifold, nothing lost - so
  hollowing sees ONE solid and empties the base too.
- **Safe fallback, reported.** If the union cannot be verified on a messy source mesh
  (non-manifold sculpts can defeat even the EXACT solver), the build falls back to the
  old overlapping join so it still succeeds - and the build summary now says so and
  suggests healing/remeshing the model for a clean union. Full detail is kept either
  way; "Unite Base with Model" OFF (separate printable base) is unchanged.

## 0.28.1 — former Fastening modes (resin-safe Pin-Lock default)

Snap-fits need a filament that flexes - brittle cured RESIN hooks crack on the first
click. The former's hold-down is now a **Fastening** choice:

- **Pin-Lock (default, resin-safe):** zero flex required. The former slides on freely;
  a tangential half-channel through each hook lines up with the shell groove, and a
  ~2 mm pin (bamboo skewer, 1.75 mm filament offcut, a nail) slides in - sitting half
  in the hook, half in the wall. Pure shear, no bending, holds the buoyant uplift.
- **Snap-Lock (flexible filament):** the 0.28.0 printed beads that click into the
  grooves - quick and tool-free on PLA/PETG/ABS.
- **Band Cleats:** outward lips on the hooks catch rubber bands; nothing is cut into
  the shells at all.

## 0.28.0 — Suction Cup Former: snap-lock legs + hollow underside

- **Snap-lock legs.** The pour FLOATS the former - the dome displaces silicone and
  buoyancy pushes the former off the mold - so resting legs are not enough. Each hook
  now carries a rounded **bead**, and a matching shallow **groove is cut into the
  shells' outer wall** at every leg position: push the former on until it clicks. The
  round profile gives lead-in both ways (easy on, pry off), and the engaged beads
  positively resist the uplift. Grooves are cut only where a leg actually meets a real
  wall, and they are shallow (0.75 mm) so the shell stays sealed.
- **Hollow underside.** The dome is now a constant-thickness shell (2.4 mm) and the
  riser a tube, opened through the plate from below - the same outer surface with far
  less filament. Two safety rules keep the part one printable solid: the tube core is
  sized to the dome footprint (never the wider riser), and the dome void is carved only
  when it genuinely opens to the underside - a small bulb dome stays solid rather than
  trapping a sealed void.

## 0.27.4 — Suction Cup Former: manual sizes respected + clearer scene

- **An explicit Cup Diameter is now used as typed.** It was clamped to the model's base
  opening, so typing a bigger value did nothing. The bell may legitimately be wider than
  the opening (it simply truncates there); the only hard cap now is the **insertion
  limit** - what physically fits in through the shells' bottom. On a Locking Base that
  is the sawtooth socket: raise **Base Margin** for a wider socket and so a wider bell.
  Auto (0) still sizes to ~70% of the model's base opening.
- The former is now **parked beside the mold** in the scene instead of nested inside the
  socket, where it interpenetrated the positive on screen and looked broken (it wasn't -
  the positive is out of the mold when you cast). Scene position is display only; the
  exported STL is unchanged.

## 0.27.3 — Suction Cup Former: legs sized to the bottom rim, always

- Fixed the sprawling legs on models that bulge out just above a short base: the leg
  reach was probed 4 mm above the rim, which on such shapes hit the FLARED jacket wall -
  so the legs stretched to the bulge and the hooks floated in mid-air. The legs are now
  sized strictly in the mold's **bottom band**: two low wall probes per leg (max of the
  two, so a leaning skirt still clears), clamped to the band's true footprint measured
  from the mold's own vertices, and the hooks are short (3 mm) bottom-edge grips that can
  never collide with a wall flaring above them.

## 0.27.2 — Suction Cup Former: press at the model's base, not the rim

- Fixed on the Locking Base: the dome pressed into the EMPTY SOCKET zone (where the
  printed base sits, Base Height below the cast) instead of the pour. The former now
  carries the dome on a **riser column through the socket** so it presses into the cast
  exactly at the **model's base plane** (the model minus the locking base); the riser's
  flat top is the shoulder that seats against the opening's rim, and it is kept inside
  the socket. Legs/plate still register on the shells' bottom rim.
- The opening is now measured from the **model's own base footprint** (raying the master
  just above its base) - the true pour opening - instead of the shells' inner wall,
  which overestimated it by the silicone gap / socket width. A rounded, unflattened base
  correctly yields a small cup: flatten the model's base for a big suction bell.

## 0.27.1 — Suction Cup Former: Locking Base support + high-poly dome

- The former now works with the **Locking Base** too: it seats at the shells' ACTUAL
  bottom (the plinth bottom, Base Height lower than the model) and its dome is sized to
  the **sawtooth socket opening** - both measured off the mold itself by ray-casting the
  inner wall just above the bottom, so any base height fits automatically.
- The dome is now **high-poly by construction**: a smooth ring-built spherical cap (like
  the locking plinth) on a 144-segment plate - no icosphere faceting, no boolean clip
  seam. The cast bell comes out smooth.

## 0.27.0 — Suction Cup Former

New **Suction Cup Former** option for Open Bottom molds. Alongside the shells, MoldForge
prints **`MF_Mold_Cup`**: a smooth dome (**Cup Diameter** x **Cup Depth**; diameter 0 =
auto, about 70% of the bottom opening) on a backing plate with **four diagonal legs**
that seat over the open base - the legs rest on the mold's bottom rim and end in upright
tabs that hug the outer wall with Fit Clearance, so the former registers centred (the
diagonal legs clear the clamp wings, and the reach is ray-cast from the mold's own wall,
so it fits hugging jackets and blocks alike).

Workflow: cast with the mold inverted (open base up), fill with silicone, press the
former in - the material cures around the dome, leaving a **suction-cup bell** in the
cast's base. Pop the former out after cure. The former is validated and exported with
the other parts.

## 0.26.4 — fully manual funnel sizes (Oversized = no cap)

- **Oversized Throat and Oversized Mouth are now fully manual.** With the toggle on,
  the funnel uses EXACTLY the sizes you type — no auto-fit cap at all (previously
  Oversized still capped the throat at ~45% of the mold half-width, and the live UI
  clamp kept snapping typed values down, so it never grew past that). With the
  toggles off, nothing changes: sizes still auto-fit the mold. Turning a toggle off
  re-clamps the field to the fit. The Throat Radius slider's drag range is also wider
  (soft max 60 mm); typed values were never limited.
- The panel's "Built:" row shows the true manual sizes and still warns when they
  exceed what the mold would normally take — you own the result.

## 0.26.3 — Key Height + 0.2 mm mating clearances

- New **Key Height** for the Wing Alignment keys: how far they stand out past the
  mating face. **0 = automatic** (proportional to Key Size, capped by the wing lip so
  the socket never pierces the wing — why the keys looked "low"). Set it higher for
  taller keys: past the lip the socket simply punches through the mating wing as a
  hole, which still aligns and prints fine. A Half Sphere caps at a full hemisphere —
  any taller would bulge wider than its socket mouth and could never assemble.
- **Fit Clearance and Lock Tolerance now default to 0.2 mm** (was 0.3) — a better
  starting point for well-tuned printers; loosen them if your prints bind.

## 0.26.2 — Wing Alignment: Key Size + Key Spacing

- The wing alignment keys are now **adjustable**: **Key Size** sets their diameter
  (default 6 mm — noticeably bigger than before; the footprint grows freely while the
  protrusion stays auto-capped by the wing lip, so a socket can never punch through —
  a large Half Sphere becomes a wide spherical cap), and **Key Spacing** spreads them
  **evenly along the seam at that pitch (default every 40 mm)** instead of at most two
  per side, still dodging the bolt holes. Both fields appear under Wing Alignment when
  a key shape is picked.

## 0.26.1 — Wing Alignment keys (Cone / Half Sphere / Half Cone)

- New **Wing Alignment** option for the clamp wings: raised keys on one half's wing
  mating faces seat into matching sockets in the other, so the bolted halves can't shear
  sideways. Three shapes — **Cone** (pointed pin, self-centering), **Half Sphere** (dome,
  smooth engage/release), **Half Cone** (truncated pad, sturdy) — or **None** (default,
  bolts only, the previous behaviour).
- Keys are placed **midway between the bolt holes** (never on one), sized to the wing lip
  so a socket can never punch through the mating wing, and the socket is grown by **Fit
  Clearance** so printed parts actually seat. Works with both flat and contoured partings
  (the parting is flat across the wings either way), and each key is validity-checked
  with rollback — a key can never break a good mold.

## 0.26.0 — Locking Base: "Unite Base with Model" checkbox

- New **Unite Base with Model** toggle on the Locking Base (default on = the existing
  behaviour). Untick it and the sawtooth base is **not** joined into the positive:
  `MF_Positive` stays your untouched model, and the base comes out as its own printable
  part, **`MF_Mold_Base`** — validated and exported together with the shells. Use it when
  you want to print the base separately and attach the master to it, or run the boolean
  yourself. The shells' tolerance socket is identical either way.

## 0.25.7 — Locking Base: teeth grip at any base height

- Fixed: the sawtooth teeth didn't transfer to the shell socket when **Base Height was
  small** (below ~10 mm), so the shells came out with a smooth socket that couldn't grip
  the base. The socket was built by a Solidify offset of the plinth, which self-intersects
  on the finer tooth pitch of a short plinth and got voxel-remeshed smooth. The socket is
  now built from the **same clean rings as the plinth** (a hair wider for the tolerance
  fit), so its teeth match the plinth exactly at any base height.

## 0.25.6 — "All sizes in mm" panel caption

- The panel now states **"All sizes in mm"** at the top, so it's obvious the size fields
  are millimetres whatever the scene's unit system. In an Imperial scene (or a configured
  metric scale) it also shows the basis the build uses, e.g. **"1 unit = 25.4 mm"**.

## 0.25.5 — correct sizes in Imperial / non-mm scenes

- Fixed a real bug: in an **Imperial** scene, or a metric scene whose unit **isn't
  millimetres**, the size fields were used as raw Blender units, so molds came out at the
  wrong scale (an Imperial scene was wrongly assumed to be "1 unit = 1 mm").
- Size fields are now **millimetres** everywhere and the build converts mm to Blender
  units with the scene's actual unit scale (`core.units.mm_per_unit`). The mm-per-unit
  basis is: **None** → 1 (the mold convention); **Blender's untouched default** (Metric ·
  Unit Scale 1.0 · Meters) → 1 (so existing files aren't shrunk 1000×); **Imperial, a
  changed Unit Scale, or a specific metric Length unit** → real (`scale_length × 1000`).
  The volume/weight estimates and the live sprue/vent caps use the same basis, and the
  panel shows it (e.g. *1 unit = 25.4 mm*).
- The size fields are plain millimetre numbers now (they no longer change with the
  scene's display unit). If you had typed custom sizes in a real-unit scene before, double
  check them once — they read as millimetres now.

## 0.25.4 — Locking Base (Pour Box)

New **Bottom → Locking Base** option for the Silicone Pour Box. A footprint-hugging base
**plinth with a fine sawtooth (zigzag) edge** is built from the model's own base and united
into the high-poly positive; the printed shells get a matching **socket that hugs the
plinth with a tolerance fit (≈0.3 mm)**, so the shells lock onto the base and can't slip,
and the bottom prints open so the master plugs straight up into it.

- **The positive keeps the model's full detail** — `MF_Positive` is your original mesh with
  the plinth united in, never the internal cleanup remesh.
- **High-poly, crisp sawtooth** built from a clean outline of the model's footprint (uniform
  teeth, no spikes or seams). The **outside of the shell stays smooth** (the teeth live only
  on the inner socket), and the **rim is sealed** (no holes at the inside corner).
- **Open bottom** across the whole footprint; **model-only clamp wings** (they hug the
  model, not the base — bolt them or band the halves, the base handles anti-slip).
- Handles **messy / multi-island / non-manifold sculpts**: the jacket is built from a
  cleaned proxy so touching sub-parts merge and mold cleanly, while the master stays
  high-poly. Genuinely separate pieces fail fast with a clear "join them" message.

Controls: **Base Height / Base Margin / Sawtooth Teeth / Tooth Depth / Lock Tolerance**.
Pre-flatten the model's base so uniting the plinth loses no detail.

**Internal robustness (benefits every mold type):** the offset dilation detects a Solidify
even-offset blow-up and falls back to a plain offset, and the voxel remesh is capped so a
stray spike can't exhaust memory.

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
