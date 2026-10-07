import re
from .services import command


async def listing():
    try:
        output = await command("ss", "-H", "-lntup")
    except FileNotFoundError:
        return {"ports": [], "message": "Install iproute2 in the distro to inspect listeners (ss)."}
    rows = []
    for line in output.splitlines():
        parts = line.split(None, 6)
        if len(parts) < 6:
            continue
        address, _, port = parts[4].rpartition(":")
        if not port.isdigit():
            continue
        details = parts[6] if len(parts) > 6 else ""
        process = re.search(r'\("([^"]+)".*?pid=(\d+)', details)
        rows.append({"port": int(port), "protocol": parts[0], "address": address.strip("[]"),
                     "process": process[1] if process else "unavailable", "pid": int(process[2]) if process else None,
                     "forwarding": "Candidate for Windows localhost" if parts[0] == "tcp" and address.strip("[]") in
                     {"127.0.0.1", "0.0.0.0", "::", "*", "::1"} else "Unknown"})
    return {"ports": rows, "message": "Forwarding candidates are inferred from bind addresses; Windows reachability is not verified. Other users' process details may be hidden."}
