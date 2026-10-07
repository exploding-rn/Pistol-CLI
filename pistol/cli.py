"""The single Pistol command. Parsing and output are kept out of core logic."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import sys

from . import (
    __version__,
    chamber,
    detective,
    dev,
    doctor,
    env,
    explorer,
    fire,
    magazine,
    medic,
    ports,
    pwr,
    req,
    routes,
    shrimp,
    vent,
    watch,
    wtf,
)

from .config import PistolError, data_dir, read_json, state_lock
from .output import health, json_output, pairs
from .utils.commands import split_command
from .utils.project import runtime_from_entrypoint, validate_entrypoint


# ============================================================
# PARSER
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pistol",
        description="Developer tools and project runtimes.",
        allow_abbrev=False,
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"Pistol {__version__}",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="structured output for inspection commands",
    )

    commands = parser.add_subparsers(
        dest="command",
        required=True,
    )

    common = argparse.ArgumentParser(
        add_help=False,
        allow_abbrev=False,
    )

    common.add_argument(
        "--project",
        type=Path,
        help="project directory (default: nearest project root)",
    )

    common.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
    )

    def sub(name: str, help_text: str):
        return commands.add_parser(
            name,
            help=help_text,
            description=help_text,
            parents=[common],
            allow_abbrev=False,
        )

    from .config import boolean

    from .wsl.cli import register
    register(commands)

    command = commands.add_parser("config", help="Global Pistol identity and HTTPS", allow_abbrev=False)
    command.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    command.add_argument("--chamberlain")
    command.add_argument("--host")
    command.add_argument("--https", type=boolean, metavar="true|false")
    command.add_argument("--export-ca", type=Path, metavar="FILE", help="export only the public Pistol CA certificate")
    command.add_argument("--trust-ca", action="store_true", help="explicitly enroll the CA in Windows current-user Root trust")

    command = sub("lecap", "Local Environment Chamber Access Point for the current chamber")
    for key in ("enabled", "https", "lan", "proxy", "redirect"):
        command.add_argument("--" + key, type=boolean, metavar="true|false")
    command.add_argument("--port", type=int, help="internal HTTP target port (defaults to chamber port)")
    for key in ("chamberlain", "host", "domain", "lan-address"):
        command.add_argument("--" + key)
    command.add_argument("--subdomain", action="append", help="repeat for multiple subdomains")
    actions = command.add_mutually_exclusive_group()
    actions.add_argument("--reset", action="store_true", help="clear local overrides and inherit defaults")
    actions.add_argument("--verify", action="store_true", help="check actual DNS, target, proxy and TLS")
    actions.add_argument("--serve", nargs="?", const="toggle", choices=("start", "stop"),
                         help="toggle the background proxy, or explicitly start/stop all enabled proxy routes")
    actions.add_argument("--dns", choices=("plan", "install", "remove"), help="inspect or explicitly update local hosts entries (admin required for writes)")

    # --------------------------------------------------------
    # chamber
    # --------------------------------------------------------

    command = sub(
        "chamber",
        "Create and manage isolated project runtimes",
    )

    actions = command.add_mutually_exclusive_group(
        required=True,
    )

    actions.add_argument(
        "--name",
        help="create a chamber",
    )

    actions.add_argument(
        "--list",
        action="store_true",
    )

    actions.add_argument(
        "--switch",
        metavar="NAME",
    )

    actions.add_argument(
        "--delete",
        metavar="NAME",
    )

    actions.add_argument(
        "--reset",
        metavar="NAME",
    )

    actions.add_argument(
        "--configure",
        metavar="NAME",
        help="edit entrypoint, port or environment",
    )

    command.add_argument(
        "--port",
        type=int,
    )

    command.add_argument(
        "--runtime",
        choices=[
            "python",
            "node",
            "rust",
            "command",
        ],
    )

    command.add_argument(
        "--entrypoint",
        help=(
            "argv string; use --entrypoint='-m module ...' "
            "for modules; {port} expands"
        ),
    )

    command.add_argument(
        "--set",
        dest="variables",
        action="append",
        default=[],
        metavar="KEY=VALUE",
    )

    command.add_argument(
        "--unset",
        action="append",
        default=[],
        metavar="KEY",
    )

    command.add_argument(
        "--yes",
        action="store_true",
        help="confirm reset or deletion",
    )

    # --------------------------------------------------------
    # magazine
    # --------------------------------------------------------

    command = sub(
        "mag",
        "Group chambers using localhost ports",
    )

    command.add_argument(
        "magazine",
        nargs="?",
    )

    actions = command.add_mutually_exclusive_group()

    actions.add_argument(
        "--name",
        help="create a magazine",
    )

    actions.add_argument(
        "--list",
        action="store_true",
    )

    actions.add_argument(
        "--add",
        metavar="CHAMBER",
    )

    actions.add_argument(
        "--remove",
        metavar="CHAMBER",
    )

    # --------------------------------------------------------
    # fire
    # --------------------------------------------------------

    command = sub(
        "fire",
        "Run an entrypoint in the foreground; Ctrl+C stops it",
    )

    command.add_argument(
        "name",
        nargs="?",
        help="chamber name",
    )

    command.add_argument(
        "--mag",
        metavar="NAME",
    )

    command.add_argument(
        "--stop",
        action="store_true",
        help="stop the tracked process or magazine",
    )

    # --------------------------------------------------------
    # doctor / wtf
    # --------------------------------------------------------

    for name, description in (
        (
            "doctor",
            "Check project health without executing source",
        ),
        (
            "wtf",
            "Diagnose failures using logs and deterministic evidence",
        ),
    ):
        command = sub(
            name,
            description,
        )

        command.add_argument(
            "--chamber",
            help="inspect a specific chamber",
        )

    # --------------------------------------------------------
    # medic
    # --------------------------------------------------------

    command = sub(
        "medic",
        "Review and apply conservative repairs",
    )

    command.add_argument(
        "--chamber",
    )

    command.add_argument(
        "--yes",
        action="store_true",
        help="approve listed repairs",
    )

    command.add_argument(
        "--install-deps",
        action="store_true",
        help="explicitly permit pip installation",
    )

    command.add_argument(
        "--dry-run",
        action="store_true",
    )

    # --------------------------------------------------------
    # dev
    # --------------------------------------------------------

    sub(
        "dev",
        "Summarize the current developer project",
    )

    # --------------------------------------------------------
    # env
    # --------------------------------------------------------

    command = sub(
        "env",
        "Inspect environment names and sources; values are hidden",
    )

    command.add_argument(
        "--diff",
        action="store_true",
    )

    command.add_argument(
        "--reveal",
        action="store_true",
        help="explicitly show values, including secrets",
    )

    # --------------------------------------------------------
    # req
    # --------------------------------------------------------

    command = sub(
        "req",
        "Scan Python imports and manage project requirements",
    )

    actions = command.add_mutually_exclusive_group()

    actions.add_argument(
        "--check",
        action="store_true",
        help="compare detected dependencies with the current chamber",
    )

    actions.add_argument(
        "--write",
        action="store_true",
        help="add detected packages to requirements.txt",
    )

    actions.add_argument(
        "--install",
        action="store_true",
        help="install missing packages into the current chamber",
    )

    actions.add_argument(
        "--sync",
        action="store_true",
        help="scan, write, install and verify",
    )

    actions.add_argument(
        "--python",
        action="store_true",
        help="show Python version and compatibility information",
    )

    # --------------------------------------------------------
    # watch
    # --------------------------------------------------------

    command = sub(
        "watch",
        "Rerun an entrypoint or command when project files change",
    )

    command.add_argument(
        "--cmd",
        help="command parsed as argv, without shell operators",
    )

    command.add_argument(
        "--interval",
        type=float,
        default=0.5,
    )

    command.add_argument(
        "--debounce",
        type=float,
        default=0.4,
    )

    # --------------------------------------------------------
    # route
    # --------------------------------------------------------

    sub(
        "route",
        "List statically discoverable FastAPI, Flask and Django routes",
    )

    # --------------------------------------------------------
    # port
    # --------------------------------------------------------

    command = sub(
        "port",
        "Inspect ports or choose an available TCP port",
    )

    command.add_argument(
        "port",
        type=int,
        nargs="?",
    )

    command.add_argument(
        "--free",
        action="store_true",
        help="print an unused port",
    )

    command.add_argument(
        "--terminate",
        action="store_true",
        help="explicitly terminate current owners of the specified port",
    )

    # --------------------------------------------------------
    # detective
    # --------------------------------------------------------

    command = sub(
        "detective",
        "Identify a port owner and its process details",
    )

    command.add_argument(
        "--port",
        required=True,
        type=int,
    )

    # --------------------------------------------------------
    # vent
    # --------------------------------------------------------

    command = sub(
        "vent",
        "Remove allowlisted caches and disposable Pistol runtime files",
    )

    command.add_argument(
        "--deep",
        action="store_true",
        help="also remove root .venv, node_modules, target, build and dist",
    )

    command.add_argument(
        "--yes",
        action="store_true",
        help="confirm deep cleanup",
    )

    command.add_argument(
        "--dry-run",
        action="store_true",
    )

    # --------------------------------------------------------
    # shrimp
    # --------------------------------------------------------

    command = sub(
        "shrimp",
        "Map files, imports, symbols, routes, environment references and calls",
    )

    modes = command.add_mutually_exclusive_group()

    for mode in (
        "tree",
        "imports",
        "routes",
        "env",
        "calls",
    ):
        modes.add_argument(
            f"--{mode}",
            dest="mode",
            action="store_const",
            const=mode,
        )

    command.set_defaults(
        mode="tree",
    )

    command.add_argument(
        "--file",
        help="limit details to one project file",
    )

    command.add_argument(
        "--output",
        type=Path,
    )

    # --------------------------------------------------------
    # pwr
    # --------------------------------------------------------

    command = sub(
        "pwr",
        "Open elevated Windows Terminal in the current or specified directory",
    )

    command.add_argument(
        "path",
        type=Path,
        nargs="?",
    )

    # --------------------------------------------------------
    # Explorer
    # --------------------------------------------------------

    command = sub(
        "explorer",
        "Install/remove the current user's pistol: URI handler",
    )

    actions = command.add_mutually_exclusive_group(
        required=True,
    )

    actions.add_argument(
        "--install",
        action="store_true",
    )

    actions.add_argument(
        "--uninstall",
        action="store_true",
    )

    # Internal URI handler
    command = commands.add_parser(
        "_uri",
        help="Internal Explorer URI handler",
        allow_abbrev=False,
    )

    command.add_argument(
        "uri",
    )

    return parser


# ============================================================
# HELPERS
# ============================================================

def confirm(
    message: str,
    yes: bool,
) -> bool:
    if yes:
        return True

    if not sys.stdin.isatty():
        raise PistolError(
            "Confirmation requires an interactive terminal. "
            "Use --yes for explicit confirmation."
        )

    return (
        input(
            f"{message} [y/N] "
        )
        .strip()
        .lower()
        in {"y", "yes"}
    )


def _variables(
    arguments: list[str],
) -> dict[str, str]:
    result: dict[str, str] = {}

    for item in arguments:
        key, equal, value = item.partition("=")

        if (
            not equal
            or not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*",
                key,
            )
            or "\0" in value
        ):
            raise PistolError(
                "Use --set KEY=VALUE with a valid environment variable name."
            )

        result[key] = value

    return result


def _chamber_data(
    item: chamber.Chamber,
) -> dict:
    payload = asdict(item)

    # Never expose configured values in listings.
    payload["env"] = sorted(
        item.env
    )

    record = read_json(
        item.directory / "process.json",
        {},
    )

    if fire.is_alive(record):
        payload["status"] = "running"

    return payload


def _req_scan_message(
    structured: bool,
    message: str = "Scanning project requirements...",
):
    if not structured:
        print(
            message,
            flush=True,
        )


def _print_req_result(
    result,
    *,
    show_standard: bool = True,
):
    print(
        f"Project: {result.project}"
    )

    print()
    print("Third-party requirements")

    if not result.third_party:
        print("  none")

    for item in result.third_party:
        icon = (
            "✓"
            if item.installed
            else "✗"
        )

        package = (
            item.package_name
            or "unresolved"
        )

        version = (
            f" ({item.version})"
            if item.version
            else ""
        )

        print(
            f"  {icon} "
            f"{item.import_name:20} "
            f"-> {package}{version}"
        )

    if result.unresolved:
        print()
        print("Unresolved imports")

        for name in result.unresolved:
            print(
                f"  ? {name}"
            )

    if show_standard:
        print()

        standard = (
            ", ".join(
                result.standard_library
            )
            if result.standard_library
            else "none"
        )

        local = (
            ", ".join(
                result.local_modules
            )
            if result.local_modules
            else "none"
        )

        print(
            f"Standard library: {standard}"
        )

        print(
            f"Local modules: {local}"
        )

    missing = result.missing

    if missing:
        print()
        print(
            f"{len(missing)} detected package(s) "
            "are missing from the current chamber."
        )
    else:
        print()
        print(
            "✓ All known detected packages are installed."
        )


def _print_python_info(
    result,
):
    print("Python")
    print()

    print(
        f"  Pistol runtime:      "
        f"{result.python.running}"
    )

    print(
        f"  Chamber runtime:     "
        f"{result.python.chamber or 'none'}"
    )

    print(
        f"  pyproject requires:  "
        f"{result.python.declared or 'not declared'}"
    )

    print(
        f"  .python-version:     "
        f"{result.python.python_version_file or 'not present'}"
    )

    print(
        f"  Static minimum:      "
        f"{result.python.static_minimum or 'unknown'}"
    )


# ============================================================
# DISPATCH
# ============================================================

def dispatch(args) -> int:
    structured = getattr(
        args,
        "json",
        False,
    )

    project = getattr(
        args,
        "project",
        None,
    )

    command = args.command

    if command == "wsl":
        from .wsl.cli import dispatch as dispatch_wsl
        return dispatch_wsl(args)

    if command in {"config", "lecap"}:
        from . import access_commands
        return access_commands.configure(args) if command == "config" else access_commands.access(args)

    # --------------------------------------------------------
    # chamber
    # --------------------------------------------------------

    if command == "chamber":
        if (
            not (
                args.name
                or args.configure
            )
            and (
                args.port is not None
                or args.runtime
                or args.entrypoint
                or args.variables
                or args.unset
            )
        ):
            raise PistolError(
                "Configuration flags require --name or --configure."
            )

        if args.list:
            values = [
                _chamber_data(c)
                for c in chamber.list_chambers()
            ]

            if structured:
                json_output(values)

            elif not values:
                print(
                    "No chambers. "
                    "Create one with pistol chamber --name api."
                )

            else:
                for value in values:
                    print(
                        f"{value['name']:20} "
                        f"{value['status']:10} "
                        f":{value['port']:<5} "
                        f"{value['project']}"
                    )

            return 0

        if args.name:
            if not structured and sys.stdout.isatty():
                from .install_backend import initialize
                initialize()
            item = chamber.create(
                args.name,
                project,
                args.port,
                runtime=args.runtime,
                entrypoint=(
                    split_command(
                        args.entrypoint
                    )
                    if args.entrypoint
                    else None
                ),
                env=_variables(
                    args.variables
                ),
            )

        elif args.switch:
            item = chamber.switch(
                args.switch
            )

        elif args.configure:
            with state_lock():
                item = chamber.get(
                    args.configure
                )

                chamber._assert_stopped(
                    item
                )

                if args.port is not None:
                    ports.validate_port(
                        args.port
                    )

                    already_assigned = any(
                        c.port == args.port
                        for c in chamber.list_chambers()
                    )

                    if (
                        args.port != item.port
                        and (
                            not ports.available(
                                args.port
                            )
                            or already_assigned
                        )
                    ):
                        raise PistolError(
                            "Port is occupied or assigned "
                            "to another chamber."
                        )

                    item.port = args.port

                if (
                    args.runtime
                    and args.runtime != item.runtime
                ):
                    raise PistolError(
                        "Create a new chamber to change runtime."
                    )

                if args.entrypoint:
                    replacement = split_command(
                        args.entrypoint
                    )

                    validate_entrypoint(
                        Path(
                            item.project
                        ),
                        replacement,
                    )

                    inferred = runtime_from_entrypoint(
                        replacement
                    )

                    if (
                        inferred
                        and inferred != item.runtime
                        and item.runtime != "command"
                    ):
                        raise PistolError(
                            f"Entrypoint requires {inferred} runtime; "
                            f"chamber uses {item.runtime}."
                        )

                    item.entrypoint = replacement

                item.env.update(
                    _variables(
                        args.variables
                    )
                )

                for key in args.unset:
                    item.env.pop(
                        key,
                        None,
                    )

                chamber.save(
                    item
                )

        elif args.delete:
            if not confirm(
                f"Delete chamber {args.delete} "
                "and its runtime data? "
                "Project source is preserved.",
                args.yes,
            ):
                return 0

            chamber.delete(
                args.delete
            )

            if structured:
                json_output({
                    "deleted": args.delete,
                })
            else:
                print(
                    f"Deleted chamber {args.delete}."
                )

            return 0

        else:
            if not confirm(
                f"Reset chamber {args.reset}, "
                "deleting its virtual environment "
                "and temporary state?",
                args.yes,
            ):
                return 0

            item = chamber.reset(
                args.reset
            )

        if structured:
            json_output(
                _chamber_data(item)
            )
        else:
            pairs(
                _chamber_data(item)
            )

        return 0

    # --------------------------------------------------------
    # magazine
    # --------------------------------------------------------

    elif command == "mag":
        if args.list:
            result = magazine.list_magazines()

        elif args.name:
            if args.magazine:
                raise PistolError(
                    "Use --name NAME to create, "
                    "or mag NAME to inspect."
                )

            result = magazine.create(
                args.name
            )

        elif args.magazine:
            if args.add:
                result = magazine.add(
                    args.magazine,
                    args.add,
                )

            elif args.remove:
                result = magazine.remove(
                    args.magazine,
                    args.remove,
                )

            else:
                result = magazine.get(
                    args.magazine
                )

        else:
            raise PistolError(
                "Use mag --name NAME, mag --list, "
                "or mag NAME --add CHAMBER."
            )

        if structured:
            json_output(
                result
            )

        else:
            items = (
                result
                if isinstance(
                    result,
                    list,
                )
                else [result]
            )

            if not items:
                print(
                    "No magazines."
                )

            for item in items:
                print(
                    f"{item.name}: "
                    f"{', '.join(item.chambers) or '(empty)'}"
                )

        return 0

    # --------------------------------------------------------
    # fire
    # --------------------------------------------------------

    elif command == "fire":
        if (
            args.name
            and args.mag
        ):
            raise PistolError(
                "Choose either a chamber or --mag."
            )

        if args.stop:
            if args.mag:
                members = [
                    chamber.get(n)
                    for n in magazine.get(
                        args.mag
                    ).chambers
                ]
            else:
                members = [
                    chamber.get(
                        args.name
                    )
                    if args.name
                    else chamber.current(
                        project
                    )
                ]

            result = []

            for item in members:
                result.append({
                    "chamber": (
                        item.name
                        if item
                        else None
                    ),
                    "stopped": fire.stop(
                        item,
                        project,
                    ),
                })

            if structured:
                json_output(
                    result
                )
            else:
                for record in result:
                    print(
                        f"{record['chamber'] or 'Project'}: "
                        f"{'stopped' if record['stopped'] else 'not running'}"
                    )

            return 0

        if structured:
            raise PistolError(
                "--json is not supported for foreground process output."
            )

        return fire.run(
            project,
            name=args.name,
            magazine=args.mag,
        )

    # --------------------------------------------------------
    # doctor
    # --------------------------------------------------------

    elif command == "doctor":
        result = doctor.run(
            project,
            chamber_name=args.chamber,
        )

        if structured:
            json_output({
                "project": result.project,
                "ok": result.ok,
                "checks": result.checks,
            })
        else:
            health(
                result
            )

        return result.exit_code

    # --------------------------------------------------------
    # medic
    # --------------------------------------------------------

    elif command == "medic":
        repairs = medic.plan(
            project,
            chamber_name=args.chamber,
            install_deps=args.install_deps,
        )

        if args.dry_run:
            if structured:
                json_output(
                    repairs
                )
            else:
                for repair in repairs:
                    print(
                        f"- {repair.description}: "
                        f"{repair.target}"
                    )

            return 0

        if not repairs:
            if structured:
                json_output([])
            else:
                print(
                    "No safe repairs needed."
                )

            return 0

        if not structured:
            for repair in repairs:
                print(
                    f"- {repair.description}: "
                    f"{repair.target}"
                )

        if confirm(
            "Apply these repairs?",
            args.yes,
        ):
            result = medic.run(
                project,
                chamber_name=args.chamber,
                install_deps=args.install_deps,
            )

            if structured:
                json_output(
                    result
                )
            else:
                print(
                    f"Applied {len(result)} repair(s)."
                )

        return 0

    # --------------------------------------------------------
    # req
    # --------------------------------------------------------

    elif command == "req":

        # WRITE
        if args.write:
            _req_scan_message(
                structured,
                "Scanning imports and updating requirements.txt...",
            )

            path = req.write(
                project
            )

            if structured:
                json_output({
                    "requirements": str(path),
                })
            else:
                print(
                    f"✓ Updated {path}"
                )

            return 0

        # INSTALL
        if args.install:
            _req_scan_message(
                structured,
                "Scanning for missing requirements...",
            )

            installed = req.install(
                project
            )

            if structured:
                json_output({
                    "installed": installed,
                })

            elif installed:
                print()
                print(
                    "Installed:"
                )

                for name in installed:
                    print(
                        f"  ✓ {name}"
                    )

            else:
                print(
                    "✓ All detected requirements are already installed."
                )

            return 0

        # SYNC
        if args.sync:
            _req_scan_message(
                structured,
                "Scanning and syncing project requirements...",
            )

            path, installed, result = req.sync(
                project
            )

            if structured:
                json_output({
                    "requirements": str(path),
                    "installed": installed,
                    "report": req.to_dict(result),
                })

            else:
                print(
                    f"Requirements: {path}"
                )

                if installed:
                    print()

                    print(
                        "Installed:"
                    )

                    for name in installed:
                        print(
                            f"  ✓ {name}"
                        )

                print()
                _print_req_result(
                    result
                )

            return 0

        # NORMAL SCAN / CHECK / PYTHON
        _req_scan_message(
            structured
        )

        result = req.run(
            project
        )

        if structured:
            json_output(
                req.to_dict(result)
            )
            return 0

        print()

        if args.python:
            _print_python_info(
                result
            )

            return 0

        _print_req_result(
            result,
            show_standard=not args.check,
        )

        if (
            args.check
            and result.missing
        ):
            return 1

        return 0

    # --------------------------------------------------------
    # wtf
    # --------------------------------------------------------

    elif command == "wtf":
        result = wtf.run(
            project,
            chamber_name=args.chamber,
        )

        if structured:
            json_output(
                result
            )

        else:
            pairs({
                "status": result.status,
                "exit_code": result.exit_code,
                "current_python": result.python,
                "project_python": result.project_python,
                "log": result.log,
                "git": result.git,
            })

            for finding in result.findings:
                print()
                print(
                    f"Detected: {finding.issue} "
                    f"({finding.confidence} confidence)"
                )

                print(
                    f"Evidence: {finding.evidence}"
                )

                print(
                    f"Next: {finding.suggestion}"
                )

        return 0

    # --------------------------------------------------------
    # dev
    # --------------------------------------------------------

    elif command == "dev":
        result = dev.run(
            project
        )

        if structured:
            json_output(
                result
            )
        else:
            pairs(
                result
            )

        return 0

    # --------------------------------------------------------
    # env
    # --------------------------------------------------------

    elif command == "env":
        result = env.run(
            project,
            reveal=args.reveal,
        )

        if structured:
            json_output(
                result
            )

        else:
            if not args.diff:
                for variable in result.variables:
                    print(
                        f"{variable.name:32} "
                        f"{', '.join(variable.sources)}"
                    )

                    if variable.values is not None:
                        for (
                            source,
                            value,
                        ) in variable.values.items():
                            print(
                                f"  {source}: {value}"
                            )

            print(
                "Missing from .env: "
                + (
                    ", ".join(
                        result.missing_from_dotenv
                    )
                    or "none"
                )
            )

            print(
                "Unconfigured source references: "
                + (
                    ", ".join(
                        result.unconfigured_references
                    )
                    or "none"
                )
            )

            for warning in result.warnings:
                print(
                    f"! {warning}"
                )

        return 0

    # --------------------------------------------------------
    # watch
    # --------------------------------------------------------

    elif command == "watch":
        if structured:
            raise PistolError(
                "--json is not supported for watcher output."
            )

        return watch.run(
            project,
            command=(
                split_command(
                    args.cmd
                )
                if args.cmd
                else None
            ),
            interval=args.interval,
            debounce=args.debounce,
        )

    # --------------------------------------------------------
    # route
    # --------------------------------------------------------

    elif command == "route":
        result = routes.run(
            project
        )

        if structured:
            json_output(
                result
            )

        else:
            for route in result:
                suffix = (
                    f" [{route.note}]"
                    if route.note
                    else ""
                )

                print(
                    f"{route.method:7} "
                    f"{route.path:35} "
                    f"{route.file}:{route.line}"
                    f"{suffix}"
                )

            if not result:
                print(
                    "No statically discoverable supported routes."
                )

        return 0

    # --------------------------------------------------------
    # port
    # --------------------------------------------------------

    elif command == "port":
        if args.free:
            if (
                args.port is not None
                or args.terminate
            ):
                raise PistolError(
                    "--free cannot be combined "
                    "with a port or --terminate."
                )

            value = ports.free_port({
                c.port
                for c in chamber.list_chambers()
            })

            if structured:
                json_output({
                    "port": value,
                })
            else:
                print(
                    value
                )

            return 0

        if args.terminate:
            if args.port is None:
                raise PistolError(
                    "--terminate requires an explicit port."
                )

            result = detective.terminate_port(
                args.port
            )

            if structured:
                json_output({
                    "terminated": result,
                })
            else:
                print(
                    "Terminated PIDs: "
                    + (
                        ", ".join(
                            map(
                                str,
                                result,
                            )
                        )
                        or "none"
                    )
                )

            return 0

        selected = chamber.current(
            project
        )

        if args.port is not None:
            numbers = [
                args.port
            ]

        elif selected:
            numbers = [
                selected.port
            ]

        else:
            numbers = sorted({
                c.port
                for c in chamber.list_chambers()
            })

        result = [
            ports.inspect(
                number
            )
            for number in numbers
        ]

        if structured:
            json_output(
                result
            )

        else:
            if not result:
                print(
                    "No chamber ports configured. "
                    "Use pistol port NUMBER or pistol port --free."
                )

            for item in result:
                state = (
                    "available (TCP)"
                    if item.available
                    else "occupied/unavailable (TCP)"
                )

                print(
                    f"{item.port}: {state}; "
                    f"assigned chambers: "
                    f"{', '.join(item.chambers) or 'none'}"
                )

                for listener in item.listeners:
                    if listener["chamber"]:
                        owner = (
                            "Pistol chamber "
                            f"{listener['chamber']}"
                        )

                    elif listener["pid"] is not None:
                        owner = (
                            "another process"
                        )

                    else:
                        owner = (
                            "owner unavailable"
                        )

                    print(
                        f"  {listener['protocol']} "
                        f"{listener['host']} "
                        f"{listener['state']} "
                        f"PID {listener['pid']} "
                        f"({owner})"
                    )

                if item.warning:
                    print(
                        f"! {item.warning}"
                    )

        return 0

    # --------------------------------------------------------
    # detective
    # --------------------------------------------------------

    elif command == "detective":
        result = detective.inspect_port(
            args.port
        )

        if structured:
            json_output(
                result
            )

        else:
            for item in result:
                pairs(
                    asdict(item)
                )
                print()

            if not result:
                state = (
                    "available"
                    if ports.available(
                        args.port
                    )
                    else "unavailable"
                )

                print(
                    f"No visible listener on port {args.port}; "
                    f"TCP bind is {state}."
                )

        return 0

    # --------------------------------------------------------
    # vent
    # --------------------------------------------------------

    elif command == "vent":
        targets = vent.plan(
            project,
            deep=args.deep,
        )

        if args.dry_run:
            if structured:
                json_output(
                    targets
                )
            else:
                for target in targets:
                    print(
                        f"{target.path} "
                        f"({target.reason})"
                    )

            return 0

        if args.deep:
            if not structured:
                for target in targets:
                    print(
                        f"- {target.path} "
                        f"({target.reason})"
                    )

            if not confirm(
                "Delete these caches, dependencies "
                "and build outputs?",
                args.yes,
            ):
                return 0

        result = vent.run(
            project,
            deep=args.deep,
        )

        if structured:
            json_output(
                result
            )

        else:
            print(
                f"Removed {len(result.removed)} target(s); "
                f"freed {result.bytes_freed:,} bytes "
                f"({result.bytes_freed / 1048576:.2f} MiB)."
            )

            for skipped in result.skipped:
                print(
                    f"! {skipped}"
                )

        return (
            1
            if result.skipped
            else 0
        )

    # --------------------------------------------------------
    # shrimp
    # --------------------------------------------------------

    elif command == "shrimp":
        result = shrimp.run(
            project,
            file=args.file,
        )

        if args.output:
            args.output.write_text(
                shrimp.render(
                    result,
                    args.mode,
                ),
                encoding="utf-8",
            )

            if structured:
                json_output({
                    "output": str(
                        args.output.resolve()
                    ),
                })
            else:
                print(
                    f"Map written to "
                    f"{args.output.resolve()}"
                )

        elif structured:
            json_output(
                result
            )

        else:
            print(
                shrimp.render(
                    result,
                    args.mode,
                ),
                end="",
            )

        return 0

    # --------------------------------------------------------
    # pwr
    # --------------------------------------------------------

    elif command == "pwr":
        pwr.run(
            args.path
            or project
        )

        return 0

    # --------------------------------------------------------
    # explorer
    # --------------------------------------------------------

    elif command == "explorer":
        if args.install:
            result = explorer.install()

            if structured:
                json_output({
                    "registered": result,
                })
            else:
                print(
                    "Registered pistol: for this user."
                )
                print(
                    "Try pistol:doctor in Explorer."
                )

        else:
            result = explorer.uninstall()

            if structured:
                json_output({
                    "removed": result,
                })
            else:
                print(
                    "Protocol removed."
                    if result
                    else "Protocol was not installed."
                )

        return 0

    # --------------------------------------------------------
    # internal URI
    # --------------------------------------------------------

    elif command == "_uri":
        explorer.handle(
            args.uri
        )

        return 0

    raise PistolError(
        f"Unknown command: {command}"
    )


# ============================================================
# LOGGING
# ============================================================

def _logging():
    logger = logging.getLogger(
        "pistol"
    )

    if logger.handlers:
        return

    directory = (
        data_dir()
        / "logs"
    )

    directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    handler = RotatingFileHandler(
        directory / "pistol.log",
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )

    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s "
            "%(levelname)s "
            "%(name)s: "
            "%(message)s"
        )
    )

    logger.addHandler(
        handler
    )

    logger.setLevel(
        logging.INFO
    )

    logger.propagate = False


# ============================================================
# MAIN
# ============================================================

def main(
    argv: list[str] | None = None,
) -> int:
    for stream in (
        sys.stdout,
        sys.stderr,
    ):
        if hasattr(
            stream,
            "reconfigure",
        ):
            stream.reconfigure(
                encoding="utf-8",
                errors="replace",
            )

    # Not registered with argparse: absent from help, choices and completion.
    # This path deliberately precedes logging/state initialization.
    if list(sys.argv[1:] if argv is None else argv) == ["_init"]:
        previous = sys.dont_write_bytecode
        try:
            sys.dont_write_bytecode = True
            from .install_backend import initialize
            initialize()
        finally:
            sys.dont_write_bytecode = previous
        return 0

    parser = build_parser()

    args = parser.parse_args(
        argv
    )

    try:
        _logging()

        return dispatch(
            args
        )

    except (
        PistolError,
        OSError,
        ValueError,
    ) as exc:
        logging.getLogger(
            "pistol"
        ).error(
            "%s",
            exc,
        )

        if getattr(
            args,
            "json",
            False,
        ):
            json_output({
                "error": str(exc),
            })

        else:
            print(
                f"pistol: {exc}",
                file=sys.stderr,
            )

        return 2

    except KeyboardInterrupt:
        print(
            "\nInterrupted.",
            file=sys.stderr,
        )

        return 130


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
