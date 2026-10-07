import json
from pathlib import Path
import socket

import pytest

from pistol import chamber, config, lecap
from pistol.cli import build_parser, main


@pytest.fixture
def item(project):
    return chamber.create("api", project, port=23567, create_venv=False)


def test_global_persistence_preserves_existing_settings():
    config.atomic_json(config.config_dir() / "config.json", {"active_chamber": "api", "future": {"a": 1}})
    config.update_global({"host": "Keller", "chamberlain": "PistolChamber", "https": False})
    assert config.global_config() == {"host": "keller", "chamberlain": "pistolchamber", "https": False}
    assert config.settings()["active_chamber"] == "api"
    assert config.settings()["future"] == {"a": 1}
    assert config.settings()["https"] is False


@pytest.mark.parametrize("value,expected", [("Keller", "keller"), ("AI.Laick.Keller.", "ai.laick.keller"), ("büro", "xn--bro-hoa")])
def test_hostname_normalization(value, expected):
    assert config.hostname(value) == expected


@pytest.mark.parametrize("value", ["", "x y", "-host", "host-", "a_b", "a..b", "abc..", "https://abc", "abc:80", "a" * 64, " a", "*", "."])
def test_invalid_hostname(value):
    with pytest.raises(config.PistolError):
        config.hostname(value)


@pytest.mark.parametrize("value", ["on", "off", "yes", "no", "True", "1", "0"])
def test_boolean_parser_rejects_alternatives(value):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["config", "--https", value])


def test_boolean_values():
    assert config.boolean("true") is True
    assert config.boolean("false") is False
    assert build_parser().parse_args(["lecap", "--lan", "false"]).lan is False


def test_inheritance_override_reset(item):
    config.update_global({"host": "Keller", "chamberlain": "pistolchamber"})
    original = lecap.effective(item)
    assert original.urls == ["http://pistolchamber.keller:23567"]
    assert original.sources["port"] == "chamber/default"
    customized = lecap.update(item, {"port": 5000, "chamberlain": "LAICK", "subdomain": ["AI", "Dashboard"]})
    assert customized.domains == ["ai.laick.keller", "dashboard.laick.keller"]
    assert customized.urls == ["http://ai.laick.keller", "http://dashboard.laick.keller"]
    assert customized.target == "127.0.0.1:5000"
    assert customized.sources["host"] == "global"
    assert customized.sources["chamberlain"] == "local"
    assert item.port == 23567
    assert lecap.effective(item) == customized
    assert lecap.update(item, {}, reset=True) == original


def test_https_and_domain_routing(item):
    result = lecap.update(item, {"domain": "Example.Test", "https": True, "port": 5000})
    assert result.urls == ["https://example.test"]
    assert result.target == "127.0.0.1:5000"
    assert result.sources["https"] == "local"


def test_port_only_preserves_direct_access(item):
    result = lecap.update(item, {"port": 5000})
    assert result.mode == "direct"
    assert result.urls[0].endswith(":5000")


def test_settings_remain_outside_project(item):
    lecap.update(item, {"enabled": False})
    assert (item.directory / "lecap.json").is_file()
    assert not (Path(item.project) / "lecap.json").exists()


def test_requires_chamber(capsys):
    assert main(["lecap"]) == 2
    assert "no unambiguous Pistol chamber" in capsys.readouterr().err


def test_nested_folder_and_ambiguity(item, project, monkeypatch):
    child = project / "src"
    child.mkdir()
    monkeypatch.chdir(child)
    assert lecap.current().name == item.name
    chamber.create("second", project, create_venv=False)
    with pytest.raises(config.PistolError, match="unambiguous"):
        lecap.current()
    chamber.switch("second")
    assert lecap.current().name == "second"


def test_collision_and_listener_target(item, project):
    lecap.update(item, {"domain": "app.test"})
    second = chamber.create("second", project, create_venv=False)
    lecap.update(second, {"domain": "app.test"})
    with pytest.raises(config.PistolError, match="Duplicate"):
        lecap.routes()
    lecap.update(second, {"enabled": False})
    lecap.update(item, {"port": 443})
    with pytest.raises(config.PistolError, match="listener"):
        lecap.routes()


@pytest.mark.parametrize("value", ["0.0.0.0", "8.8.8.8", "127.0.0.1", "224.0.0.1", "::", "169.254.1.1"])
def test_lan_public_and_wildcard_addresses_forbidden(value):
    with pytest.raises(config.PistolError):
        lecap.private_address(value)


def test_cli_global_json(capsys):
    assert main(["config", "--host", "Keller", "--https", "false", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["identity"] == "pistolchamber.keller"
    assert main(["--json", "config"]) == 0
    assert json.loads(capsys.readouterr().out)["https"] is False


def test_cli_status_sources(item, monkeypatch, capsys):
    from pistol import lecap_dns, lecap_proxy
    monkeypatch.setattr(lecap_dns, "inspect", lambda access: [{"ok": False}])
    monkeypatch.setattr(lecap_proxy, "probe", lambda access, runtime_state=None: "inactive")
    assert main(["lecap", "--project", item.project, "--subdomain", "ai", "--json"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["sources"]["host"] == "global"
    assert value["sources"]["subdomain"] == "local"
    assert value["proxy_status"] == "inactive"


def test_verify_reports_failure_without_mutation(item, monkeypatch):
    from pistol import lecap_verify
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(OSError("DNS unavailable")))
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(OSError("connection refused")))
    result = lecap_verify.verify(lecap.effective(item, {"domain": "app.test", "https": True}))
    assert result["passed"] is False
    assert any(c["check"].startswith("DNS") and not c["ok"] for c in result["checks"])
    assert any(c["check"].startswith("system certificate trust") and not c["ok"] for c in result["checks"])
    assert not (config.data_dir() / "pki").exists()


def test_hosts_explicit_permission_and_scoped_update(item, tmp_path, monkeypatch):
    from pistol import lecap_dns
    path = tmp_path / "hosts"
    original = b"127.0.0.1 localhost\r\n# keep this comment\r\n"
    path.write_bytes(original)
    backend = lecap_dns.HostsBackend(path)
    access = lecap.effective(item, {"domain": "app.test"})
    monkeypatch.setattr(lecap_dns, "is_admin", lambda: False)
    with pytest.raises(config.PistolError, match="Administrator"):
        backend.apply(access)
    assert path.read_bytes() == original
    monkeypatch.setattr(lecap_dns, "is_admin", lambda: True)
    backend.apply(access)
    backend.apply(access)
    assert path.read_bytes().count(b"app.test") == 1
    assert path.read_bytes().startswith(original)
    backend.apply(access, remove=True)
    assert path.read_bytes() == original
    assert path.with_name("hosts.pistol-backup").read_bytes() == original


def test_hosts_conflicts_are_not_overwritten(item, tmp_path, monkeypatch):
    from pistol import lecap_dns
    path = tmp_path / "hosts"
    path.write_text("192.168.1.1 app.test\n")
    monkeypatch.setattr(lecap_dns, "is_admin", lambda: True)
    with pytest.raises(config.PistolError, match="conflict"):
        lecap_dns.HostsBackend(path).apply(lecap.effective(item, {"domain": "app.test"}))
    assert path.read_text() == "192.168.1.1 app.test\n"


def test_hidden_initialization_has_no_state_or_network(monkeypatch, capsys):
    from pistol import cli
    monkeypatch.setattr(cli, "_logging", lambda: pytest.fail("logging initialized"))
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("network used"))
    assert main(["_init"]) == 0
    output = capsys.readouterr().out
    assert "100%" in output and "██████" in output
    assert "\x1b" not in output and "\r" not in output
    assert not config.data_dir().exists()
    assert not config.config_dir().exists()
    assert "_init" not in build_parser().format_help()
    with pytest.raises(SystemExit):
        build_parser().parse_args(["_init"])


def test_presentation_ascii_fallback():
    import io
    from pistol.install_backend import initialize
    class ASCII(io.StringIO):
        encoding = "ascii"
    output = ASCII()
    initialize(output)
    assert "PISTOL" in output.getvalue()
    assert "\x1b" not in output.getvalue()


def test_existing_commands_still_discoverable():
    help_text = build_parser().format_help()
    for name in "chamber mag fire doctor wtf medic dev env watch route port detective vent shrimp pwr explorer req".split():
        assert name in help_text


@pytest.mark.parametrize("changes", [{"enabled": "false"}, {"https": 1}, {"port": 0}, {"port": 65536}, {"port": True}, {"subdomain": "ai"}, {"host": "a.b"}])
def test_invalid_overrides_leave_saved_settings_intact(item, changes):
    lecap.update(item, {"port": 5000})
    before = (item.directory / "lecap.json").read_bytes()
    with pytest.raises(config.PistolError):
        lecap.update(item, changes)
    assert (item.directory / "lecap.json").read_bytes() == before


def test_global_changes_inherit_but_local_values_win(item):
    config.update_global({"host": "first"})
    lecap.update(item, {"subdomain": ["ai"]})
    config.update_global({"host": "second"})
    assert lecap.effective(item).domains == ["ai.pistolchamber.second"]
    lecap.update(item, {"host": "local"})
    config.update_global({"host": "third"})
    assert lecap.effective(item).domains == ["ai.pistolchamber.local"]


def test_unexpected_dns_address_is_a_failure(item, monkeypatch):
    from pistol import lecap_dns
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80))])
    result = lecap_dns.inspect(lecap.effective(item))
    assert result[0]["ok"] is False


def test_conflicting_mutation_and_operation_flags_do_not_write(item):
    assert main(["lecap", "--project", item.project, "--verify", "--port", "5000"]) == 2
    assert not (item.directory / "lecap.json").exists()


def test_more_specific_project_association_wins(item, project, monkeypatch):
    (project / "pyproject.toml").write_text("")
    child = project / "nested"
    child.mkdir()
    chamber.create("nested", child, runtime="command", create_venv=False)
    monkeypatch.chdir(child)
    assert lecap.current().name == "nested"


def test_hidden_subprocess_creates_no_bytecode_or_state(tmp_path):
    import os
    import shutil
    import subprocess
    import sys
    import pistol
    isolated_source = tmp_path / "source"
    shutil.copytree(Path(pistol.__file__).parent, isolated_source / "pistol", ignore=shutil.ignore_patterns("__pycache__"))
    script = "import sys; sys.argv = ['pistol', '_init']; from pistol.cli import main; raise SystemExit(main())"
    result = subprocess.run([sys.executable, "-B", "-c", script], cwd=isolated_source, env={**os.environ, "PYTHONPATH": str(isolated_source)}, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0
    assert "██████" in result.stdout
    assert not list(isolated_source.rglob("__pycache__"))
    assert not config.data_dir().exists()
    assert not config.config_dir().exists()
