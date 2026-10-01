"""The single Pistol command. Parsing and output are kept out of core logic."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import sys

from . import __version__, chamber, detective, dev, doctor, env, explorer, fire, magazine, medic, ports, pwr, routes, shrimp, vent, watch, wtf
from .config import PistolError, data_dir, read_json, state_lock
from .output import health, json_output, pairs
from .utils.commands import split_command
from .utils.project import runtime_from_entrypoint, validate_entrypoint


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pistol", description="Offline developer tools and project runtimes. No accounts, daemon or AI.", allow_abbrev=False)
    parser.add_argument("--version", action="version", version=f"Pistol {__version__}")
    parser.add_argument("--json", action="store_true", help="structured output for inspection commands")
    commands = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    common.add_argument("--project", type=Path, help="project directory (default: nearest project root)")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    def sub(name, help_text):
        return commands.add_parser(name, help=help_text, description=help_text, parents=[common], allow_abbrev=False)

    command = sub("chamber", "Create and manage isolated project runtimes")
    actions = command.add_mutually_exclusive_group(required=True)
    actions.add_argument("--name", help="create a chamber")
    actions.add_argument("--list", action="store_true")
    actions.add_argument("--switch", metavar="NAME")
    actions.add_argument("--delete", metavar="NAME")
    actions.add_argument("--reset", metavar="NAME")
    actions.add_argument("--configure", metavar="NAME", help="edit entrypoint, port or environment")
    command.add_argument("--port", type=int)
    command.add_argument("--runtime", choices=["python", "node", "rust", "command"])
    command.add_argument("--entrypoint", help="argv string; use --entrypoint='-m module ...' for modules; {port} expands")
    command.add_argument("--set", dest="variables", action="append", default=[], metavar="KEY=VALUE")
    command.add_argument("--unset", action="append", default=[], metavar="KEY")
    command.add_argument("--yes", action="store_true", help="confirm reset or deletion")

    command = sub("mag", "Group chambers using localhost ports")
    command.add_argument("magazine", nargs="?")
    actions = command.add_mutually_exclusive_group()
    actions.add_argument("--name", help="create a magazine")
    actions.add_argument("--list", action="store_true")
    actions.add_argument("--add", metavar="CHAMBER")
    actions.add_argument("--remove", metavar="CHAMBER")

    command = sub("fire", "Run an entrypoint in the foreground; Ctrl+C stops it")
    command.add_argument("name", nargs="?", help="chamber name")
    command.add_argument("--mag", metavar="NAME")
    command.add_argument("--stop", action="store_true", help="stop the tracked process or magazine")

    for name, description in (("doctor", "Check project health without executing source"), ("wtf", "Diagnose failures using logs and deterministic evidence")):
        command = sub(name, description)
        command.add_argument("--chamber", help="inspect a specific chamber")

    command = sub("medic", "Review and apply conservative repairs")
    command.add_argument("--chamber")
    command.add_argument("--yes", action="store_true", help="approve listed repairs")
    command.add_argument("--install-deps", action="store_true", help="explicitly permit pip installation (may use network)")
    command.add_argument("--dry-run", action="store_true")

    sub("dev", "Summarize the current developer project")
    command = sub("env", "Inspect environment names and sources; values are hidden")
    command.add_argument("--diff", action="store_true")
    command.add_argument("--reveal", action="store_true", help="explicitly show values, including secrets")

    command = sub("watch", "Rerun an entrypoint or command when project files change")
    command.add_argument("--cmd", help="command parsed as argv, without shell operators")
    command.add_argument("--interval", type=float, default=0.5)
    command.add_argument("--debounce", type=float, default=0.4)
    sub("route", "List statically discoverable FastAPI, Flask and Django routes")

    command = sub("port", "Inspect ports or choose an available TCP port")
    command.add_argument("port", type=int, nargs="?")
    command.add_argument("--free", action="store_true", help="print an unused port; does not kill anything")
    command.add_argument("--terminate", action="store_true", help="explicitly terminate current owners of the specified port")
    command = sub("detective", "Identify a port owner and its process details")
    command.add_argument("--port", required=True, type=int)

    command = sub("vent", "Remove allowlisted caches and disposable Pistol runtime files")
    command.add_argument("--deep", action="store_true", help="also remove root .venv, node_modules, target, build and dist")
    command.add_argument("--yes", action="store_true", help="confirm deep cleanup")
    command.add_argument("--dry-run", action="store_true")

    command = sub("shrimp", "Map files, imports, symbols, routes, environment references and calls")
    modes = command.add_mutually_exclusive_group()
    for mode in ("tree", "imports", "routes", "env", "calls"):
        modes.add_argument(f"--{mode}", dest="mode", action="store_const", const=mode)
    command.set_defaults(mode="tree")
    command.add_argument("--file", help="limit details to one project file")
    command.add_argument("--output", type=Path)

    command = sub("pwr", "Open elevated Windows Terminal in the current or specified directory")
    command.add_argument("path", type=Path, nargs="?")
    command = sub("explorer", "Explicitly install/remove the current user's pistol: URI handler")
    actions = command.add_mutually_exclusive_group(required=True)
    actions.add_argument("--install", action="store_true")
    actions.add_argument("--uninstall", action="store_true")
    command = commands.add_parser("_uri", help="Internal validated Explorer URI handler", allow_abbrev=False)
    command.add_argument("uri")
    return parser


def confirm(message: str, yes: bool) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        raise PistolError("Confirmation requires an interactive terminal. Review --dry-run where available, then use --yes.")
    return input(f"{message} [y/N] ").strip().lower() in {"y", "yes"}


def _variables(arguments: list[str]) -> dict[str, str]:
    result = {}
    for item in arguments:
        key, equal, value = item.partition("=")
        if not equal or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or "\0" in value:
            raise PistolError("Use --set KEY=VALUE with a valid environment variable name.")
        result[key] = value
    return result


def _chamber_data(item: chamber.Chamber) -> dict:
    payload = asdict(item)
    payload["env"] = sorted(item.env)  # Never leak configured values in listings.
    record = read_json(item.directory / "process.json", {})
    if fire.is_alive(record):
        payload["status"] = "running"
    return payload


def dispatch(args) -> int:
    structured = args.json
    project = getattr(args, "project", None)
    command = args.command
    if command == "chamber":
        if not (args.name or args.configure) and (args.port is not None or args.runtime or args.entrypoint or args.variables or args.unset):
            raise PistolError("Configuration flags require --name or --configure.")
        if args.list:
            values = [_chamber_data(c) for c in chamber.list_chambers()]
            if structured:
                json_output(values)
            elif not values:
                print("No chambers. Create one with pistol chamber --name api.")
            else:
                for value in values:
                    print(f"{value['name']:20} {value['status']:10} :{value['port']:<5} {value['project']}")
            return 0
        if args.name:
            item = chamber.create(args.name, project, args.port, runtime=args.runtime, entrypoint=split_command(args.entrypoint) if args.entrypoint else None, env=_variables(args.variables))
        elif args.switch:
            item = chamber.switch(args.switch)
        elif args.configure:
            with state_lock():
                item = chamber.get(args.configure)
                chamber._assert_stopped(item)
                if args.port is not None:
                    ports.validate_port(args.port)
                    if args.port != item.port and (not ports.available(args.port) or any(c.port == args.port for c in chamber.list_chambers())):
                        raise PistolError("Port is occupied or assigned to another chamber.")
                    item.port = args.port
                if args.runtime and args.runtime != item.runtime:
                    raise PistolError("Create a new chamber to change runtime.")
                if args.entrypoint:
                    replacement = split_command(args.entrypoint)
                    validate_entrypoint(Path(item.project), replacement)
                    inferred = runtime_from_entrypoint(replacement)
                    if inferred and inferred != item.runtime and item.runtime != "command":
                        raise PistolError(f"Entrypoint requires {inferred} runtime; chamber uses {item.runtime}. Create a new chamber.")
                    item.entrypoint = replacement
                item.env.update(_variables(args.variables))
                for key in args.unset:
                    item.env.pop(key, None)
                chamber.save(item)
        elif args.delete:
            if not confirm(f"Delete chamber {args.delete} and its runtime data? Project source is preserved.", args.yes):
                return 0
            chamber.delete(args.delete)
            json_output({"deleted": args.delete}) if structured else print(f"Deleted chamber {args.delete}.")
            return 0
        else:
            if not confirm(f"Reset chamber {args.reset}, deleting its virtual environment and temporary state?", args.yes):
                return 0
            item = chamber.reset(args.reset)
        json_output(_chamber_data(item)) if structured else pairs(_chamber_data(item))
    elif command == "mag":
        if args.list:
            result = magazine.list_magazines()
        elif args.name:
            if args.magazine:
                raise PistolError("Use --name NAME to create, or mag NAME to inspect.")
            result = magazine.create(args.name)
        elif args.magazine:
            result = magazine.add(args.magazine, args.add) if args.add else magazine.remove(args.magazine, args.remove) if args.remove else magazine.get(args.magazine)
        else:
            raise PistolError("Use mag --name NAME, mag --list, or mag NAME --add CHAMBER.")
        if structured:
            json_output(result)
        else:
            items = result if isinstance(result, list) else [result]
            if not items:
                print("No magazines.")
            for item in items:
                print(f"{item.name}: {', '.join(item.chambers) or '(empty)'}")
    elif command == "fire":
        if args.name and args.mag:
            raise PistolError("Choose either a chamber or --mag.")
        if args.stop:
            members = [chamber.get(n) for n in magazine.get(args.mag).chambers] if args.mag else [chamber.get(args.name) if args.name else chamber.current(project)]
            result = [{"chamber": item.name if item else None, "stopped": fire.stop(item, project)} for item in members]
            json_output(result) if structured else [print(f"{r['chamber'] or 'Project'}: {'stopped' if r['stopped'] else 'not running'}") for r in result]
        else:
            if structured:
                raise PistolError("--json is not supported for foreground process output.")
            return fire.run(project, name=args.name, magazine=args.mag)
    elif command == "doctor":
        result = doctor.run(project, chamber_name=args.chamber)
        json_output({"project": result.project, "ok": result.ok, "checks": result.checks}) if structured else health(result)
        return result.exit_code
    elif command == "medic":
        repairs = medic.plan(project, chamber_name=args.chamber, install_deps=args.install_deps)
        if args.dry_run:
            json_output(repairs) if structured else [print(f"- {r.description}: {r.target}") for r in repairs]
            return 0
        if not repairs:
            json_output([]) if structured else print("No safe repairs needed.")
            return 0
        if not structured:
            for repair in repairs:
                print(f"- {repair.description}: {repair.target}")
        if confirm("Apply these repairs?", args.yes):
            result = medic.run(project, chamber_name=args.chamber, install_deps=args.install_deps)
            json_output(result) if structured else print(f"Applied {len(result)} repair(s).")
    elif command == "wtf":
        result = wtf.run(project, chamber_name=args.chamber)
        if structured:
            json_output(result)
        else:
            pairs({"status": result.status, "exit_code": result.exit_code, "current_python": result.python, "project_python": result.project_python, "log": result.log, "git": result.git})
            for finding in result.findings:
                print(f"\nDetected: {finding.issue} ({finding.confidence} confidence)\nEvidence: {finding.evidence}\nNext: {finding.suggestion}")
    elif command == "dev":
        result = dev.run(project)
        json_output(result) if structured else pairs(result)
    elif command == "env":
        result = env.run(project, reveal=args.reveal)
        if structured:
            json_output(result)
        else:
            if not args.diff:
                for variable in result.variables:
                    print(f"{variable.name:32} {', '.join(variable.sources)}")
                    if variable.values is not None:
                        for source, value in variable.values.items():
                            print(f"  {source}: {value}")
            print("Missing from .env: " + (", ".join(result.missing_from_dotenv) or "none"))
            print("Unconfigured source references: " + (", ".join(result.unconfigured_references) or "none"))
            for warning in result.warnings:
                print(f"! {warning}")
    elif command == "watch":
        if structured:
            raise PistolError("--json is not supported for watcher output.")
        return watch.run(project, command=split_command(args.cmd) if args.cmd else None, interval=args.interval, debounce=args.debounce)
    elif command == "route":
        result = routes.run(project)
        if structured:
            json_output(result)
        else:
            for route in result:
                print(f"{route.method:7} {route.path:35} {route.file}:{route.line}" + (f" [{route.note}]" if route.note else ""))
            if not result:
                print("No statically discoverable supported routes.")
    elif command == "port":
        if args.free:
            if args.port is not None or args.terminate:
                raise PistolError("--free selects an unused port; it cannot be combined with a port or --terminate.")
            value = ports.free_port({c.port for c in chamber.list_chambers()})
            json_output({"port": value}) if structured else print(value)
        elif args.terminate:
            if args.port is None:
                raise PistolError("--terminate requires an explicit port number.")
            result = detective.terminate_port(args.port)
            json_output({"terminated": result}) if structured else print(f"Terminated PIDs: {', '.join(map(str, result)) or 'none'}")
        else:
            selected = chamber.current(project)
            numbers = [args.port] if args.port is not None else [selected.port] if selected else sorted({c.port for c in chamber.list_chambers()})
            result = [ports.inspect(number) for number in numbers]
            if structured:
                json_output(result)
            else:
                if not result:
                    print("No chamber ports configured. Use pistol port NUMBER or pistol port --free.")
                for item in result:
                    print(f"{item.port}: {'available (TCP)' if item.available else 'occupied/unavailable (TCP)'}; assigned chambers: {', '.join(item.chambers) or 'none'}")
                    for listener in item.listeners:
                        owner = f"Pistol chamber {listener['chamber']}" if listener['chamber'] else "another process" if listener['pid'] is not None else "owner unavailable"
                        print(f"  {listener['protocol']} {listener['host']} {listener['state']} PID {listener['pid']} ({owner})")
                    if item.warning:
                        print(f"! {item.warning}")
    elif command == "detective":
        result = detective.inspect_port(args.port)
        if structured:
            json_output(result)
        else:
            for item in result:
                pairs(asdict(item))
                print()
            if not result:
                print(f"No visible listener on port {args.port}; TCP bind is {'available' if ports.available(args.port) else 'unavailable'}.")
    elif command == "vent":
        targets = vent.plan(project, deep=args.deep)
        if args.dry_run:
            json_output(targets) if structured else [print(f"{t.path} ({t.reason})") for t in targets]
            return 0
        if args.deep:
            if not structured:
                for target in targets:
                    print(f"- {target.path} ({target.reason})")
            if not confirm("Delete these caches, dependencies and build outputs?", args.yes):
                return 0
        result = vent.run(project, deep=args.deep)
        if structured:
            json_output(result)
        else:
            print(f"Removed {len(result.removed)} target(s); freed {result.bytes_freed:,} bytes ({result.bytes_freed / 1048576:.2f} MiB).")
            for skipped in result.skipped:
                print(f"! {skipped}")
        return 1 if result.skipped else 0
    elif command == "shrimp":
        result = shrimp.run(project, file=args.file)
        if args.output:
            args.output.write_text(shrimp.render(result, args.mode), encoding="utf-8")
            json_output({"output": str(args.output.resolve())}) if structured else print(f"Map written to {args.output.resolve()}")
        elif structured:
            json_output(result)
        else:
            print(shrimp.render(result, args.mode), end="")
    elif command == "pwr":
        pwr.run(args.path or project)
    elif command == "explorer":
        if args.install:
            result = explorer.install()
            json_output({"registered": result}) if structured else print("Registered pistol: for this user. Try pistol:doctor in Explorer. Reinstall the handler if this Python environment moves.")
        else:
            result = explorer.uninstall()
            json_output({"removed": result}) if structured else print("Protocol removed." if result else "Protocol was not installed.")
    elif command == "_uri":
        explorer.handle(args.uri)
    return 0


def _logging():
    logger = logging.getLogger("pistol")
    if logger.handlers:
        return
    directory = data_dir() / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(directory / "pistol.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        _logging()
        return dispatch(args)
    except (PistolError, OSError, ValueError) as exc:
        logging.getLogger("pistol").error("%s", exc)
        if args.json:
            json_output({"error": str(exc)})
        else:
            print(f"pistol: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
