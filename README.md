# Pistol

Pistol is an offline, Windows-first developer CLI and Python library. It checks projects, runs isolated project environments, diagnoses failures, inspects ports, and maps Python code using deterministic static analysis.

**No AI functionality, providers, models, embeddings, accounts, API keys, containers, services, or background daemon.** Core operations work offline. Initial package installation and explicitly requested dependency installation may need the internet unless you have a local wheel cache.

## Installation

Requires **Python 3.11 or newer**. Python 3.14 is supported. The only runtime dependency is `psutil`. Use PowerShell, CMD, Windows Terminal, or Git Bash with the installed Python environment on PATH.

```powershell
cd C:\Users\Keller\pistol
py -m venv .venv
.\.venv\Scripts\activate
py -m pip install -e .

pistol --help
pistol doctor
```

If PowerShell blocks activation, you can use the executables directly without changing execution policy:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\pistol.exe doctor
```

CMD activation: `.venv\Scripts\activate.bat`. Git Bash activation: `source .venv/Scripts/activate`. Editable installation reflects code changes immediately; reinstall only if dependencies or package metadata change. `python -m pistol` also works in the installed environment. To use Pistol across terminals without activating this repository's environment, an optional `pipx install -e .` provides a persistent command (requires pipx configured by you).

For development:

```powershell
py -m pip install -e ".[dev]"
pytest
```

Tests isolate their generated data under `%LOCALAPPDATA%\Pistol\temp`, and never need your accounts, administrator rights, existing chambers, or network. Test fixtures exercise temporary sample projects.

## Commands

| Command | Purpose |
| --- | --- |
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

Every public command has `--help`. Inspection commands support `--json` before or after the command, and `--project "C:\path with spaces"`. By default, Pistol finds the closest parent containing `pyproject.toml`, `requirements.txt`, `package.json`, `.git`, or `manage.py`; without those it uses the current directory. An explicit `--project` always wins.

Exit codes: `0` success, `1` doctor failure/cleanup failure, `2` usage or operational error, `130` interruption. `fire` returns the child's nonzero status when in the portable range 1–125, otherwise 1.

## Chambers

A chamber isolates a Python virtual environment, environment overrides, port assignment, runtime state, logs, cache and temporary files. **It is not an OS sandbox:** programs retain your user privileges and can access your filesystem and network. No firewall rules, containers or virtual networking are created.

```powershell
cd C:\projects\api
pistol chamber --name api --port 8775
pistol chamber --list
pistol chamber --switch api
pistol fire api
```

Python entrypoints are detected in this order: `main.py`, `app.py`, `server.py`, `run.py`, then Django's `manage.py runserver`. For an explicit `--entrypoint`, its script extension takes priority over project markers: `.py`/`.pyw` select Python, `.js`/`.mjs`/`.cjs`/`.ts` select Node, and `.rs` selects Rust. `--runtime` overrides automatic selection. Explicit script files must exist when the chamber is created. Python chambers create a virtual environment with pip using the Python running Pistol; dependencies are not installed automatically.

```powershell
pistol medic --chamber api --install-deps
pistol chamber --configure api --entrypoint="-m uvicorn app:app --host 127.0.0.1 --port {port}"
pistol chamber --configure api --set DEBUG=1
pistol chamber --configure api --unset DEBUG
pistol chamber --configure api --port 8780
```

Entrypoints are argument vectors, not shell programs. `{port}` expands to the chamber's selected port. Use double quotes inside an argument string for paths containing spaces, e.g. `--entrypoint '"scripts\my app.py"'` in PowerShell. Pipes, redirection and shell operators are not interpreted. For Python modules beginning with a dash, use the equals form `--entrypoint="-m ..."`.

`--switch` selects the chamber among those linked to the current project. It does not change the parent terminal's directory or activate its venv. Outside the linked project, use `pistol fire api` explicitly. Multiple chambers may use the same source tree but have separate runtime data and ports.

`package.json` projects without a Python project file can detect `npm run dev` or `npm run start`. Node must already be installed; node_modules are not virtualized. On Windows, the standard npm installation is launched via `node` and `npm-cli.js` to avoid implicit batch argument interpretation. Other `.cmd`/`.bat` wrappers require an explicitly configured `cmd.exe` command or their underlying executable. `--runtime command --entrypoint "executable args"` supports other installed runtimes. Python receives the most extensive diagnostics.

Direct Node script entrypoints use the installed `node` executable. Direct `.rs` entrypoints are compiled with `rustc` to the chamber's temporary runtime directory, then run; Cargo projects can use `cargo run`. The required runtime executable is checked before launch. Entry points beginning with `-m` are treated as Python modules and do not require a matching source file. When searching for project markers from the current directory, Pistol stops before the user's home directory to avoid unrelated package files there.

```powershell
pistol chamber --reset api       # confirms; rebuilds venv/temp/cache/state; keeps config and logs
pistol chamber --delete api      # confirms; removes chamber runtime and magazine memberships
```

Use `--yes` for explicit noninteractive confirmation. Running chambers must be stopped first. Project source is preserved by chamber deletion/reset.

## Magazines and running processes

```powershell
pistol mag --name laick
pistol mag laick --add api
pistol mag laick --add frontend
pistol mag laick --add worker
pistol mag --list
pistol fire --mag laick
```

Create each chamber first, using `--project` to link different directories. Members communicate via ordinary localhost sockets. Every member receives variables such as:

```text
PISTOL_API_HOST=127.0.0.1
PISTOL_API_PORT=8775
PISTOL_FRONTEND_HOST=127.0.0.1
PISTOL_FRONTEND_PORT=3000
```

Names are case insensitive and restricted to letters, digits, underscores and hyphens. Hyphens become underscores in environment names; collisions are rejected. Port selection is best effort, not a reservation: another program can claim a free port before startup.

Runtime environment precedence: `.env` < terminal environment < chamber overrides < magazine discovery variables < Pistol runtime variables. Pistol sets `PORT`, `HOST`, `PISTOL_CHAMBER`, `PISTOL_STATE_DIR`, `PISTOL_LOG_DIR`, `PISTOL_CACHE_DIR`, `TEMP`, `TMP`, and `TMPDIR`. The chamber interpreter and venv executables lead PATH. Your application must actually use `PORT` or the `{port}` argument; Pistol cannot force its listen address.

`fire` stays in the foreground, mirrors merged stdout/stderr to the terminal, and writes a UTF-8-oriented log and PID/start-time record. Ctrl+C requests graceful shutdown, then escalates for processes still running after a timeout. If a magazine member fails, peers are stopped. A completed member with exit code 0 does not stop peers. From a second terminal:

```powershell
pistol fire api --stop
pistol fire --mag laick --stop
```

There is no detached daemon. Abruptly closing/killing the supervisor can leave a child or stale state behind; `doctor`, `wtf`, `fire --stop`, and `medic` help recover. PID creation times guard against PID reuse. Logs can contain application secrets; treat local log files as private.

## Diagnostics, repair, and environment

```powershell
pistol doctor
pistol doctor --chamber api
pistol medic --dry-run
pistol medic --yes
pistol medic --install-deps       # explicit pip/network/build-code permission, then confirmation
pistol wtf --chamber api
pistol env --diff
pistol env --reveal               # explicitly displays values, including secrets
```

Doctor parses source with AST and inspects installed package metadata in the selected Python. It does not import project modules. Missing unconditional declared dependencies and syntax errors are failures. Unresolved imports are warnings because optional/test imports and namespace packages are ambiguous. `pip check` checks installed dependency consistency. Dependency markers, nested requirements and URL requirements need installer validation; Pistol does not implement a full dependency resolver. An entrypoint warning is normal for library-only projects, including this repository.

Medic can create a missing venv, copy `.env.example` only when `.env` does not exist, archive stale PID state, repair incomplete runtime directories, and reassign an occupied chamber port. It never rewrites source. Dependency installation uses requirements.txt when present, otherwise installs the project editable; package build code runs only with `--install-deps`. Repairs are incremental; if pip fails, preceding successful repairs remain applied.

Environment inspection reports relevant names and sources, not all inherited environment values. Simple `.env` assignments and `export KEY=value` are supported; shell expansion, interpolation and multiline dotenv values are not. Source references support `os.getenv`, `os.environ.get`, `os.environ[...]` and aliases. Missing references are advisory because source may provide defaults. `wtf` reports log evidence with confidence and suggested next steps. Its redaction is best effort, so avoid sharing raw logs without review.

## Mapping, watching, ports, and cleanup

```powershell
pistol shrimp --tree
pistol shrimp --imports
pistol shrimp --routes
pistol shrimp --env
pistol shrimp --calls
pistol shrimp --file main.py
pistol shrimp --output map.txt
pistol route
pistol watch --cmd "python main.py"
pistol detective --port 8775
pistol port --free
pistol port 8775 --terminate      # explicit termination request
pistol vent --dry-run
pistol vent
pistol vent --deep --dry-run
pistol vent --deep               # review and confirm dependency/build cleanup
```

Shrimp reports file/directory inventories, functions, classes, methods, imports, internal module links, environment references, framework routes and syntactic call relationships. Calls show their spelling in source, not proof of runtime dispatch. Routes support FastAPI/Flask decorators and Django `path`/`re_path`; dynamic registration, complex aliases and cross-file prefixes cannot always be resolved. Router-local and Django include paths are labeled. The analysis adapter is isolated in `shrimp.PythonAnalyzer` to allow additional languages later. File inventory works for other languages; their symbols are not yet analyzed.

Scanning skips `.git`, virtual environments, node_modules, bytecode, build/dist, and other common generated directories, and never follows links or junctions. Inventory scans stop at 10,000 entries or 10 seconds; Python analysis stops at 15 seconds or approximately 25 MB total, skipping individual files over 2 MB. Truncation is reported. Watch uses portable polling with a debounce, detects additions/deletions, and fails clearly if it cannot obtain a complete bounded snapshot. Files generated outside the ignore list can trigger restarts.

Port checks test TCP bind availability on IPv4/IPv6; detective reports visible TCP listeners and UDP bindings. Some process details may be inaccessible without elevation. `--free` prints an available port and never kills anything. `--terminate` explicitly permits terminating the observed owner even if it is not a Pistol process.

Default vent removes only allowlisted caches (`__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.vite`, `.parcel-cache`, `.next/cache`) plus stopped project/chamber temp/cache and Pistol process logs older than seven days. It preserves node_modules, .venv, target, build and dist. `--deep` also removes those five root dependency/output directories after confirmation. It refuses links/junctions, never deletes the project root, and reports skipped paths. Byte counts are logical file sizes, not filesystem allocation units.

## Windows elevation and Explorer

```powershell
pistol pwr
pistol pwr "C:\Some Folder"
pistol explorer --install
pistol explorer --uninstall
```

`pwr` uses the standard Windows [ShellExecuteW runas verb](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shellexecutew) and UAC to launch `wt.exe` itself as administrator. It opens a new [Windows Terminal window](https://learn.microsoft.com/en-us/windows/terminal/command-line-arguments?tabs=powershell) in the requested directory using your default profile, tabs, colors and window size. If `wt.exe` genuinely cannot be found, Pistol falls back to the standalone PowerShell console. The CLI prints the selected executable; URI launches record it in `%LOCALAPPDATA%\Pistol\logs\uri.log`. No UAC bypass, firewall changes, services, PATH edits or PowerShell module are used.

Explorer setup writes **only** `HKCU\Software\Classes\pistol` and a Pistol user configuration file. Package installation never modifies the registry. Setup refuses to replace a handler owned by another application. The handler uses the installing environment's `pythonw.exe` to avoid a flashing dispatcher console, and logs failures to `%LOCALAPPDATA%\Pistol\logs\uri.log`. Rerun setup if that environment moves or is deleted.

Enter these in Explorer's address bar (Windows may display an external application confirmation):

```text
pistol:pwr
pistol:doctor
pistol:dev
pistol:doctor?path=C%3A%5Cprojects%5Capi
pistol:pwr?path=C%3A%5Cprojects%5Capi
```

Only `doctor`, `dev` and `pwr` are permitted, with one optional URL-encoded absolute local `path`. Arbitrary commands and other query parameters are rejected. URI data is passed as quoted arguments, never evaluated as a shell command. Doctor/dev open a visible non-elevated PowerShell so their output remains readable. Pwr triggers UAC for Windows Terminal normally.

For `pistol:pwr` typed in File Explorer, Pistol reads the active Explorer window's folder and opens the elevated terminal there. Explorer does not pass that folder in the URI itself, so if the active window cannot be identified reliably, Pistol uses your home directory. An explicit URL-encoded absolute local `path` always takes priority. Terminal `pistol pwr` uses the terminal's current directory. After updating Pistol from an older registration, run `pistol explorer --install` again to refresh the protocol command.

## Python API

```python
import pistol
from pistol.chamber import create
from pistol.detective import inspect_port
from pistol import env, shrimp, vent

report = pistol.doctor.run()
print(report.ok, report.exit_code)
for check in report.checks:
    print(check.level, check.message)

owners = inspect_port(8775)                 # list[ProcessInfo]
environment = env.run()                    # EnvironmentReport, values hidden
mapping = shrimp.run()                     # ProjectMap
candidates = vent.plan()                   # inspect before an explicit vent.run()
# api = create("api", project="C:/projects/api", port=8775)
```

The library returns dataclasses/lists/dictionaries; CLI formatting lives in `cli.py` and `output.py`. `fire.launch` returns a process handle whose `finish()` records completion; `fire.run` supervises automatically. Mutation functions such as `chamber.delete`, `vent.run(deep=True)` and `medic.run` treat the API call as explicit authorization; confirmation prompts belong to the CLI.

## Storage and safety

```text
%LOCALAPPDATA%\Pistol\
  chambers\NAME\chamber.json, process.json, venv\, logs\, temp\, cache\, state\
  magazines\NAME.json
  projects\PROJECT_HASH\process.json, logs\, temp\, cache\, state\
  logs\pistol.log (rotated)
  state.lock
%APPDATA%\Pistol\
  config.json
  explorer.json
```

State uses atomic replace, fsync and a cross-process file lock for updates. Runtime data follows the Windows environment variables, without hardcoded usernames. Non-Windows systems use `~/.local/share/Pistol` and `~/.config/Pistol`; pwr/Explorer are Windows only. Pistol is designed for a single trusted user, not hostile concurrent edits to state or project directories.

Pistol never automatically rewrites source, kills unrelated processes, modifies firewall settings, installs services, alters PATH, or registers itself. Cleanup and reset/delete commands are explicit; destructive broader cleanup asks for confirmation. Logs and chamber environment overrides are local plaintext under your user profile; Pistol is not a secret manager.

Generated runtime state stays outside the repository. The editable install's `.venv`, Python bytecode and packaging metadata are standard development artifacts covered by `.gitignore`.
#   P i s t o l - C L I  
 #   P i s t o l - C L I  
 