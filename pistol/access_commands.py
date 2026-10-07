"""Presentation/command orchestration for global config and chamber access."""
import sys

from . import config, lecap
from .output import json_output


def display(value):
    if type(value) is bool:
        return "true" if value else "false"
    if isinstance(value, list):
        return ", ".join(value) or "none"
    return str(value) if value is not None else "none"


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _trust_later() -> None:
    print("\nHTTPS remains enabled, but the CA is not trusted.\n\nTo trust it later:\n  pistol config --trust-ca")


def configure(args):
    from . import certificates

    changes = {key: getattr(args, key) for key in ("chamberlain", "host", "https") if getattr(args, key) is not None}
    was_enabled = config.global_config()["https"] if changes.get("https") is True else False
    ca_preexisted = certificates.ca_exists() if changes.get("https") is True else False
    result = config.update_global(changes) if changes else config.global_config()
    if args.export_ca:
        result["exported_ca"] = str(certificates.export_ca(args.export_ca))

    first_enable = changes.get("https") is True and not was_enabled
    if args.trust_ca:
        if not args.json:
            print("Checking Pistol Local CA in the Windows current-user Root trust store.")
        result["trust"] = certificates.trust_ca()
    elif first_enable and not args.json and certificates.windows_trust_supported() and not certificates.is_ca_trusted():
        if _interactive():
            print("\nHTTPS requires trusting the Pistol Local CA.\n\n"
                  "This will add the Pistol Local CA certificate to your\n"
                  "Windows current-user Trusted Root Certification Authorities.\n")
            print("Proceed? [y/N]: ", end="", flush=True)
            try:
                answer = input().strip().lower()
            except EOFError:
                answer = ""
            if answer in {"y", "yes"}:
                print("✓ Pistol Local CA " + ("ready" if ca_preexisted else "created"))
                try:
                    result["trust"] = certificates.trust_ca()
                except config.PistolError as exc:
                    raise config.PistolError(
                        f"HTTPS was enabled and the Pistol Local CA is present, but current-user trust enrollment failed: {exc} "
                        "Retry with pistol config --trust-ca."
                    ) from exc
                print("✓ Pistol Local CA trusted\n✓ HTTPS enabled")
            else:
                _trust_later()
        else:
            _trust_later()

    result["ca_exists"] = certificates.ca_exists()
    result["ca_trusted"] = certificates.is_ca_trusted() if result["ca_exists"] else False
    result["ca_trust_required"] = bool(result["https"] and not result["ca_trusted"])
    result["https_enabled"] = result["https"]
    result["identity"] = f'{result["chamberlain"]}.{result["host"]}'
    if args.json:
        json_output(result)
    else:
        print("Pistol global configuration")
        for key, value in result.items():
            print(f"  {key:18} {display(value)}")
    return 0


def access(args):
    item = lecap.current(args.project)
    changes = {key: getattr(args, key) for key in lecap.FIELDS if getattr(args, key) is not None}
    if args.reset and changes:
        raise config.PistolError("Use --reset alone, then apply any new overrides.")
    if (args.serve or args.verify or args.dns) and (changes or args.reset):
        raise config.PistolError("Save settings first, then run --serve, --verify or --dns.")
    effective = lecap.update(item, changes, reset=args.reset) if changes or args.reset else lecap.effective(item)
    if args.serve:
        from .lecap_proxy import serve
        result = serve(args.serve)
        if args.json:
            json_output(result)
        else:
            messages = {"started": "✓ LECAP proxy started", "stopped": "✓ LECAP proxy stopped",
                        "already_active": f"LECAP proxy already {'active' if result['active'] else 'running'} (PID {result['pid']})",
                        "already_stopped": "LECAP proxy already stopped"}
            print(messages[result["action"]])
            if result["running"]:
                for key, value in {"PID": result["pid"], "HTTP": result["http_port"],
                                   "HTTPS": result["https_port"], "URL": result["urls"], "Log": result["log"]}.items():
                    print(f"  {key:12} {display(value)}")
            if result["needs_restart"]:
                print("Configuration changed; restart with --serve stop then --serve start.")
            if result.get("reason") and result["running"] and not result["active"]:
                print(result["reason"])
        return 0
    if args.dns:
        from .lecap_dns import HostsBackend, plan
        if args.dns == "plan":
            result = plan(effective)
            if args.json:
                json_output(result)
            else:
                for key, value in result.items():
                    print(f"{key}: {display(value)}")
        else:
            message = HostsBackend().apply(effective, remove=args.dns == "remove")
            json_output({"message": message}) if args.json else print(message)
        return 0
    if args.verify:
        from .lecap_verify import verify
        result = verify(effective)
        if args.json:
            json_output(result)
        else:
            print("LECAP verification")
            for check in result["checks"]:
                print(f"  {'✓' if check['ok'] else '✗'} {check['check']}: {check['detail']}")
            for note in result["notes"]:
                print(note)
            print("Verification passed." if result["passed"] else "Verification failed.")
        return 0 if result["passed"] else 1
    result = lecap.status(item)
    if args.json:
        json_output(result)
    else:
        print(f"LECAP — {item.name}\n\nAccess")
        for key, value in {"URL": effective.urls, "Target": effective.target, "Mode": effective.mode,
                           "HTTPS": effective.values["https"], "Proxy": result["proxy_status"],
                           "DNS": "active" if all(d["ok"] for d in result["dns"]) else "unresolved or unexpected address; use --dns plan"}.items():
            print(f"  {key:18} {display(value)}")
        print("\nConfiguration")
        for key, value in effective.values.items():
            print(f"  {key:18} {display(value):28} {effective.sources[key]}")
        if effective.mode == "direct" and effective.values["https"]:
            print("\nDirect access uses the application's HTTP listener. Use --proxy true for Pistol-managed HTTPS.")
    return 0
