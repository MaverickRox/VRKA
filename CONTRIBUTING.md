# Contributing to VRKA

Contributions are welcome.

## Principles

Changes should preserve:

- local-first operation;
- no VRKA telemetry;
- no DRM circumvention;
- structured process arguments;
- safe staging and filenames;
- cancellation and process-tree cleanup;
- secret redaction;
- accurate Queue/History identity;
- honest errors and limitations;
- source-agnostic behavior rather than fragile site-specific hacks.

## Development setup

See `docs/BUILD_FROM_SOURCE.md`.

## Before submitting

- create a focused branch;
- keep unrelated formatting/refactoring out of the change;
- add or update tests;
- run the existing test suite;
- verify Windows behavior;
- update documentation for visible behavior;
- update third-party notices when dependencies or bundled data change;
- sanitize logs and fixtures.

## Pull requests

Describe:

- the problem;
- the chosen design;
- files changed;
- tests run;
- security/privacy impact;
- cancellation/recovery impact;
- compatibility impact;
- screenshots for UI changes.

## Unacceptable contributions

Do not submit:

- DRM bypass or decryption;
- credential theft or cookie exfiltration;
- hidden telemetry;
- malicious updater behavior;
- copyrighted media/test files;
- unlicensed filter lists or copied code;
- hardcoded adult/hostile-site URLs;
- unsafe shell command construction;
- changes that silently weaken security to make one website work.

By contributing, you confirm that you have the right to license your contribution under the repository licence.
