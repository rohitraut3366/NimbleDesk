# Distribution

NimbleDesk has one bundled executable on macOS, Windows, and Linux. It exposes the same component commands as the Python installation:

```text
nimbledesk start
nimbledesk daemon
nimbledesk mcp
nimbledesk studio
nimbledesk create ...
nimbledesk revise ...
nimbledesk highlights ...
nimbledesk photos ...
nimbledesk music-index ...
nimbledesk approve ...
nimbledesk smoke ...
nimbledesk service install
nimbledesk service uninstall
nimbledesk diagnostics --output nimbledesk-diagnostics.zip
```

`nimbledesk start` launches the local authenticated daemon, waits for its fresh connection file, runs Studio on loopback, and terminates the daemon when Studio exits. Model clients should run `nimbledesk mcp` and use the same connection file.

`nimbledesk service install` configures the daemon for the current user only: a LaunchAgent on macOS, a systemd user unit on Linux, or a limited-privilege logon task on Windows. It starts the service immediately and restarts it after failures or later logins. `nimbledesk service uninstall` stops and removes that registration without deleting projects, configuration, or audit data.

`nimbledesk diagnostics` creates a privacy-safe ZIP containing platform and bundled-runtime versions,
optional tool availability, loopback daemon health and backend capabilities, connection-file
permission checks, hash-chain health and aggregate action statuses, and aggregate job states. It
never includes connection secrets, paths, screenshots, media, transcripts, typed text, or raw logs.

Build a platform archive on that platform:

```bash
uv sync --extra bundle --locked
uv run --extra bundle python packaging/build_bundle.py
```

The archive is written under `artifacts/`. CI performs this build independently on macOS, Windows, and Linux and uploads each archive. The executable includes NimbleDesk and its Python runtime. FFmpeg/FFprobe, optional Whisper/Tesseract, DaVinci Resolve, and operating-system accessibility or portal components remain host dependencies and are diagnosed at runtime.

The current archives are unsigned engineering artifacts. Public release still requires platform signing identities, Apple notarization, Windows signing, installer wrappers, update signatures, rollback metadata, and clean-machine install/upgrade/uninstall validation. Signing credentials must be supplied through the release environment; they are never stored in the repository.
