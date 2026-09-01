# Release process

## 1. Stabilize

- freeze feature work;
- update tests and docs;
- pin/record dependencies;
- verify third-party licences;
- run secret/path scans.

## 2. Validate

- tests;
- dependency check;
- installer/portable launch;
- media matrix;
- cancellation/shutdown;
- updater/rollback;
- clean user-data behavior;
- uninstall cleanup.

## 3. Package

- build executable;
- build installer;
- build portable ZIP;
- prepare clean source ZIP;
- prepare validation report;
- collect third-party notices.

## 4. Hash

Generate `SHA256SUMS.txt` only after every asset is final.

## 5. Commit and tag

Commit documentation and source. Record the clean commit. Create an annotated immutable tag.

## 6. Draft release

Use accurate notes, known limitations, and exact asset names.

## 7. Verify draft assets

Download every draft asset, hash it, launch it, then publish.

## 8. Post-release

Test signed-out access, installer, portable mode, issue forms, links, and uninstall.

Never replace an existing release binary without changing the version/tag.
