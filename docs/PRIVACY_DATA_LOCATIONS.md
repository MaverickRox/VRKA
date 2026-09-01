# Local Data Locations & Storage

VRKA operates strictly on your local computer. It does not send data to remote servers or cloud accounts.

---

## Windows Data Locations

| Location | Purpose |
| :--- | :--- |
| `%USERPROFILE%\.vrka\` | Local configuration, user preferences, and task history (`tasks.json`). |
| `%LOCALAPPDATA%\VRKA\runtime\` | Managed engine updates (yt-dlp) and verified FFmpeg runtime components. |
| `%LOCALAPPDATA%\VRKA\browser-session\` | Temporary isolated session storage for Browser Fallback (cleared upon completion). |
| User-Selected Download Directory | Destination folder where completed media files are saved. |

---

## Data Removal
To completely remove all VRKA configuration and history:
1. Uninstall VRKA via Windows Settings or delete the portable directory.
2. Delete the `%USERPROFILE%\.vrka` and `%LOCALAPPDATA%\VRKA` directories from your system.
