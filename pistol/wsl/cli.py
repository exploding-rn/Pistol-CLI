"""Argparse integration using Pistol's existing presentation helpers."""
import argparse
import sys
import webbrowser

from ..config import PistolError
from ..output import json_output, pairs
from . import manager


def register(commands):
    command = commands.add_parser("wsl", help="Local WSL terminal and management console", allow_abbrev=False)
    command.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    actions = command.add_subparsers(dest="wsl_action", required=True)
    for name, description in (("gui", "Start or reopen the console"), ("list", "List installed distributions"),
                              ("status", "Inspect the tracked console"), ("stop", "Stop only the console")):
        action = actions.add_parser(name, help=description, allow_abbrev=False)
        action.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        if name == "gui":
            action.add_argument("distro", nargs="?")
            action.add_argument("--no-browser", action="store_true", help="print an authenticated launch URL instead")


def choose(distros):
    if not sys.stdin.isatty():
        raise PistolError("Choose a distribution explicitly: pistol wsl gui <distro>.")
    for index, distro in enumerate(distros, 1):
        print(f"  {index}. {distro.name} ({distro.state})")
    try:
        index = int(input("Distribution number: ")) - 1
        if not 0 <= index < len(distros):
            raise ValueError()
        return distros[index]
    except (ValueError, EOFError):
        raise PistolError("Select a listed distribution number, or pass its name.") from None


def dispatch(args):
    structured = getattr(args, "json", False)
    action = args.wsl_action
    if action == "list":
        items = manager.list_distros()
        if structured:
            json_output(items)
        else:
            print("PISTOL WSL\n")
            width = max(18, *(len(item.name) + 2 for item in items))
            print(f"{'NAME':{width}} {'STATE':14} DEFAULT")
            for item in items:
                print(f"{item.name:{width}} {item.state:14} {'Yes' if item.default else 'No'}")
        return 0
    if action == "status":
        state = manager.status()
        result = manager.public_state(state) if state else {"status": "Stopped"}
    elif action == "stop":
        result = {"status": "Stopped" if manager.stop() else "Already stopped"}
    else:
        if not structured:
            print("PISTOL WSL CONSOLE\n")
        def progress(message):
            if not structured:
                print(f"[ok] {message}", flush=True)
        state, reused = manager.start(args.distro, choose=choose, progress=progress)
        result = manager.public_state(state)
        url = result["url"] + "#token=" + state["token"]
        if not structured:
            if reused:
                print("Pistol WSL Console already running")
            pairs(result)
        opened = False
        if not args.no_browser:
            if not structured:
                print("\nOpening browser...")
            try:
                opened = webbrowser.open(url)
            except (OSError, webbrowser.Error):
                pass
        if not opened:
            # Only the explicit launch command reveals this capability, never status/logs.
            if structured:
                result["launch_url"] = url
            else:
                print(f"\nOpen this private launch URL in your browser:\n{url}")
        if structured:
            json_output(result)
        return 0
    if structured:
        json_output(result)
    else:
        print("PISTOL WSL CONSOLE\n")
        pairs(result)
    return 0
