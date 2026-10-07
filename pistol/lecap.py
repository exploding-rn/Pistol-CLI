"""Chamber access overrides and their effective configuration. No network mutations."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import ipaddress
from pathlib import Path

from . import chamber
from .config import PistolError, atomic_json, global_config, hostname, read_json, state_lock
from .ports import validate_port

FIELDS = ("enabled", "port", "chamberlain", "host", "subdomain", "domain", "https", "lan", "lan_address", "proxy", "redirect")
IDENTITY = {"chamberlain", "host", "subdomain", "domain"}


def current(project: Path | None = None) -> chamber.Chamber:
    directory = Path(project or Path.cwd()).resolve()
    candidates = [c for c in chamber.list_chambers() if directory.is_relative_to(Path(c.project).resolve())]
    if candidates:
        depth = max(len(Path(c.project).parts) for c in candidates)
        candidates = [c for c in candidates if len(Path(c.project).parts) == depth]
        from .config import settings
        active = settings().get("active_chamber")
        selected = next((c for c in candidates if c.name == active), None)
        if selected or len(candidates) == 1:
            return selected or candidates[0]
    raise PistolError("This folder has no unambiguous Pistol chamber. Use pistol chamber --name NAME, or pistol chamber --switch NAME for this project.")


def private_address(value: str) -> str:
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise PistolError("LAN address must be a private IPv4 interface address.") from exc
    if not any(address in ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
        raise PistolError("LAN address must be in 10/8, 172.16/12 or 192.168/16; public and wildcard bindings are forbidden.")
    return str(address)


def validate(values: dict) -> dict:
    if not isinstance(values, dict):
        raise PistolError("LECAP overrides must be a JSON object; file left intact.")
    result = dict(values)
    if set(result) - set(FIELDS):
        raise PistolError("Unknown LECAP settings; file left intact.")
    for key, value in result.items():
        if key in {"enabled", "https", "lan", "proxy", "redirect"}:
            if type(value) is not bool:
                raise PistolError(f"{key} must be true or false.")
        elif key == "port":
            if type(value) is not int:
                raise PistolError("Target port must be an integer.")
            validate_port(value)
        elif key == "subdomain":
            if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
                raise PistolError("Subdomains must be a nonempty list of DNS names.")
            result[key] = list(dict.fromkeys(hostname(v) for v in value))
        elif key == "lan_address":
            result[key] = private_address(value)
        else:
            result[key] = hostname(value, label=key in {"host", "chamberlain"})
    return result


def overrides(item: chamber.Chamber) -> dict:
    payload = read_json(item.directory / "lecap.json", {})
    if payload and payload.get("schema") != 1:
        raise PistolError("Unsupported LECAP schema; file left intact.")
    return validate(payload.get("overrides", {}))


@dataclass
class Access:
    chamber: str
    values: dict
    sources: dict
    domains: list[str]
    urls: list[str]
    target: str
    mode: str


def effective(item: chamber.Chamber, local: dict | None = None) -> Access:
    local = overrides(item) if local is None else validate(local)
    values = {"enabled": True, "port": item.port, "lan": False, "lan_address": None,
              "subdomain": [], "domain": None, "proxy": bool(IDENTITY & local.keys()), "redirect": True}
    sources = {key: "chamber/default" for key in values}
    values.update(global_config())
    sources.update({key: "global" for key in ("host", "chamberlain", "https")})
    values.update(local)
    sources.update({key: "local" for key in local})
    base = values["domain"] or f'{values["chamberlain"]}.{values["host"]}'
    domains = [hostname(f"{sub}.{base}") for sub in values["subdomain"]] or [hostname(base)]
    mode = "proxy" if values["proxy"] else "direct"
    # Direct targets speak HTTP; an HTTPS preference does not turn an application into a TLS server.
    scheme = "https" if mode == "proxy" and values["https"] else "http"
    urls = [f'{scheme}://{name}' + (f':{values["port"]}' if mode == "direct" else "") for name in domains]
    return Access(item.name, values, sources, domains, urls, f'127.0.0.1:{values["port"]}', mode)


def update(item: chamber.Chamber, changes: dict, *, reset: bool = False) -> Access:
    with state_lock():
        local = {} if reset else overrides(item)
        local.update(validate(changes))
        result = effective(item, local)
        atomic_json(item.directory / "lecap.json", {"schema": 1, "overrides": local})
    return result


def routes() -> list[Access]:
    result = []
    claimed = set()
    for item in chamber.list_chambers():
        access = effective(item)
        if not access.values["enabled"] or access.mode != "proxy":
            continue
        for name in access.domains:
            if name in claimed:
                raise PistolError(f"Duplicate LECAP domain {name}; set unique subdomains or disable one proxy.")
            claimed.add(name)
        if access.values["port"] in {80, 443}:
            raise PistolError(f"{access.chamber}: target port cannot be a proxy listener (80/443).")
        result.append(access)
    return result


def status(item: chamber.Chamber) -> dict:
    from .lecap_dns import inspect
    from .lecap_proxy import probe
    from .lecap_runtime import status as runtime_status
    access = effective(item)
    result = asdict(access)
    result["dns"] = inspect(access)
    result["proxy_runtime"] = runtime_status()
    result["proxy_status"] = probe(access, result["proxy_runtime"])
    return result
