# Assets and licensing policy

Foundation inventory:
- src/breach/scene.py: original procedural meshes, color/material definitions,
  and deterministic navigation marker placement; MIT (repository LICENSE).
- artifacts/**/*.png: direct screenshots of those meshes and framework text,
  captured by this application; not third-party concept art or mock frames.
- Panda3D 1.10.16: installed wheel, Modified BSD; upstream license copied verbatim
  from wheel metadata to docs/PANDA3D-LICENSE.txt. Its bundled default Perspective
  Sans font is used through the framework, not separately extracted or relicensed.
- No external models, textures, music, sound recordings or downloaded art assets.

Use original procedural content by default. Future external art must be CC0 or
CC-BY-4.0, code MIT/BSD/Apache-2.0 compatible, fonts OFL-1.1 or another explicitly
redistributable license. Do not use unlicensed web images, franchise likenesses,
noncommercial-only assets or files with missing provenance. Keep each imported
asset entry in an assets/manifest.json with path, original URL, creator, license
identifier, local license-text path, modifications and SHA256. Preserve required
attribution. Original assets record creator and creation method instead of URL.
Review generated-art service terms/provenance before adding any generated asset.

Packaging owner must inventory the actual bundled engine/native libraries/fonts,
retain their notices and review transitive licenses before shipping binaries;
this source foundation does not imply that audit has already happened. Do not
copy an entire development virtualenv into a distributable. The MIT project
license does not override third-party terms. No runtime network asset fetches.
