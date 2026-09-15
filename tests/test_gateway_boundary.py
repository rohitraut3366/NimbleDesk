from pathlib import Path


def test_gateway_does_not_import_an_os_backend() -> None:
    gateway_source = Path("src/nimbledesk/gateway/server.py").read_text()

    assert "nimbledesk.backends" not in gateway_source
    assert "pyautogui" not in gateway_source
