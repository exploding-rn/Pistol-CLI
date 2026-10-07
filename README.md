<div align="center">

# Pistol CLI

<<<<<<< HEAD
A Windows-first developer toolkit for running, inspecting, debugging, and mapping projects from the terminal.
=======
**No AI functionality, providers, models, embeddings, accounts, API keys, containers, hosted services, or Windows Service.** Core operations work offline. LECAP's background proxy starts only when explicitly requested. Initial package installation and explicitly requested dependency installation may need the internet unless you have a local wheel cache.
>>>>>>> 717a075 (Add LECAP, requirements, WSL, and HTTPS support)

`chamber` · `fire` · `doctor` · `medic` · `shrimp` · `detective` · `vent`

<<<<<<< HEAD
</div>

## Install
=======
Requires **Python 3.11 or newer**. Python 3.14 is supported. Runtime dependencies are `psutil` for process inspection and `cryptography` for local certificate issuance and validation. HTTP proxying uses the Python standard library. Use PowerShell, CMD, Windows Terminal, or Git Bash with the installed Python environment on PATH.
>>>>>>> 717a075 (Add LECAP, requirements, WSL, and HTTPS support)

```powershell
irm https://raw.githubusercontent.com/exploding-rn/Pistol-CLI/main/install.ps1 | iex
```

## What is Pistol?

Pistol is a developer CLI built around the idea that common project tasks should be fast, local, and easy to remember.

Instead of juggling different commands for environments, ports, project diagnostics, process inspection, cleanup, and codebase analysis, Pistol puts them behind one command:

pistol

Examples:
pistol doctor
pistol shrimp
pistol detective --port 8775
pistol chamber --list
pistol pwr

Pistol is primarily built for Windows, but much of the core project inspection functionality is portable.
Installation
Requirements
- Windows 10 or Windows 11
- Python 3.11+
- Git
- Windows Terminal recommended
Clone the repository:
git clone https://github.com/exploding-rn/Pistol-CLI.git
cd Pistol-CLI

Create a virtual environment:
py -m venv .venv

Activate it:
.\.venv\Scripts\activate

Install Pistol:
py -m pip install -e .

Test it:
pistol --help
pistol doctor

Use Pistol Everywhere
If you want pistol available without activating the development virtual environment, install it for your user:
py -m pip install --user -e .

Python may tell you that its Scripts directory is not on PATH.
It will usually look similar to:
C:\Users\<you>\AppData\Roaming\Python\Python314\Scripts

Add that directory to your Windows PATH.
After reopening your terminal:
pistol --help

should work from anywhere.
Commands
Command	What it does
pistol chamber	Create and manage isolated project runtimes
pistol mag	Group multiple chambers together
pistol fire	Run a project's configured entrypoint
pistol doctor	Inspect the health of a project
pistol medic	Apply safe automatic repairs
pistol wtf	Diagnose why something failed
pistol dev	Show useful information about the current project
pistol env	Inspect project environment variables
pistol watch	Restart a project when files change
pistol route	Discover application routes
pistol port	Inspect and manage ports
pistol detective	Find the process occupying a port
pistol vent	Clear project caches and disposable files
pistol shrimp	Crawl and map a codebase
pistol pwr	Open an elevated Windows Terminal
pistol explorer	Install or remove Explorer integration


<<<<<<< HEAD
Run:
pistol <command> --help
=======
Tests isolate their generated data under `%LOCALAPPDATA%\Pistol\temp`, and never need your accounts, administrator rights, existing chambers, or Internet access. Proxy integration tests use ephemeral loopback listeners and temporary certificates without modifying OS trust or the hosts file.
>>>>>>> 717a075 (Add LECAP, requirements, WSL, and HTTPS support)

for command-specific options.
Chamber
A chamber is Pistol's project runtime environment.
It can track:
- Project path
- Runtime
- Entrypoint
- Port
- Virtual environment
- Environment variables
- Logs
- Temporary files
- Runtime state
Create one:
pistol chamber --name api --port 8775 --entrypoint main.py

<<<<<<< HEAD
List chambers:
=======
| Command | Purpose |
| --- | --- |
| `pistol config` | Global identity, HTTPS preference, public CA export and explicit trust enrollment |
| `pistol lecap` | Current chamber access settings, status, DNS setup, proxy and verification |
| `pistol wsl` | Start, inspect and stop a local WSL terminal and management console |
| `pistol chamber` | Create, list, select, configure, reset, or delete runtimes |
| `pistol mag` | Group chambers and provide localhost discovery variables |
| `pistol fire [chamber]` | Run the configured entrypoint and capture output |
| `pistol doctor` | Check environment, dependencies, source syntax, ports and state |
| `pistol medic` | Preview and confirm conservative repairs |
| `pistol wtf` | Diagnose the latest run using log evidence and known errors |
| `pistol dev` | Summarize the current project |
| `pistol env [--diff]` | Inspect environment names and configuration differences |
| `pistol watch [--cmd "python main.py"]` | Restart on debounced file changes |
| `pistol route` | List static FastAPI, Flask and Django routes |
| `pistol port [number]` | Check port availability and listeners |
| `pistol detective --port 8775` | Show process details for a port |
| `pistol vent [--deep]` | Clean caches; deep deletion requires confirmation |
| `pistol shrimp` | Map the codebase with Python AST analysis |
| `pistol pwr [directory]` | Open elevated Windows Terminal using normal Windows UAC |
| `pistol explorer --install` | Explicitly register the per-user `pistol:` handler |

Every public command has `--help`. Inspection commands support `--json` before or after the command, and project commands support `--project "C:\path with spaces"`. Global `config` has no project option. By default, Pistol finds the closest parent containing `pyproject.toml`, `requirements.txt`, `package.json`, `.git`, or `manage.py`; without those it uses the current directory. An explicit `--project` always wins.

Exit codes: `0` success, `1` doctor failure/cleanup failure, `2` usage or operational error, `130` interruption. `fire` returns the child's nonzero status when in the portable range 1–125, otherwise 1.

## Pistol WSL Console

Open a lightweight browser dashboard backed by a real Linux PTY inside WSL:

```powershell
pistol wsl gui
pistol wsl gui Ubuntu
pistol wsl list
pistol wsl status
pistol wsl stop
```

Pistol reads `wsl.exe --list --verbose`, selects your default distro (or the only distro),
and asks you to choose if there is no unambiguous default. Explicit names are matched
against installed distros. A running console is reopened without creating another server.
Stop it before switching distros. The default address is `http://localhost:8765`; if that
port is occupied on Windows or Linux, Pistol selects another available loopback port.
The CLI opens your default browser and prints the address. `--no-browser` prints a private
authenticated launch URL instead. All five commands accept `--json`.

The distro needs **Python 3.11+**, its **venv/pip support**, and a **normal default Linux user**.
On Ubuntu, install missing prerequisites from a normal WSL terminal:

```bash
sudo apt update
sudo apt install python3 python3-venv iproute2
```

First launch creates a private environment in `~/.local/share/Pistol/wsl-console/venv`
and installs `aiohttp>=3.14.4,<4` and Pistol's existing `psutil` dependency there. This needs
internet access or a configured pip cache/index. It does not install global Python packages
or invoke sudo. Later launches work offline. Browser terminal assets (xterm.js 6.0.0 and its
fit addon) are bundled locally with their MIT licenses; no CDN, Node build, or Electron is
required. The Windows CLI itself needs no new runtime dependency. For backend development,
install `pip install -e ".[dev,wsl]"`.

The dashboard includes:

- **Terminal:** independent persistent PTY per browser tab, ANSI colors, full-screen programs,
  shell completion, Ctrl+C/Ctrl+D, resizing, and ordinary interactive `sudo`. Closing the tab,
  disconnecting, or stopping the console cleans up its PTY and attached session processes.
  Use **New session** after a disconnect or shell exit; terminal sessions are not resumed.
- **Files:** home directory browsing, back/forward/up, UTF-8 editing up to 2 MiB, file metadata,
  creation, rename, confirmed deletion, download, and uploads up to 32 MiB. Existing files are
  never silently overwritten. Saves require confirmation and reject stale revisions. Symlinks
  are listed but not traversed; use Terminal for intentional symlink operations. System trees,
  the home directory itself, and drive/mount roots are protected from deletion.
- **Processes:** live PID, user, CPU, RAM, command filtering and confirmed termination of your
  own processes, checked against creation time to avoid PID reuse.
- **Ports:** TCP/UDP listeners via `ss`, available process ownership, and Windows localhost
  forwarding candidates inferred from bind addresses. These labels are not reachability probes.
- **Services:** system and user systemd service lists and start/stop/restart where your permissions
  allow. The API never prompts through polkit or escalates; use Terminal when authentication is
  needed. Distros without systemd show a useful explanation.
- **System:** kernel, user, hostname, WSL version, CPU/RAM/disk, uptime, IPs and mounted Windows
  drives. CPU/RAM sampling uses psutil every two seconds rather than spawning monitoring commands.
- **Chambers:** the existing Linux chamber registry plus accessible Windows chamber projects
  mapped to `/mnt/<drive>/...`. Windows information is a snapshot at console launch. This is a
  read-only adapter; manage runtimes using existing Pistol commands in the appropriate OS.

**Security:** the agent binds only to `127.0.0.1`, never a LAN interface. Commands run as the
normal Linux user, and sudo credentials are never stored. A fresh random capability token is
generated for each server. The browser receives it in a URL fragment, removes the fragment,
and retains it in tab session storage. HTTP APIs require a bearer header; WebSockets validate
the origin and authenticate their first message before allocating a PTY. Host/origin checks,
no CORS, no third-party scripts, and a restrictive content security policy protect the local
interface. File operations validate absolute Linux paths and use directory descriptors with
no symlink following. The terminal intentionally has the same access as your Linux shell;
this is not a sandbox. Treat the launch URL and console state as credentials.

Windows tracks the instance in `%LOCALAPPDATA%\Pistol\wsl-console.json` using Pistol's state
lock and atomic writes. Linux state and tokens are in the private (0700) agent directory with
0600 files. Tokens are not included in status output or logs. `pistol wsl stop` checks the Linux
instance identity and requests authenticated graceful shutdown, with Linux PID start-time checks
as a fallback when localhost forwarding is unavailable; it never shuts down WSL.
Agent logs are at `~/.local/share/Pistol/wsl-console/agent.log`; Windows lifecycle messages use
Pistol's existing log. A second lock inside Linux also protects against duplicate launches after
Windows state is lost. If startup succeeds but Windows cannot connect, the CLI preserves the
instance so status/stop still work; check WSL `localhostForwarding` and Windows firewall settings.
Pistol will not widen the bind address to work around forwarding problems.

Tests: `python -m pytest tests/test_wsl.py -q` runs portable parsing, selection, command/state,
authentication and path checks. Linux additionally exercises directory descriptor operations,
real PTY resize/interruption, and cleanup. Run the same test file with the agent venv's Python
inside WSL (with pytest installed) for those tests. Run `python -m pytest -q` for the full suite.

## Chambers

For global identity, chamber access and HTTPS setup, see [LECAP](docs/lecap.md).

A chamber isolates a Python virtual environment, environment overrides, port assignment, runtime state, logs, cache and temporary files. **It is not an OS sandbox:** programs retain your user privileges and can access your filesystem and network. No firewall rules, containers or virtual networking are created.

```powershell
cd C:\projects\api
pistol chamber --name api --port 8775
>>>>>>> 717a075 (Add LECAP, requirements, WSL, and HTTPS support)
pistol chamber --list

Switch chambers:
pistol chamber --switch api

Delete one:
pistol chamber --delete api

A chamber is not an operating-system sandbox. Programs still run with your normal user permissions.
Fire
Run the current project's configured entrypoint:
pistol fire

Or run a specific chamber:
pistol fire api

Pistol tracks the process, runtime, working directory, selected port, and logs.
Use Ctrl+C to stop foreground processes normally.
Magazines
A magazine groups multiple chambers into one project stack.
For example:
frontend
api
worker

Create a magazine:
pistol mag --name myproject

Add chambers:
pistol mag myproject --add frontend
pistol mag myproject --add api
pistol mag myproject --add worker

Launch the whole magazine:
pistol fire --mag myproject

Members communicate through normal localhost ports.
Pistol automatically provides discovery variables such as:
PISTOL_API_HOST=127.0.0.1
PISTOL_API_PORT=8775

PISTOL_FRONTEND_HOST=127.0.0.1
PISTOL_FRONTEND_PORT=3000

Doctor
Inspect a project:
pistol doctor

Example:
Project: C:\Projects\example

✓ Python 3.14.3
✓ Virtual environment exists
✓ pyproject.toml parses correctly
✓ Parsed 33 Python files
✓ Declared dependencies installed

! PORT is not configured

Doctor can inspect things such as:
- Python versions
- Virtual environments
- Dependencies
- Project metadata
- Syntax errors
- Entrypoints
- Environment variables
- Ports
- Chamber state
- Git information
Medic
Doctor finds problems.
Medic tries to fix the safe ones.
pistol medic

Preview repairs first:
pistol medic --dry-run

Automatically confirm safe repairs:
pistol medic --yes

Examples of things Medic may repair:
- Missing virtual environments
- Missing .env copied from .env.example
- Stale Pistol process metadata
- Occupied automatically selected ports
- Broken runtime directories
Pistol does not automatically rewrite your source code.
WTF
When something breaks:
pistol wtf

Pistol analyzes available evidence such as:
- Tracebacks
- Exit codes
- Logs
- Missing modules
- Invalid paths
- Port conflicts
- Environment mismatches
- Connection failures
- Runtime configuration
Example:
Detected:
ModuleNotFoundError: No module named 'fastapi'

Project Python:
C:\Project\.venv\Scripts\python.exe

Last command used:
C:\Python314\python.exe

Likely cause:
Project was started outside its virtual environment.

Shrimp 🦐
Shrimp crawls through the depths of your project and builds a map of the codebase.
pistol shrimp

It can inspect:
- Files
- Directories
- Imports
- Functions
- Classes
- Methods
- Routes
- Environment references
- Module relationships
- Entrypoints
- Basic call relationships

Useful modes:
pistol shrimp --tree
pistol shrimp --imports
pistol shrimp --routes
pistol shrimp --env
pistol shrimp --calls
pistol shrimp --file main.py
pistol shrimp --output map.txt

Detective
Want to know what the hell is using a port?
pistol detective --port 8775

Example:
Port        8775
State       LISTENING
PID         18244
Process     python.exe
Executable  C:\Project\.venv\Scripts\python.exe
Command     python main.py
Parent      powershell.exe

No more digging through Task Manager and netstat.
Port
Check a port:
pistol port 8775

Find an available port:
pistol port --free

Pistol will not terminate processes unless explicitly requested.
Vent
Clear project caches:
pistol vent

Common targets include:
__pycache__
.pytest_cache
.mypy_cache
.ruff_cache
.vite
.parcel-cache
.next/cache

Preview cleanup:
pistol vent --dry-run

More aggressive cleanup:
pistol vent --deep

Deep cleanup asks for confirmation before removing larger generated directories.
Routes
Inspect statically discoverable routes:
pistol route

Pistol currently understands common patterns from:
- FastAPI
- Flask
- Django
Example:
GET     /health
POST    /api
GET     /v1/models

Environment Inspection
Inspect environment configuration:
pistol env

Compare sources:
pistol env --diff

Pistol can compare:
- .env
- .env.example
- Process environment
- Chamber environment
- Environment variables referenced by source code
Values that may contain secrets are hidden by default.
Watch
Automatically rerun a project when files change:
pistol watch

Or:
pistol watch --cmd "python main.py"

Generated folders and common dependency directories are ignored automatically.
Elevated Windows Terminal
One of the smallest but most useful Pistol commands:
pistol pwr

This opens a new elevated Windows Terminal in your current directory using normal Windows UAC.
Example:
C:\Projects\Pistol>

Run:
pistol pwr

and the administrator terminal opens in the same folder.
You can also specify one:
pistol pwr "C:\Projects\My Project"

Explorer Integration
Pistol can register a pistol: URI handler with Windows.
Install it:
pistol explorer --install

Remove it:
pistol explorer --uninstall

Then commands can be entered into File Explorer's address bar:
pistol:pwr

which opens an elevated Windows Terminal for the current Explorer location.
Other supported actions include:
pistol:doctor
pistol:dev

Explorer integration is opt-in and is not installed automatically.
Python Library
Pistol can also be used directly from Python.
import pistolreport = pistol.doctor.run()print(report.ok)for check in report.checks:    print(check.level, check.message)


Port inspection:
from pistol.detective import inspect_portowners = inspect_port(8775)for owner in owners:    print(owner.process_name)    print(owner.pid)


Code mapping:
from pistol import shrimpmapping = shrimp.run()


The CLI and underlying logic are kept separate so Pistol can also be used as a library.
Storage
Pistol keeps runtime data outside your project repository.
Windows runtime data:
%LOCALAPPDATA%\Pistol\

Persistent configuration:
%APPDATA%\Pistol\

Your source repositories stay clean.
Safety
Pistol takes a conservative approach to system changes.
It does not silently:
- Kill unrelated processes
- Rewrite project source
- Disable UAC
- Modify firewall rules
- Install Windows services
- Change your PATH
- Register Explorer integration
- Delete dependency directories
Potentially destructive actions require an explicit command or confirmation.
Development
Clone the repository:
git clone https://github.com/exploding-rn/Pistol-CLI.git
cd Pistol-CLI

Install development dependencies:
py -m venv .venv
.\.venv\Scripts\activate
py -m pip install -e ".[dev]"

Run tests:
pytest

Run Pistol directly:
pistol doctor
pistol shrimp

Editable installation means source changes are reflected immediately.
License
See LICENSE for licensing information.
<div align="center">

Pistol CLI
Small command. Lots of tools.
</div>

<sub>
Development disclaimer: Pistol was vibe coded with assistance from Codex, GPT Astra, and GPT Sol. Generated code was tested, iterated on, and modified during development.
</sub>

<<<<<<< HEAD
=======
Generated runtime state stays outside the repository. The editable install's `.venv`, Python bytecode and packaging metadata are standard development artifacts covered by `.gitignore`.
#   P i s t o l - C L I  
 #   P i s t o l - C L I  
 
>>>>>>> 717a075 (Add LECAP, requirements, WSL, and HTTPS support)
