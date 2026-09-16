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
nimbledesk service uninstall --purge-data
nimbledesk update status
nimbledesk update install release.json --trusted-keys release-keys.json --artifact macos-arm64
nimbledesk update rollback
nimbledesk diagnostics --output nimbledesk-diagnostics.zip
```

`nimbledesk start` launches the local authenticated daemon, waits for its fresh connection file, runs Studio on loopback, and terminates the daemon when Studio exits. Model clients should run `nimbledesk mcp` and use the same connection file.

`nimbledesk service install` configures the daemon for the current user only: a LaunchAgent on macOS, a systemd user unit on Linux, or a limited-privilege logon task on Windows. It starts the service immediately and restarts it after failures or later logins. `nimbledesk service uninstall` stops and removes that registration without deleting projects, configuration, or audit data. Pass `--purge-data` only when local projects, configuration, profiles, logs, backups, and audit data should also be deleted.

`nimbledesk diagnostics` creates a privacy-safe ZIP containing platform and bundled-runtime versions,
optional tool availability, loopback daemon health and backend capabilities, connection-file
permission checks, hash-chain health and aggregate action statuses, and aggregate job states. It
never includes connection secrets, paths, screenshots, media, transcripts, typed text, or raw logs.

Build a platform archive on that platform:

```bash
uv sync --extra bundle --extra native --extra speech --locked
uv run --extra bundle --extra native --extra speech python packaging/build_bundle.py
uv run --extra bundle --extra native --extra speech python packaging/build_installer.py --version 0.1.0
```

The builders write an archive and a native installer under `artifacts/`: a macOS application package, per-user Windows MSI, or Debian package. CI builds, installs, executes, and uninstalls each artifact on a clean hosted OS image. The executable includes NimbleDesk, its Python runtime, and the faster-whisper transcription runtime. Whisper model weights download on first use. FFmpeg/FFprobe, optional Tesseract, DaVinci Resolve, accessibility services, XDG Desktop Portal, and GStreamer PipeWire support remain host dependencies and are diagnosed at runtime.

Every bundle build executes its embedded adapter and DaVinci worker routes before creating the
archive or installer. The adapter route must complete a structured fixture invocation. The
DaVinci route must parse its internal seven-argument protocol and write a bounded structured error
for a deliberately missing plan. A frozen executable that still tries to invoke `python -m` fails
packaging. Windows builds additionally run the malicious-adapter AppContainer contract; packaging
fails unless undeclared reads and child processes are denied, declared reads and writes work, the
512 MiB limit is enforced, and the internet-client capability exactly follows the manifest.

## Signed updates and rollback

Release metadata is a canonical JSON manifest signed with Ed25519. The signature covers the version, channel, publish time, minimum compatible version, and the HTTPS URL, byte count, and SHA-256 digest of every artifact. The updater rejects unknown keys, altered metadata, insecure network URLs, changed downloads, oversized downloads, archive path traversal, and archive links.

Create a release document with an offline Ed25519 PEM private key:

```bash
uv run python packaging/sign_release.py \
  --version 0.2.0 \
  --key-id production-2026 \
  --private-key /secure/release-ed25519.pem \
  --artifact macos-arm64=artifacts/nimbledesk-darwin-arm64.tar.gz=https://downloads.example/nimbledesk-darwin-arm64.tar.gz \
  --output artifacts/release.json
```

The trusted-key file is a JSON object from key ID to the base64 encoding of the raw 32-byte Ed25519 public key. Keep the private key in the release secret store; never put it in source control, build logs, installers, or diagnostic archives.

An accepted update archive is downloaded to a temporary file, verified before extraction, and installed into a new version directory. NimbleDesk atomically records the current and previous versions. The original package-manager executable acts as a stable launcher and hands normal commands to the selected managed version; update and rollback commands always remain available through that stable launcher. Before the switch NimbleDesk snapshots mutable configuration, state, and profiles under `~/.nimbledesk/backups`. `nimbledesk update rollback` switches back to the previous executable and restores that snapshot. Source media and generated projects are not copied or changed by migrations.

## Platform signing

Engineering builds are deliberately unsigned. Public release jobs must provide signing identities through their secret environment and must fail when credentials are absent.

- macOS uses `NIMBLEDESK_APPLE_APPLICATION_IDENTITY` for hardened-runtime app signing, `NIMBLEDESK_APPLE_INSTALLER_IDENTITY` for the package, and `NIMBLEDESK_APPLE_NOTARY_PROFILE` for `notarytool` submission and stapling.
- Windows uses `NIMBLEDESK_WINDOWS_SIGNING_CERTIFICATE` and `NIMBLEDESK_WINDOWS_SIGNING_PASSWORD`; the MSI is timestamped after WiX builds it.
- Linux distribution trust comes from the signed release manifest and the repository metadata when publishing the Debian package through an APT repository.

The macOS installer owns `/Applications/NimbleDesk.app` and its `/usr/local/bin/nimbledesk` link. Windows Installer owns `%LOCALAPPDATA%\NimbleDesk` and its per-user PATH entry. `dpkg` owns `/usr/bin/nimbledesk` on Debian systems. Uninstall the per-user daemon first, then use the platform package manager. User data remains unless `nimbledesk service uninstall --purge-data` is requested.

Public certificates and physical clean-machine results still have to be supplied by the release owner; repository CI cannot manufacture external signing identities or substitute hosted VMs for physical permission dialogs.
