"""Best-effort lookup of the folder in the invoking File Explorer window."""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import logging
import os
from pathlib import Path
import subprocess

logger = logging.getLogger(__name__)


def _foreground_hwnd() -> int:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    get_foreground = user32.GetForegroundWindow
    get_foreground.argtypes = []
    get_foreground.restype = wintypes.HWND
    return int(get_foreground() or 0)


def _top_level_hwnds() -> list[int]:
    """Return visible top-level window handles in foreground order."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    handles: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    is_visible = user32.IsWindowVisible
    is_visible.argtypes = [wintypes.HWND]
    is_visible.restype = wintypes.BOOL

    @callback_type
    def collect(hwnd, _parameter):
        if is_visible(hwnd):
            handles.append(int(hwnd))
        return True

    enumerate_windows = user32.EnumWindows
    enumerate_windows.argtypes = [callback_type, wintypes.LPARAM]
    enumerate_windows.restype = wintypes.BOOL
    if not enumerate_windows(collect, 0):
        return []
    return handles


def _shell_window_folder(hwnd: int, window_order: list[int]) -> str | None:
    # PowerShell's built-in Shell.Application COM support avoids a pywin32
    # dependency. The helper has no console and exits after one bounded query.
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    if not powershell.is_file():
        return None
    ordered_handles = ",".join(str(value) for value in window_order)
    script = f"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$folders = @()
foreach ($window in (New-Object -ComObject Shell.Application).Windows()) {{
    try {{
        if ([IO.Path]::GetFileName([string]$window.FullName) -ine 'explorer.exe') {{ continue }}
        $folder = [string]$window.Document.Folder.Self.Path
        if ($folder) {{
            $folders += [pscustomobject]@{{ Hwnd = [long]$window.HWND; Path = $folder }}
        }}
    }} catch {{ }}
}}
$chosen = @($folders | Where-Object {{ $_.Hwnd -eq {hwnd} }}) | Select-Object -First 1
if (-not $chosen) {{
    foreach ($handle in @({ordered_handles})) {{
        $chosen = @($folders | Where-Object {{ $_.Hwnd -eq $handle }}) | Select-Object -First 1
        if ($chosen) {{ break }}
    }}
}}
if (-not $chosen -and $folders.Count -eq 1) {{ $chosen = $folders[0] }}
if ($chosen) {{ [Console]::Out.Write($chosen.Path) }}
"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    result = subprocess.run(
        [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=5,
        creationflags=subprocess.CREATE_NO_WINDOW,
        startupinfo=startup,
        check=False,
    )
    if result.returncode:
        logger.debug("Explorer folder lookup failed: %s", result.stderr.strip())
        return None
    return result.stdout.strip() or None


def active_folder() -> Path | None:
    """Return a local Explorer folder, or None if the source is ambiguous."""
    if os.name != "nt":
        return None
    try:
        raw = _shell_window_folder(_foreground_hwnd(), _top_level_hwnds())
        if not raw or any(ord(char) < 32 for char in raw):
            return None
        path = Path(raw)
        if not path.is_absolute() or str(path).startswith(("\\\\", "//")) or not path.is_dir():
            return None
        return path.resolve()
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        logger.debug("Explorer folder lookup unavailable: %s", exc)
        return None
