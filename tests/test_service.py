import plistlib
from pathlib import Path

import pytest

from nimbledesk.service import install_service, uninstall_service


@pytest.mark.parametrize("system", ["Darwin", "Linux", "Windows"])
def test_service_install_and_uninstall_are_per_user(tmp_path: Path, system: str) -> None:
    executable = tmp_path / "install" / ("nimbledesk.exe" if system == "Windows" else "nimbledesk")
    executable.parent.mkdir()
    executable.write_bytes(b"fixture")

    service_path = install_service(
        executable,
        system=system,
        home=tmp_path,
        activate=False,
    )

    assert service_path.is_file()
    assert tmp_path in service_path.parents
    if system == "Darwin":
        payload = plistlib.loads(service_path.read_bytes())
        assert payload["ProgramArguments"] == [str(executable), "daemon"]
        assert payload["RunAtLoad"] is True
    elif system == "Linux":
        content = service_path.read_text(encoding="utf-8")
        assert f'ExecStart="{executable}" daemon' in content
        assert "WantedBy=default.target" in content
    else:
        assert service_path.read_text(encoding="utf-8") == str(executable)

    removed = uninstall_service(system=system, home=tmp_path, deactivate=False)

    assert removed == service_path
    assert not service_path.exists()
