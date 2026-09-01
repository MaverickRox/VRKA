# Security Policy

## Supported Versions

| Version | Supported |
| :--- | :--- |
| 4.0.0 (Build 016) | :white_check_mark: |
| < 4.0.0 | :x: |

---

## Reporting a Vulnerability

We take the security and privacy of VRKA seriously. If you discover a potential vulnerability, please report it responsibly using GitHub Private Vulnerability Reporting:

1. Navigate to the **Security** tab of this repository: [https://github.com/MaverickRox/VRKA/security/advisories/new](https://github.com/MaverickRox/VRKA/security/advisories/new)
2. Click **Report a vulnerability**.
3. Provide a clear explanation of the issue, steps to reproduce, and any relevant logs (with personal data redacted).

Alternatively, you may contact the maintainer at `mavroxx@protonmail.com`.

Please allow up to 48 hours for initial triage before public disclosure.

---

## Security Architecture Highlights

- **Redacted Logging**: Sensitive values, access tokens, and cookies are automatically redacted from activity logs and persistent records.
- **Task-Scoped Subprocesses**: All helper and browser processes are registered with the task context and terminated upon task completion or cancellation.
- **DRM Respect**: VRKA intentionally terminates extraction on DRM-protected media streams.
- **Signed Runtime Updates**: The runtime manager validates SHA-256 signatures before activating updated yt-dlp binaries.
