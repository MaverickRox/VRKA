# VRKA Production Icon Assets

Version 2.0.0, build 010

- Signature colour: `#8140DC` / RGB `(129, 64, 220)` (application UI accent).
- Canonical artwork: `vrka-build010-canonical-source.png` - the approved
  **two-colour wolf** (purple geometry with internal black geometry: inner
  ears, eye cutouts, nose, cheek markings) rendered on a solid black
  background. This is the single canonical artwork for the build010 branding
  pipeline; it is never redrawn, recolored, simplified, or reinterpreted.
- Background: true RGBA transparency. Only the external black background is
  removed (border flood fill); the internal black geometry stays opaque black.
- The antialiased silhouette edge is preserved as partial alpha, so the wolf
  renders correctly on any background.
- Every production size is derived from the canonical PNG by uniform scaling
  and centering only (proportions and silhouette preserved).
- No gradient, shadow, glow, watermark, checkerboard, filter, script or animation is present.
- There is no alternate compact face. Tiny rasters use the same geometry with
  size-specific rasterisation and safe padding.
- `vrka-wolf-*.png` contains validated runtime/platform sizes.
- `vrka.ico` contains 16, 20, 24, 32, 40, 48, 64, 128 and 256 px images.
- `vrka.icns` covers standard Retina pixel sizes through 1024 px.
- `icon-manifest.json` records validation results, extraction statistics and
  alpha bounding boxes, plus the canonical source image's SHA-256 provenance.
- The legacy flat-purple `vrka-wolf-master.svg` trace was removed; it was the
  source of the build008-era single-colour icon and is not part of build010.

Regenerate deterministically from the project root with:

```text
.venv/Scripts/python tools/generate_brand_assets.py
.venv/Scripts/python -m unittest -v test_vrka.VRKARegressionTests.test_production_icon_assets_are_two_colour_transparent_and_complete
```
