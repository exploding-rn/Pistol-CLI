import json
from pathlib import Path

from pistol import env, shrimp
from pistol.utils.files import scan


SOURCE = '''
import os as operating
from os import getenv
from fastapi import FastAPI, APIRouter
app = FastAPI()
router = APIRouter(prefix="/items")
@app.get("/health")
async def health():
    return getenv("HEALTH_MODE", "ok")
@router.post("/create")
def create():
    service.send(operating.environ["API_TOKEN"])
app.include_router(router, prefix="/v1")
class Service:
    def send(self, value):
        print(value)
'''


def test_python_ast_symbols_routes_env_calls():
    result = shrimp.parse_python(SOURCE, "api.py")
    assert result.errors == []
    assert result.functions == ["health", "create"]
    assert result.classes == ["Service"]
    assert result.methods == ["Service.send"]
    assert result.env == ["API_TOKEN", "HEALTH_MODE"]
    assert [(r.method, r.path) for r in result.routes] == [("GET", "/health"), ("POST", "/v1/items/create")]
    assert any("create -> service.send" in call for call in result.calls)


def test_flask_and_django_routes():
    result = shrimp.parse_python('''
from flask import Flask
from django.urls import path, include
app = Flask(__name__)
@app.route("/submit", methods=["GET", "POST"])
def submit(): pass
urlpatterns = [path("admin/", include("admin.urls")), path("health/", submit)]
''')
    assert [(r.method, r.path) for r in result.routes] == [("GET", "/submit"), ("POST", "/submit"), ("ANY", "admin/"), ("ANY", "health/")]
    assert "unresolved" in result.routes[2].note


def test_unrelated_get_decorator_is_not_route():
    assert shrimp.parse_python('@cache.get("key")\ndef f(): pass').routes == []


def test_broken_syntax_is_data():
    assert "SyntaxError" in shrimp.parse_python("def broken(:\n").errors[0]


def test_map_does_not_execute_and_ignores_junk(project):
    (project / "danger.py").write_text("raise RuntimeError('must not run')\n", encoding="utf-8")
    (project / ".venv").mkdir()
    (project / ".venv" / "bad.py").write_text("invalid !!!", encoding="utf-8")
    result = shrimp.run(project)
    assert "danger.py" in result.files
    assert all(".venv" not in path for path in result.files)
    assert all(not item.errors for item in result.python)


def test_relative_internal_import_mapping(project):
    package = project / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "a.py").write_text("from .b import function\n", encoding="utf-8")
    (package / "b.py").write_text("def function(): pass\n", encoding="utf-8")
    result = shrimp.run(project)
    assert next(item for item in result.python if item.path == "pkg/a.py").internal_imports == [".b -> pkg/b.py"]
    (package / "a.py").write_text("from . import b\n", encoding="utf-8")
    result = shrimp.run(project)
    assert next(item for item in result.python if item.path == "pkg/a.py").internal_imports == [".b -> pkg/b.py"]


def test_env_diff_does_not_leak_values(project, monkeypatch):
    (project / ".env").write_text("API_TOKEN=super-secret\nEXTRA=value # comment\n", encoding="utf-8")
    (project / ".env.example").write_text("API_TOKEN=sample\nMISSING=sample\n", encoding="utf-8")
    (project / "main.py").write_text("import os\na=os.getenv('PROCESS_ONLY')\nb=os.environ['UNSET']\n", encoding="utf-8")
    monkeypatch.setenv("PROCESS_ONLY", "process-secret")
    result = env.run(project)
    assert result.missing_from_dotenv == ["MISSING"]
    assert result.unconfigured_references == ["UNSET"]
    assert all(variable.values is None for variable in result.variables)
    assert "super-secret" not in repr(result)
    revealed = env.run(project, reveal=True)
    assert next(v for v in revealed.variables if v.name == "API_TOKEN").values[".env"] == "super-secret"


def test_dotenv_parser(project):
    file = project / ".env"
    file.write_text('export NAME="hello # there"\nURL=http://localhost/#fragment\nLITERAL=$(do-not-run)\n', encoding="utf-8")
    assert env.read_dotenv(file) == {"NAME": "hello # there", "URL": "http://localhost/#fragment", "LITERAL": "$(do-not-run)"}


def test_scan_is_bounded(project):
    for i in range(5):
        (project / f"file{i}.py").write_text("", encoding="utf-8")
    result = scan(project, limit=2)
    assert result.warnings
    assert len(result.files) <= 2
