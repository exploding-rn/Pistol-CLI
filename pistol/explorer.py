"""Explicit per-user protocol registration with a deliberately small URI grammar."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import logging
from urllib.parse import parse_qs, urlsplit

from . import pwr, windows_explorer
from .config import PistolError, atomic_json, config_dir

REGISTRY_PATH = r"Software\Classes\pistol"
OWNER = "Pistol developer CLI v1"
ALLOWED = {"doctor", "dev", "pwr"}
logger = logging.getLogger(__name__)


def parse_uri(uri: str, default_path: Path | None = None) -> tuple[str, Path]:
    if len(uri) > 8192 or any(ord(c) < 32 for c in uri) or '"' in uri or re.search(r"%(?![0-9a-fA-F]{2})", uri):
        raise PistolError("Invalid Pistol URI.")
    try:
        parsed = urlsplit(uri)
        if parsed.scheme.lower() != "pistol" or parsed.netloc or parsed.fragment or parsed.path not in ALLOWED:
            raise ValueError("Only pistol:doctor, pistol:dev and pistol:pwr are supported.")
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=1, encoding="utf-8", errors="strict")
        if set(query) - {"path"} or any(len(values) != 1 for values in query.values()):
            raise ValueError("Only one optional path parameter is supported.")
        raw = query.get("path", [str(default_path or Path.home())])[0]
        if not raw or any(ord(c) < 32 for c in raw) or '"' in raw:
            raise ValueError("Invalid path.")
        directory = Path(raw)
        if not directory.is_absolute():
            raise ValueError("URI paths must be absolute.")
        if str(directory).startswith(("\\\\", "//")):
            raise ValueError("URI paths must be local, not network or device paths.")
        return parsed.path, directory.resolve()
    except (ValueError, UnicodeError) as exc:
        raise PistolError(str(exc)) from exc


def _registry():
    if os.name != "nt":
        raise PistolError("Explorer integration requires Windows.")
    import winreg
    return winreg


def install() -> str:
    registry = _registry()
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        raise PistolError(f"Windowless Python launcher is missing: {pythonw}")
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER, REGISTRY_PATH) as key:
            try:
                owner = registry.QueryValueEx(key, "PistolOwner")[0]
            except FileNotFoundError:
                owner = None
            if owner != OWNER:
                raise PistolError("The pistol: protocol belongs to another application; refusing to overwrite it.")
    except FileNotFoundError:
        pass
    command = subprocess.list2cmdline([str(pythonw), "-I", "-m", "pistol.uri_dispatcher"]) + ' "%1"'
    with registry.CreateKeyEx(registry.HKEY_CURRENT_USER, REGISTRY_PATH, 0, registry.KEY_WRITE) as key:
        registry.SetValueEx(key, "", 0, registry.REG_SZ, "URL:Pistol Developer CLI")
        registry.SetValueEx(key, "URL Protocol", 0, registry.REG_SZ, "")
        registry.SetValueEx(key, "PistolOwner", 0, registry.REG_SZ, OWNER)
    with registry.CreateKeyEx(registry.HKEY_CURRENT_USER, REGISTRY_PATH + r"\shell\open\command", 0, registry.KEY_WRITE) as key:
        registry.SetValueEx(key, "", 0, registry.REG_SZ, command)
    atomic_json(config_dir() / "explorer.json", {"pythonw": str(pythonw)})
    return command


def uninstall() -> bool:
    registry = _registry()
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER, REGISTRY_PATH) as key:
            try:
                owner = registry.QueryValueEx(key, "PistolOwner")[0]
            except FileNotFoundError:
                owner = None
            if owner != OWNER:
                raise PistolError("Refusing to remove a protocol registered by another application.")
    except FileNotFoundError:
        return False
    for suffix in (r"\shell\open\command", r"\shell\open", r"\shell", ""):
        registry.DeleteKey(registry.HKEY_CURRENT_USER, REGISTRY_PATH + suffix)
    (config_dir() / "explorer.json").unlink(missing_ok=True)
    return True


def handle(uri: str) -> None:
    action, path = parse_uri(uri, Path.home())
    if action == "pwr" and not urlsplit(uri).query:
        explorer_path = windows_explorer.active_folder()
        if explorer_path is not None:
            path = explorer_path
            logger.info("Using File Explorer folder for pistol:pwr: %s", path)
        else:
            logger.info("File Explorer folder unavailable; using home directory: %s", path)
    if not path.is_dir():
        raise PistolError(f"URI project directory does not exist: {path}")
    if action == "pwr":
        pwr.run(path)
    else:
        # URI data is never interpreted as a shell program or arbitrary subcommand.
        pwr.open_shell(path, elevated=False, command=[sys.executable, "-I", "-m", "pistol", action, "--project", str(path)])
