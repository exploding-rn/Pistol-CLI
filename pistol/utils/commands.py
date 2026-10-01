"""Parse command strings into argv without a shell."""
import ctypes
from ctypes import wintypes
import os
import shlex

from pistol.config import PistolError


def split_command(command: str) -> list[str]:
    if not command.strip() or "\0" in command:
        raise PistolError("Command must be non-empty and contain no NUL characters.")
    if os.name != "nt":
        try:
            return shlex.split(command)
        except ValueError as exc:
            raise PistolError(str(exc)) from exc
    # Prefix an executable so CommandLineToArgvW's special argv[0] rules do not
    # affect the first user argument (e.g. a quoted -m entrypoint).
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    parse = shell.CommandLineToArgvW
    parse.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    parse.restype = ctypes.POINTER(wintypes.LPWSTR)
    count = ctypes.c_int()
    arguments = parse("pistol.exe " + command, ctypes.byref(count))
    if not arguments:
        raise ctypes.WinError(ctypes.get_last_error())
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel.LocalFree.restype = wintypes.HLOCAL
    try:
        return [arguments[i] for i in range(1, count.value)]
    finally:
        kernel.LocalFree(ctypes.cast(arguments, wintypes.HLOCAL))

