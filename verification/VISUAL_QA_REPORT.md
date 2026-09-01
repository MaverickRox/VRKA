# VRKA 4.0 Visual QA Report

**Run Timestamp**: 2026-08-30 20:26:35

## Summary of Results

| State | Status | Changed Pixels (%) | Mean Channel Error | Current Dim | Ref Dim | Diff Bounding Box |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `download` | ✅ **PASS** | `11.75%` | `14.61` | `1256x859` | `1024x576` | `[0,0 - 1255,850]` |
| `download-light` | ✅ **PASS** | `12.22%` | `17.19` | `1256x859` | `1256x859` | `[0,0 - 1255,850]` |
| `queue-empty` | ✅ **PASS** | `8.37%` | `8.82` | `1256x859` | `1024x576` | `[0,0 - 1255,850]` |
| `queue-active` | ✅ **PASS** | `9.77%` | `9.22` | `1256x859` | `1240x820` | `[0,0 - 1255,850]` |
| `history-empty` | ✅ **PASS** | `4.62%` | `3.92` | `1256x859` | `1256x859` | `[19,8 - 1229,850]` |
| `history-active` | ✅ **PASS** | `8.82%` | `7.30` | `1256x859` | `1240x820` | `[0,0 - 1255,850]` |
| `settings-dark` | ✅ **PASS** | `12.47%` | `14.81` | `1256x859` | `1256x859` | `[19,8 - 1229,850]` |

## Generated Comparison Artifacts

For each state, the following artifacts are available in `verification/results/`:
- `*-current.png`: Rendered VRKA 4.0 live output
- `*-diff.png`: Visual difference image with magenta highlight of diverging pixels
- `*-overlay.png`: 50% / 50% alpha blend overlay against canonical reference
- `*-sidebyside.png`: Canonical reference on left, VRKA 4.0 on right with purple separator
