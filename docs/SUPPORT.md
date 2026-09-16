# NimbleDesk version 1 support contract

NimbleDesk version 1 qualifies the following physical targets. A platform is supported only after
the matching fixture, endurance, installer, update, rollback, and uninstall evidence is recorded
for the release candidate.

| Target ID | Operating system and desktop | Native contract |
| --- | --- | --- |
| `macos-arm64` | The current and previous major macOS releases on Apple silicon | ScreenCaptureKit/Core Graphics capture, Accessibility, CGEvent pointer/keyboard input, application activation, and DaVinci Resolve |
| `windows-11-x64` | Supported Windows 11 x64 releases | Windows Graphics Capture with Desktop Duplication and GDI fallbacks, per-monitor DPI, UI Automation, SendInput, UIPI diagnostics, AppContainer workers, and DaVinci Resolve |
| `ubuntu-24.04-gnome-wayland` | Ubuntu 24.04 LTS, GNOME Wayland | PipeWire ScreenCast, RemoteDesktop portal input, AT-SPI, and DaVinci Resolve |
| `ubuntu-24.04-gnome-x11` | Ubuntu 24.04 LTS, GNOME X11 | X11 capture/input fallback, AT-SPI, and DaVinci Resolve |
| `kde-plasma-6-wayland` | A maintained Linux distribution with KDE Plasma 6 Wayland | PipeWire ScreenCast and RemoteDesktop portal subset plus available AT-SPI controls |

DaVinci Resolve 20.x is the version 1 editor API target. Every supported operating-system family
must retain a passing contract report from its latest qualified Resolve 20.x patch. A new Resolve
major version is unsupported until the import, save, render, reopen, plan-reuse, revision, and
cancellation contract passes and this document is updated.

Media-quality qualification uses at least two separately labeled source corpora totaling eight or
more hours. Each event and ranking report must record the same non-sensitive `corpus_id` and the
full source duration. At least one corpus must be gameplay with labeled kills, multi-kills,
grenade kills, clutches, and narrow survivals. At least one must be speech-led or general footage
so gameplay-specific rules cannot make the aggregate look artificially strong. Private source
names, transcripts, screenshots, and frames stay outside published reports.

Support does not mean every control in every application is accessible. NimbleDesk reports each
available capability and uses application APIs, accessibility, observation-bound visual targets,
or coordinate input in that order. Operations that cross UAC, macOS permission, portal, or
application security boundaries fail explicitly.
