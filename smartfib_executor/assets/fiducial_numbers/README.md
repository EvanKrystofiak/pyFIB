Numbered fiducials (1-30) -- source: elynumbers v0.3.0 (out/plain/).

polygons_um.json  loop-free seven-segment hexagon polygons, um, origin = number centre,
                  +X right, +Y up (top row = +Y). Verified by elynumbers --verify:
                  ONE connected unmilled region per number, 0.9 um bridges.
manifest.json     per-number width / milled area.
contact_sheet_plain.png  visual reference (WHITE = milled).

pyFIB never ships hand-written .ely for these: smartfib_executor.fiducial writes each
number through elymaker's own LayoutBuilder.polygon() + write_ely, offset to the centre
of the prototype square (fiducial_left.ely / fiducial_right.ely) and with the
prototype's exposure (probe, recipe, depth, frame). Regenerate/inspect all 60 with:
    python -m smartfib_executor.fiducial --write-all <dir>
