"""Name resolution backend. Hosts edits are explicit and never configure a router."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import socket
from typing import Protocol

from .config import PistolError, state_lock


class DNSBackend(Protocol):
    def apply(self, access, *, remove: bool = False) -> str: ...


def inspect(access) -> list[dict]:
    expected = {"127.0.0.1"}
    if access.values["lan"] and access.values["lan_address"]:
        expected.add(access.values["lan_address"])
    result = []
    for name in access.domains:
        try:
            addresses = sorted({entry[4][0] for entry in socket.getaddrinfo(name, None, type=socket.SOCK_STREAM)})
            result.append({"name": name, "addresses": addresses, "ok": bool(addresses) and set(addresses) <= expected,
                           "expected": sorted(expected)})
        except OSError as exc:
            result.append({"name": name, "addresses": [], "ok": False, "expected": sorted(expected), "error": str(exc)})
    return result


def is_admin() -> bool:
    return bool(ctypes.windll.shell32.IsUserAnAdmin()) if os.name == "nt" else os.geteuid() == 0


class HostsBackend:
    def __init__(self, path: Path | None = None):
        self.path = path or (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/drivers/etc/hosts" if os.name == "nt" else Path("/etc/hosts"))

    def apply(self, access, *, remove: bool = False) -> str:
        if not is_admin():
            raise PistolError(f"Administrator privileges required to {'remove' if remove else 'add'} Pistol entries in {self.path}. Run this explicit --dns operation from an elevated terminal. No firewall or adapter settings will change.")
        marker = f"# Pistol LECAP {access.chamber}"
        with state_lock():
            original = self.path.read_bytes()
            # Preserve the encoding and bytes of unrelated host entries.
            lines = original.splitlines(keepends=True)
            kept = [line for line in lines if not line.rstrip().endswith(marker.encode("ascii"))]
            if not remove:
                wanted = set(access.domains)
                for line in kept:
                    fields = line.split(b"#", 1)[0].decode("ascii", errors="ignore").lower().split()
                    if len(fields) > 1 and wanted.intersection(fields[1:]):
                        raise PistolError("A LECAP name already has an unmanaged or other chamber hosts entry. Resolve that conflict before installing DNS.")
            content = b"".join(kept)
            if not remove:
                if content and not content.endswith(b"\n"):
                    content += b"\r\n"
                content += ("127.0.0.1 " + " ".join(access.domains) + " " + marker + "\r\n").encode("ascii")
            # Exclusive backup; preserve original ACL by updating the existing hosts file.
            backup = self.path.with_name(self.path.name + ".pistol-backup")
            if not backup.exists():
                with backup.open("xb") as stream:
                    stream.write(original)
            if self.path.read_bytes() != original:
                raise PistolError("Hosts file changed during this operation; retry.")
            try:
                with self.path.open("r+b") as stream:
                    stream.write(content)
                    stream.truncate()
                    stream.flush()
                    os.fsync(stream.fileno())
            except OSError as exc:
                raise PistolError(f"Hosts update failed. Original backup: {backup}. {exc}") from exc
        return f"{'Removed' if remove else 'Installed'} {access.chamber} hosts entries in {self.path}. Backup: {backup}."


def plan(access) -> dict:
    return {"backend": "hosts", "local_records": [f"127.0.0.1 {name}" for name in access.domains],
            "lan_records": [f'{access.values["lan_address"]} {name}' for name in access.domains] if access.values["lan"] and access.values["lan_address"] else [],
            "instructions": "Use --dns install explicitly as administrator for this computer. For LAN clients, manually add the LAN records to trusted devices or your private DNS server. No public DNS, router, or firewall changes are made."}
