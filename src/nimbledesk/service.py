from __future__ import annotations

import argparse
import platform
import plistlib
import subprocess
import sys
from pathlib import Path


def install_service(
    executable: Path,
    *,
    system: str | None = None,
    home: Path | None = None,
    activate: bool = True,
) -> Path:
    selected_system = system or platform.system()
    selected_home = home or Path.home()
    executable = executable.resolve()
    if selected_system == "Darwin":
        path = selected_home / "Library" / "LaunchAgents" / "io.nimbledesk.daemon.plist"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "Label": "io.nimbledesk.daemon",
            "ProgramArguments": [str(executable), "daemon"],
            "RunAtLoad": True,
            "KeepAlive": True,
            "ProcessType": "Interactive",
            "StandardOutPath": str(selected_home / ".nimbledesk" / "logs" / "daemon.log"),
            "StandardErrorPath": str(
                selected_home / ".nimbledesk" / "logs" / "daemon-error.log"
            ),
        }
        (selected_home / ".nimbledesk" / "logs").mkdir(parents=True, exist_ok=True)
        path.write_bytes(plistlib.dumps(payload, sort_keys=True))
        if activate:
            _run(["launchctl", "load", "-w", str(path)])
        return path
    if selected_system == "Linux":
        path = selected_home / ".config" / "systemd" / "user" / "nimbledesk.service"
        path.parent.mkdir(parents=True, exist_ok=True)
        escaped_executable = str(executable).replace("%", "%%")
        path.write_text(
            "\n".join(
                (
                    "[Unit]",
                    "Description=NimbleDesk local desktop automation daemon",
                    "After=graphical-session.target",
                    "",
                    "[Service]",
                    f'ExecStart="{escaped_executable}" daemon',
                    "Restart=on-failure",
                    "RestartSec=2",
                    "",
                    "[Install]",
                    "WantedBy=default.target",
                    "",
                )
            ),
            encoding="utf-8",
        )
        if activate:
            _run(["systemctl", "--user", "daemon-reload"])
            _run(["systemctl", "--user", "enable", "--now", "nimbledesk.service"])
        return path
    if selected_system == "Windows":
        marker = selected_home / ".nimbledesk" / "service" / "NimbleDesk.task"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(executable), encoding="utf-8")
        if activate:
            _run(
                [
                    "schtasks",
                    "/Create",
                    "/TN",
                    "NimbleDesk Daemon",
                    "/SC",
                    "ONLOGON",
                    "/TR",
                    f'"{executable}" daemon',
                    "/RL",
                    "LIMITED",
                    "/F",
                ]
            )
            _run(["schtasks", "/Run", "/TN", "NimbleDesk Daemon"])
        return marker
    raise ValueError(f"unsupported service platform: {selected_system}")


def uninstall_service(
    *,
    system: str | None = None,
    home: Path | None = None,
    deactivate: bool = True,
) -> Path:
    selected_system = system or platform.system()
    selected_home = home or Path.home()
    if selected_system == "Darwin":
        path = selected_home / "Library" / "LaunchAgents" / "io.nimbledesk.daemon.plist"
        if deactivate and path.exists():
            _run(["launchctl", "unload", "-w", str(path)], check=False)
    elif selected_system == "Linux":
        path = selected_home / ".config" / "systemd" / "user" / "nimbledesk.service"
        if deactivate:
            _run(["systemctl", "--user", "disable", "--now", "nimbledesk.service"], check=False)
            _run(["systemctl", "--user", "daemon-reload"], check=False)
    elif selected_system == "Windows":
        path = selected_home / ".nimbledesk" / "service" / "NimbleDesk.task"
        if deactivate:
            _run(["schtasks", "/Delete", "/TN", "NimbleDesk Daemon", "/F"], check=False)
    else:
        raise ValueError(f"unsupported service platform: {selected_system}")
    path.unlink(missing_ok=True)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the per-user NimbleDesk daemon service")
    parser.add_argument("action", choices=("install", "uninstall"))
    arguments = parser.parse_args()
    if arguments.action == "install":
        path = install_service(_installed_executable())
        print(f"Installed per-user service: {path}")
    else:
        path = uninstall_service()
        print(f"Removed per-user service: {path}")


def _run(command: list[str], *, check: bool = True) -> None:
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if check and completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(message or f"service command failed: {command[0]}")


def _installed_executable() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    console_script = Path(sys.executable).parent / (
        "nimbledesk.exe" if platform.system() == "Windows" else "nimbledesk"
    )
    if not console_script.is_file():
        raise RuntimeError("install NimbleDesk before registering its per-user service")
    return console_script


if __name__ == "__main__":
    main()
