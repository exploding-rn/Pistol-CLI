"""Evidence-based failure diagnosis; no speculative root-cause claims."""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import sys

from .chamber import current, get
from .config import read_json
from .env import read_dotenv
from .fire import is_alive, project_python, record_path
from .utils.project import git_state, root_path


@dataclass
class Finding:
    issue: str
    evidence: str
    suggestion: str
    confidence: str = "high"


@dataclass
class Diagnosis:
    project: str
    python: str
    project_python: str
    log: str | None
    status: str
    exit_code: int | None
    findings: list[Finding] = field(default_factory=list)
    git: dict = field(default_factory=dict)


PATTERNS = [
    (r"ModuleNotFoundError: No module named [^\r\n]+", "Missing Python module", "Install the project's declared dependencies in the selected environment: pistol medic --install-deps."),
    (r"(?:ImportError:)[^\r\n]+", "Import failed", "Check the named symbol, package versions, and circular imports."),
    (r"[^\r\n]*(?:Address already in use|WinError 10048|EADDRINUSE)[^\r\n]*", "Port already occupied", "Use pistol detective --port PORT; stop the known owner or choose another chamber port."),
    (r"[^\r\n]*(?:Connection refused|WinError 10061|ECONNREFUSED)[^\r\n]*", "Connection refused", "Check that the dependency is listening at the configured host and port."),
    (r"[^\r\n]*(?:missing (?:required )?(?:env|environment)|environment variable .+ (?:not set|missing)|KeyError: ['\"][A-Z][A-Z0-9_]*['\"])[^\r\n]*", "Possibly missing environment variable", "Compare pistol env --diff and the variable named in the traceback."),
    (r"(?:SyntaxError|IndentationError|TabError):[^\r\n]+", "Python syntax error", "Run pistol doctor for source file and line information."),
    (r"[^\r\n]*(?:PermissionError|Permission denied|Access is denied)[^\r\n]*", "Permission denied", "Check file permissions, open file handles, and the requested path."),
    (r"[^\r\n]*(?:FileNotFoundError|No such file or directory|cannot find the file)[^\r\n]*", "File not found", "Check the configured entrypoint and paths relative to the recorded working directory."),
]


def redact(text: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        if len(value) >= 4 and re.search(r"secret|token|password|passwd|api.?key|credential", key, re.I):
            text = text.replace(value, "<redacted>")
    return re.sub(r"(?i)((?:password|token|secret|api_key)\s*[=:]\s*)[^\s,;]+", r"\1<redacted>", text)


def run(project: Path | str | None = None, *, chamber_name: str | None = None) -> Diagnosis:
    selected = get(chamber_name) if chamber_name else current(project)
    root = Path(selected.project) if selected else root_path(project)
    path = record_path(selected, root)
    record = read_json(path, {}) or read_json(path.parent / "last-process.json", {})
    status = record.get("status", "no recorded process")
    if status == "running" and not is_alive(record):
        status = "stale; exit status unavailable"
    result = Diagnosis(str(root), sys.executable, project_python(root, selected), record.get("log_file"), status, record.get("exit_code"), git=git_state(root))
    log = ""
    if result.log:
        try:
            with Path(result.log).open("rb") as stream:
                stream.seek(0, 2)
                stream.seek(max(0, stream.tell() - 65536))
                log = stream.read().decode("utf-8", errors="replace")
        except OSError as exc:
            result.findings.append(Finding("Log unavailable", str(exc), "The process log may have been moved or cleaned."))
    values = {**os.environ, **read_dotenv(root / ".env"), **(selected.env if selected else {})}
    for pattern, issue, suggestion in PATTERNS:
        matches = re.findall(pattern, log, re.I)
        if matches:
            result.findings.append(Finding(issue, redact(matches[-1][:1500], values), suggestion, "medium" if issue.startswith("Possibly") else "high"))
    if selected and not selected.python.is_file() and selected.runtime == "python":
        result.findings.append(Finding("Chamber Python is missing", str(selected.python), "Run pistol medic for this chamber."))
    command = record.get("command", [])
    if command and selected and selected.runtime == "python" and Path(command[0]).resolve() != selected.python.resolve():
        result.findings.append(Finding("Python executable mismatch", command[0], "Run with pistol fire to use the chamber interpreter.", "medium"))
    if not result.findings:
        from .doctor import run as doctor
        health = doctor(root, chamber_name=selected.name if selected else None)
        for check in health.checks:
            if check.level == "error":
                result.findings.append(Finding(check.message, redact(check.detail or check.message, values), "Resolve this doctor failure, then rerun the project.", "medium"))
    if not result.findings:
        result.findings.append(Finding("No conclusive cause detected", f"Recorded status: {status}; exit code: {result.exit_code}", "Reproduce with pistol fire to capture output; inspect the indicated log.", "low"))
    return result
