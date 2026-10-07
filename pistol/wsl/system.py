import getpass
import os
from pathlib import Path
import platform
import socket
import time

import psutil


def snapshot(config):
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(str(Path.home()))
    return {"distro": config["distro"], "kernel": platform.release(), "hostname": socket.gethostname(),
            "username": getpass.getuser(), "wsl_version": config.get("version"), "cpu": psutil.cpu_percent(),
            "ram_used": memory.used, "ram_total": memory.total, "disk_used": disk.used, "disk_total": disk.total,
            "uptime": int(time.time() - psutil.boot_time()), "home": str(Path.home()),
            "ip_addresses": sorted({a.address for values in psutil.net_if_addrs().values() for a in values
                                    if a.family in (socket.AF_INET, socket.AF_INET6)}),
            "windows_drives": [str(path) for path in Path("/mnt").glob("[a-z]") if os.path.ismount(path)]}
