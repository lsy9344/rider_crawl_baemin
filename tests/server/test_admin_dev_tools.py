from pathlib import Path


def test_dev_admin_ui_requires_explicit_local_bypass_flag() -> None:
    source = Path("scripts/dev_admin_ui.py").read_text(encoding="utf-8")

    assert "DEV_ADMIN_AUTH_BYPASS" in source
    assert "APP_ENV" in source
    assert "production" in source
    assert "127.0.0.1" in source
