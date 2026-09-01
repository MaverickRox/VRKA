# Privacy

VRKA is designed as a local desktop application. The project does not operate a VRKA account system, media proxy, analytics endpoint, advertising network, or telemetry server.

## Data stored locally

Depending on use, VRKA may store:

- settings;
- output-folder preferences;
- task metadata;
- local History;
- updater state;
- managed yt-dlp runtime files;
- temporary staging files;
- temporary browser-session data.

Known Windows locations include:

- `%USERPROFILE%\.vrka`
- `%LOCALAPPDATA%\VRKA`

Exact paths can vary by build and installation mode.

## Media URLs

Media/page URLs are sent to the websites and infrastructure needed to fulfill the user's request through yt-dlp, FFmpeg, WebView2, and the operating system's network stack.

VRKA does not send those URLs to a VRKA-operated server.

## Cookies and browser sessions

When the user selects browser cookies or uses browser verification:

- cookies/session data are accessed locally;
- relevant context may be passed locally to yt-dlp;
- temporary cookie files may be created for the task;
- sensitive values should be redacted from normal logs;
- temporary task/session material is intended to be deleted during cleanup.

The visited website can still observe normal browser/download requests.

## External services

VRKA or its dependencies may contact external services when the user enables or invokes relevant functionality:

- source media websites;
- official yt-dlp GitHub release endpoints for runtime updates;
- yt-dlp remote challenge-solver components;
- SponsorBlock when enabled;
- browser/runtime update infrastructure managed by Microsoft or other installed runtimes;
- any service explicitly requested through an advanced custom command.

Those services have their own privacy policies.

## Logs and issue reports

Review logs before sharing them publicly. Remove:

- cookies;
- authorization headers;
- signed URLs;
- tokens;
- local usernames/paths;
- private media titles;
- account information.

## Deleting local data

Close VRKA, uninstall it if installed, and remove the VRKA data directories listed above. Also delete any downloads and portable copies manually.

## No sale of data

The open-source VRKA project does not sell user data.
