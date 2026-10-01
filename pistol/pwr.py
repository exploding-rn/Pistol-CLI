"""Interactive Windows shells using normal ShellExecuteW and UAC."""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .config import PistolError

logger = logging.getLogger(__name__)


def quote_ps(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def shell_parameters(path: Path, command: list[str] | None = None) -> str:
    # powershell.exe can inherit its own HKCU\Console override. Restore the
    # ordinary console colors and window dimensions for this shell only.
    appearance = (
        "try { $raw = $Host.UI.RawUI; $raw.BackgroundColor = 'Black'; "
        "$raw.ForegroundColor = 'Gray' } catch {}; "
        "try { $packed = [uint32](Get-ItemProperty -LiteralPath 'HKCU:\\Console' "
        "-Name WindowSize -ErrorAction Stop).WindowSize; "
        "$width = [int]($packed -band 0xffff); "
        "$height = [int](($packed -shr 16) -band 0xffff); "
        "if ($width -ge 20 -and $height -ge 10) { "
        "$raw = $Host.UI.RawUI; $buffer = $raw.BufferSize; "
        "if ($buffer.Width -lt $width -or $buffer.Height -lt $height) { "
        "$buffer.Width = [Math]::Max($buffer.Width, $width); "
        "$buffer.Height = [Math]::Max($buffer.Height, $height); "
        "$raw.BufferSize = $buffer }; "
        "$raw.WindowSize = [System.Management.Automation.Host.Size]::new($width, $height) "
        "} } catch {}; "
        "try { Clear-Host } catch {}; "
    )
    script = appearance + "$ErrorActionPreference = 'Stop'; Set-Location -LiteralPath " + quote_ps(str(path))
    if command:
        script += "; & " + " ".join(quote_ps(part) for part in command)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return "-NoLogo -NoProfile -NoExit -EncodedCommand " + encoded


def terminal_executable() -> str | None:
    """Resolve Windows Terminal without launching an unrelated PATH program."""
    found = shutil.which("wt.exe")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    if local:
        alias = Path(local) / "Microsoft" / "WindowsApps" / "wt.exe"
        if alias.is_file():
            return str(alias)
    return None


def terminal_parameters(path: Path, command: list[str] | None = None) -> str:
    # -w new guarantees a separate elevated window; omitting -p selects the
    # user's own default Windows Terminal profile and appearance.
    arguments = ["-w", "new", "-d", str(path)]
    if command:
        powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        arguments.extend([str(powershell), *shell_parameters(path, command).split()])
    return subprocess.list2cmdline(arguments)


def open_shell(path: Path | str | None = None, *, elevated: bool = True, command: list[str] | None = None) -> None:
    if os.name != "nt":
        raise PistolError("pistol pwr and Explorer integration require Windows.")
    directory = Path(path or Path.cwd()).resolve()
    if not directory.is_dir():
        raise PistolError(f"Directory does not exist: {directory}")
    terminal = terminal_executable() if elevated else None
    if terminal:
        executable = terminal
        parameters = terminal_parameters(directory, command)
        display = "Windows Terminal"
    else:
        executable = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe")
        parameters = shell_parameters(directory, command)
        display = "PowerShell"
    message = f"Launching {display}: {Path(executable).name} ({executable}) in {directory}"
    logger.info(message)
    if elevated and sys.stdout is not None:
        print(message)
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    execute = shell.ShellExecuteW
    execute.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
    execute.restype = ctypes.c_ssize_t
    result = execute(None, "runas" if elevated else "open", str(executable), parameters, str(directory), 1)
    if result <= 32:
        raise PistolError(f"Windows could not open {display} (code {result}); UAC may have been cancelled.")


def run(path: Path | str | None = None) -> None:
    open_shell(path, elevated=True)
