# VRKA production typography

VRKA bundles only the two font files used at runtime:

- `SpaceMono-Regular.ttf` — normal interface, controls, helper text, logs, paths and commands
- `SpaceMono-Bold.ttf` — page titles, section headings, navigation emphasis and status emphasis

The source archive supplied Italic and Bold Italic files as well. VRKA does
not use italics, so those files are intentionally not packaged.

The family name reported by both production files is `Space Mono`.

The fonts are registered privately for the VRKA process. They are not
installed permanently into Windows or macOS. If registration fails, VRKA
falls back to Segoe UI on Windows or SF Pro Text on macOS for interface text,
and Consolas or Menlo for technical text.

Redistribution and application embedding are permitted under the SIL Open
Font License 1.1. The complete licence is included as `OFL.txt`.
