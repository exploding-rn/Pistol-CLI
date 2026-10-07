import asyncio
from pathlib import Path
import re


async def command(*argv):
    process = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 12)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise ValueError(stderr.decode(errors="replace").strip()[:800] or "Linux command failed.")
    return stdout.decode(errors="replace")


async def listing(scope="system"):
    if not Path("/run/systemd/system").exists():
        return {"services": [], "message": "systemd is not running in this distro. Manage services through the terminal, or enable systemd in WSL."}
    try:
        text = await command("systemctl", *scope_args(scope), "list-units", "--type=service", "--all", "--no-legend", "--plain", "--no-pager")
        rows = []
        for line in text.splitlines():
            parts = line.split(None, 4)
            if len(parts) >= 4:
                rows.append({"service": parts[0], "state": parts[2] + "/" + parts[3],
                             "description": parts[4] if len(parts) > 4 else ""})
        return {"services": rows, "message": "Actions use your Linux permissions. Administrative authentication is available in Terminal."}
    except (OSError, ValueError) as exc:
        return {"services": [], "message": str(exc)}


def scope_args(scope):
    if scope not in {"user", "system"}:
        raise ValueError("Service scope must be user or system.")
    return ["--user"] if scope == "user" else ["--system"]


async def action(service, operation, scope):
    if operation not in {"start", "stop", "restart"} or not isinstance(service, str) or not re.fullmatch(r"[a-zA-Z0-9_@.:-]+\.service", service):
        raise ValueError("Invalid service action.")
    await command("systemctl", *scope_args(scope), "--no-ask-password", operation, "--", service)
