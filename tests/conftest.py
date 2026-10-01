"""All test state lives in a unique Pistol runtime temp directory."""
import logging
from pathlib import Path
import uuid

import pytest

from pistol.config import data_dir
from pistol.utils.files import safe_remove

_owned_base = None


def pytest_configure(config):
    global _owned_base
    if not config.option.basetemp:
        _owned_base = data_dir() / "temp" / ("pytest-" + uuid.uuid4().hex)
        _owned_base.parent.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = str(_owned_base)


def pytest_unconfigure(config):
    if _owned_base is not None and _owned_base.exists():
        # pytest creates these convenience symlinks inside its own unique base.
        # Unlink the links themselves; never traverse their targets.
        for path in _owned_base.iterdir():
            if path.name.endswith("current") and path.is_symlink():
                path.unlink()
        safe_remove(_owned_base, _owned_base.parent)


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    yield
    logger = logging.getLogger("pistol")
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "Project with spaces ü"
    root.mkdir()
    (root / "main.py").write_text("print('hello from project')\n", encoding="utf-8")
    return root
