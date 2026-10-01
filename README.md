<div align="center">

#  Pistol CLI


### A Windows-first developer toolkit for running, inspecting, debugging, and mapping projects from the terminal.

`chamber` · `fire` · `doctor` · `medic` · `shrimp` · `detective` · `vent`

</div>
## ⚡ Install
---

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


Run:
pistol <command> --help

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

List chambers:
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

