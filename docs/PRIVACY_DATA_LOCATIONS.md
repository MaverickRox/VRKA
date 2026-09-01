# Local data locations

Build008 can use the following Windows locations:

- `%USERPROFILE%\.vrka` — settings/history and related local state
- `%LOCALAPPDATA%\VRKA\runtime` — managed yt-dlp runtime
- `%LOCALAPPDATA%\VRKA\browser-session` — temporary browser verification data
- selected output directory — user downloads
- temporary staging directories — partial downloads and processing files

Before filing a bug or publishing a diagnostic archive, inspect these locations and remove cookies, tokens, private titles, and personal paths.

Uninstalling the application may not remove user-generated downloads or every local settings/history file. Delete them manually when complete removal is desired.
