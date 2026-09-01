# FFmpeg redistribution checklist

FFmpeg can be LGPL or GPL depending on its build configuration. The exact binary determines the obligations.

This document is not legal advice.

## For the exact bundled binaries

Record:

```powershell
ffmpeg -version
ffmpeg -buildconf
ffmpeg -L
ffprobe -version
```

Preserve:

- FFmpeg version;
- build commit;
- configuration flags;
- whether GPL components are enabled;
- licence output;
- source code location for the exact commit/release;
- upstream modifications, if any;
- full applicable licence text.

## Distribution package

Include or clearly provide:

- FFmpeg copyright notice;
- applicable LGPL/GPL text;
- exact source link/source offer required by the licence;
- build configuration;
- statement that FFmpeg is a separate third-party component;
- replacement/relinking information if required by the chosen LGPL distribution method.

Do not claim that a generic link to the newest FFmpeg source represents the exact binary used.

## Gyan builds

If using a Gyan Windows build, record the exact archive/version and the FFmpeg source commit identified for that archive. Preserve the build's own notices.

## Patents

Open-source copyright licences do not guarantee patent clearance in every country. Review distribution risk for enabled codecs where necessary.
