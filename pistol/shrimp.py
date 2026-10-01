"""Bounded, static Python codebase analysis. Project code is never imported."""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
import tokenize
import time

from .config import PistolError
from .utils.files import scan
from .utils.project import detect, root_path


@dataclass
class Route:
    method: str
    path: str
    file: str
    line: int
    framework: str
    note: str = ""


@dataclass
class CodeFile:
    path: str
    imports: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    env: list[str] = field(default_factory=list)
    routes: list[Route] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    internal_imports: list[str] = field(default_factory=list)


@dataclass
class ProjectMap:
    root: str
    files: list[str]
    directories: list[str]
    python: list[CodeFile]
    entrypoints: list[str]
    dependencies: list[str]
    warnings: list[str]


def dotted(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{dotted(node.value)}.{node.attr}"
    return ""


def literal(node: ast.AST | None, default=None):
    try:
        return ast.literal_eval(node) if node is not None else default
    except (ValueError, TypeError, SyntaxError, RecursionError):
        return default


class PythonAnalyzer(ast.NodeVisitor):
    """Language adapters can expose the same CodeFile model."""

    def __init__(self, path: str):
        self.result = CodeFile(path)
        self.scope: list[str] = []
        self.scope_types: list[str] = []
        self.aliases: dict[str, str] = {}
        self.routers: dict[str, tuple[str, str]] = {}
        self.mounts: dict[str, list[str]] = {}

    def canonical(self, node: ast.AST) -> str:
        name = dotted(node)
        first, _, rest = name.partition(".")
        return self.aliases.get(first, first) + ("." + rest if rest else "")

    def visit_Import(self, node: ast.Import):
        for item in node.names:
            self.result.imports.append(item.name)
            self.aliases[item.asname or item.name.split(".")[0]] = item.name if item.asname else item.name.split(".")[0]

    def visit_ImportFrom(self, node: ast.ImportFrom):
        module = "." * node.level + (node.module or "")
        if node.module:
            self.result.imports.append(module)
        else:
            self.result.imports.extend(module + item.name for item in node.names)
        for item in node.names:
            self.aliases[item.asname or item.name] = module + ("." if node.module else "") + item.name

    def visit_ClassDef(self, node: ast.ClassDef):
        self.result.classes.append(".".join([*self.scope, node.name]))
        self.scope.append(node.name)
        self.scope_types.append("class")
        self.generic_visit(node)
        self.scope.pop()
        self.scope_types.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef):
        name = ".".join([*self.scope, node.name])
        target = self.result.methods if self.scope_types and self.scope_types[-1] == "class" else self.result.functions
        target.append(name)
        for decorator in node.decorator_list:
            self.route(decorator)
        self.scope.append(node.name)
        self.scope_types.append("function")
        self.generic_visit(node)
        self.scope.pop()
        self.scope_types.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def route(self, node: ast.AST):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            return
        owner = dotted(node.func.value)
        method = node.func.attr
        if owner not in self.routers or method not in {"get", "post", "put", "delete", "patch", "head", "options", "route", "api_route", "websocket"}:
            return
        framework, prefix = self.routers[owner]
        keywords = {kw.arg: kw.value for kw in node.keywords}
        path = literal(node.args[0] if node.args else keywords.get("path") or keywords.get("rule"))
        methods = literal(keywords.get("methods"), ["GET"] if method in {"route", "api_route"} else [method.upper()])
        if not isinstance(path, str):
            path = "<dynamic>"
        if not isinstance(methods, (list, tuple)) or not all(isinstance(m, str) for m in methods):
            methods = ["<dynamic>"]
        mounts = self.mounts.get(owner, [""])
        note = "Flask also provides HEAD/OPTIONS" if framework == "Flask" else ""
        if owner not in self.mounts and self.routers[owner][0] == "FastAPI router":
            note = "router-local; external mount prefix unresolved"
        for mount in mounts:
            for verb in methods:
                self.result.routes.append(Route(verb.upper(), mount + prefix + path, self.result.path, node.lineno, framework, note))

    def visit_Call(self, node: ast.Call):
        callee = self.canonical(node.func)
        if callee:
            caller = ".".join(self.scope) or "<module>"
            self.result.calls.append(f"{caller} -> {callee} (line {node.lineno})")
        if callee in {"os.getenv", "os.environ.get", "os.environ.setdefault"} and node.args:
            key = literal(node.args[0])
            if isinstance(key, str):
                self.result.env.append(key)
        if callee in {"django.urls.path", "django.urls.re_path", "django.conf.urls.url"} and node.args:
            route = literal(node.args[0], "<dynamic>")
            if isinstance(route, str):
                included = len(node.args) > 1 and isinstance(node.args[1], ast.Call) and self.canonical(node.args[1].func) == "django.urls.include"
                self.result.routes.append(Route("ANY", route, self.result.path, node.lineno, "Django", "include prefix; child URLconf unresolved" if included else "URLconf-local; methods unresolved"))
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript):
        if self.canonical(node.value) == "os.environ":
            key = literal(node.slice)
            if isinstance(key, str):
                self.result.env.append(key)
        self.generic_visit(node)

    def prepare(self, tree: ast.AST):
        # Resolve aliases and router declarations before decorators, including later mounts.
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                self.visit(node)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Call):
                kind = self.canonical(node.value.func)
                framework = {"fastapi.FastAPI": "FastAPI", "fastapi.APIRouter": "FastAPI router", "flask.Flask": "Flask", "flask.Blueprint": "Flask"}.get(kind)
                if framework:
                    prefix = next((literal(kw.value, "<dynamic-prefix>") for kw in node.value.keywords if kw.arg in {"prefix", "url_prefix"}), "")
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        self.routers[dotted(target)] = (framework, prefix if isinstance(prefix, str) else "<dynamic-prefix>")
                    self.result.frameworks.append(framework)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"include_router", "register_blueprint"} and node.args:
                router = dotted(node.args[0])
                prefix = next((literal(kw.value, "<dynamic-prefix>") for kw in node.keywords if kw.arg in {"prefix", "url_prefix"}), "")
                self.mounts.setdefault(router, []).append(str(prefix))


def parse_python(source: str, filename: str = "<string>") -> CodeFile:
    analyzer = PythonAnalyzer(filename)
    try:
        tree = ast.parse(source, filename=filename)
        analyzer.prepare(tree)
        analyzer.visit(tree)
    except (SyntaxError, ValueError, RecursionError) as exc:
        analyzer.result.errors.append(f"{type(exc).__name__}: {exc}")
    for attribute in ("imports", "env", "frameworks", "calls"):
        setattr(analyzer.result, attribute, sorted(set(getattr(analyzer.result, attribute))))
    return analyzer.result


def run(project: Path | str | None = None, *, file: str | None = None) -> ProjectMap:
    root = root_path(project)
    inventory = scan(root)
    paths = inventory.files
    if file:
        selected = (root / file).resolve()
        if not selected.is_relative_to(root) or not selected.is_file():
            raise PistolError("--file must name an existing file inside the project.")
        if selected not in paths:
            raise PistolError("--file is excluded by the safe project scan.")
        paths = [selected]
    parsed = []
    deadline = time.monotonic() + 15
    bytes_read = 0
    for path in paths:
        if time.monotonic() > deadline or bytes_read > 25_000_000:
            inventory.warnings.append("Python analysis truncated at 15 seconds / 25 MB of source.")
            break
        if path.suffix != ".py":
            continue
        relative = path.relative_to(root).as_posix()
        try:
            if path.stat().st_size > 2_000_000:
                inventory.warnings.append(f"Skipped oversized Python file: {relative}")
                continue
            bytes_read += path.stat().st_size
            with tokenize.open(path) as stream:
                parsed.append(parse_python(stream.read(), relative))
        except (OSError, UnicodeError, SyntaxError) as exc:
            parsed.append(CodeFile(relative, errors=[str(exc)]))
    modules = {}
    for path in inventory.files:
        if path.suffix == ".py":
            parts = list(path.relative_to(root).with_suffix("").parts)
            if parts and parts[0] == "src":
                parts.pop(0)
            if parts and parts[-1] == "__init__":
                parts.pop()
            modules[".".join(parts)] = path.relative_to(root).as_posix()
    for item in parsed:
        for imported in item.imports:
            absolute = imported
            if imported.startswith("."):
                level = len(imported) - len(imported.lstrip("."))
                package = item.path.split("/")[:-1]
                if package and package[0] == "src":
                    package.pop(0)
                absolute = ".".join(package[:max(0, len(package) - level + 1)] + [imported.lstrip(".")]).rstrip(".")
            if absolute in modules:
                item.internal_imports.append(f"{imported} -> {modules[absolute]}")
    info = detect(root)
    dependencies = [f for f in ("pyproject.toml", "requirements.txt", "requirements-dev.txt", "package.json", "poetry.lock", "uv.lock") if (root / f).is_file()]
    directories = sorted({str(parent.relative_to(root)).replace("\\", "/") for path in paths for parent in path.parents if parent != root and parent.is_relative_to(root)})
    return ProjectMap(str(root), [p.relative_to(root).as_posix() for p in paths], directories, parsed, info.entrypoint, dependencies, inventory.warnings)


def render(result: ProjectMap, mode: str = "tree") -> str:
    lines = [f"PROJECT MAP — {result.root}", "", f"Files: {len(result.files)} | Python files: {len(result.python)}", f"Entrypoint: {' '.join(result.entrypoints) or 'not detected'}", f"Dependencies: {', '.join(result.dependencies) or 'none'}"]
    by_path = {item.path: item for item in result.python}
    for path in result.files:
        item = by_path.get(path)
        if mode != "tree" and item is None:
            continue
        details = []
        if item:
            if mode in {"tree", "imports"}:
                details.extend(f"imports {x}" for x in item.imports)
                details.extend(f"internal {x}" for x in item.internal_imports)
            if mode == "tree":
                details.extend(f"class {x}" for x in item.classes)
                details.extend(f"function {x}()" for x in item.functions)
                details.extend(f"method {x}()" for x in item.methods)
                details.extend(f"framework {x}" for x in item.frameworks)
            if mode in {"tree", "routes"}:
                details.extend(f"{r.method:7} {r.path} (line {r.line})" + (f" [{r.note}]" if r.note else "") for r in item.routes)
            if mode in {"tree", "env"}:
                details.extend(f"reads {x}" for x in item.env)
            if mode == "calls":
                details.extend(item.calls)
            details.extend(f"ERROR {x}" for x in item.errors)
        if details or mode == "tree":
            lines.append(f"\n{path}")
            lines.extend(f"  {'└──' if i == len(details) - 1 else '├──'} {detail}" for i, detail in enumerate(details))
    lines.extend(f"! {warning}" for warning in result.warnings)
    return "\n".join(lines) + "\n"
