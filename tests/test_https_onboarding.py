"""HTTPS onboarding only mutates trust after explicit user consent."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes

from pistol import access_commands, certificates, config
from pistol.cli import main


@pytest.fixture
def windows_trust(monkeypatch):
    state = {"trusted": False, "enrollments": 0}
    monkeypatch.setattr(certificates, "windows_trust_supported", lambda: True)
    monkeypatch.setattr(certificates, "is_ca_trusted", lambda: state["trusted"])

    def enroll():
        state["enrollments"] += 1
        state["trusted"] = True
        return "Pistol Local CA enrolled in current-user Root"

    monkeypatch.setattr(certificates, "trust_ca", enroll)
    return state


def test_first_enable_yes_enrolls_after_prompt(windows_trust, monkeypatch, capsys):
    monkeypatch.setattr(access_commands, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda: "yes")
    assert main(["config", "--https", "true"]) == 0
    output = capsys.readouterr().out
    assert "Proceed? [y/N]" in output
    assert "Windows current-user Trusted Root" in output
    assert "✓ Pistol Local CA created" in output
    assert "✓ Pistol Local CA trusted" in output
    assert "✓ HTTPS enabled" in output
    assert windows_trust["enrollments"] == 1
    assert config.global_config()["https"] is True
    assert certificates.ca_exists()
    private = (certificates.directory() / "ca-key.pem").read_bytes()
    assert b"PRIVATE KEY" in private
    assert "PRIVATE KEY" not in output
    assert private.decode() not in output


@pytest.mark.parametrize("answer", ["n", "no", ""])
def test_declining_keeps_https_and_explains_later_command(answer, windows_trust, monkeypatch, capsys):
    monkeypatch.setattr(access_commands, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda: answer)
    assert main(["config", "--https", "true"]) == 0
    output = capsys.readouterr().out
    assert "HTTPS remains enabled, but the CA is not trusted" in output
    assert "pistol config --trust-ca" in output
    assert windows_trust["enrollments"] == 0
    assert config.global_config()["https"] is True


def test_already_trusted_does_not_prompt(windows_trust, monkeypatch, capsys):
    windows_trust["trusted"] = True
    monkeypatch.setattr(access_commands, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda: pytest.fail("unexpected prompt"))
    assert main(["config", "--https", "true"]) == 0
    assert "Proceed?" not in capsys.readouterr().out
    assert windows_trust["enrollments"] == 0


def test_noninteractive_enable_does_not_prompt_or_enroll(windows_trust, monkeypatch, capsys):
    monkeypatch.setattr(access_commands, "_interactive", lambda: False)
    monkeypatch.setattr("builtins.input", lambda: pytest.fail("unexpected prompt"))
    assert main(["config", "--https", "true"]) == 0
    output = capsys.readouterr().out
    assert "Proceed?" not in output
    assert "pistol config --trust-ca" in output
    assert windows_trust["enrollments"] == 0
    assert config.global_config()["https"] is True


def test_json_enable_and_inspection_are_structured_without_prompt(windows_trust, monkeypatch, capsys):
    monkeypatch.setattr(access_commands, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda: pytest.fail("unexpected prompt"))
    assert main(["config", "--https", "true", "--json"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["https_enabled"] is True
    assert first["ca_exists"] is True
    assert first["ca_trusted"] is False
    assert first["ca_trust_required"] is True
    assert main(["config", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ca_trust_required"] is True
    assert windows_trust["enrollments"] == 0


def test_enrollment_failure_keeps_ca_and_https_for_retry(windows_trust, monkeypatch, capsys):
    monkeypatch.setattr(access_commands, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda: "y")
    monkeypatch.setattr(certificates, "trust_ca", lambda: (_ for _ in ()).throw(config.PistolError("policy denied")))
    assert main(["config", "--https", "true"]) == 2
    captured = capsys.readouterr()
    assert "policy denied" in captured.err
    assert "trust enrollment failed" in captured.err
    assert "pistol config --trust-ca" in captured.err
    assert "✓ Pistol Local CA trusted" not in captured.out
    assert "✓ HTTPS enabled" not in captured.out
    assert config.global_config()["https"] is True
    assert certificates.ca_exists()


def test_reenable_and_disable_do_not_prompt(windows_trust, monkeypatch, capsys):
    config.update_global({"https": True})
    monkeypatch.setattr(access_commands, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda: pytest.fail("unexpected prompt"))
    assert main(["config", "--https", "true"]) == 0
    assert main(["config"]) == 0
    assert main(["config", "--https", "false"]) == 0
    assert "Proceed?" not in capsys.readouterr().out
    assert windows_trust["enrollments"] == 0


def test_inspection_is_read_only_without_ca(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda: pytest.fail("unexpected prompt"))
    assert main(["config", "--json"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["ca_exists"] is False
    assert value["ca_trusted"] is False
    assert value["ca_trust_required"] is False
    assert not certificates.directory().exists()


def test_manual_trust_is_idempotent_and_uses_only_public_user_root(tmp_path, monkeypatch):
    public = tmp_path / "ca.pem"
    public.write_text("public certificate", encoding="utf-8")
    monkeypatch.setattr(certificates, "ensure_ca", lambda: public)
    monkeypatch.setattr(certificates, "windows_trust_supported", lambda: True)
    trust = {"value": False}
    monkeypatch.setattr(certificates, "is_ca_trusted", lambda: trust["value"])
    calls = []

    def add(command, **kwargs):
        calls.append(command)
        trust["value"] = True
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(certificates.subprocess, "run", add)
    assert "enrolled" in certificates.trust_ca()
    assert calls == [["certutil.exe", "-user", "-addstore", "Root", str(public)]]
    assert "no trust-store change" in certificates.trust_ca()
    assert len(calls) == 1


def test_trust_query_matches_public_thumbprint_in_current_user_root(monkeypatch):
    public = certificates.ensure_ca()
    expected = x509.load_pem_x509_certificate(public.read_bytes()).fingerprint(hashes.SHA1()).hex().upper()
    monkeypatch.setattr(certificates, "windows_trust_supported", lambda: True)
    calls = []

    def read(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "false", "")

    monkeypatch.setattr(certificates.subprocess, "run", read)
    assert certificates.is_ca_trusted() is False
    command, kwargs = calls[0]
    assert command[0] == "powershell.exe"
    assert "StoreLocation]::CurrentUser" in command[-1]
    assert "StoreName]::Root" in command[-1]
    assert kwargs["env"]["PISTOL_CA_THUMBPRINT"] == expected
    assert "PRIVATE KEY" not in str(command) + str(kwargs)


def test_manual_trust_failure_reports_step_and_keeps_ca(tmp_path, monkeypatch):
    public = tmp_path / "ca.pem"
    public.write_text("public certificate", encoding="utf-8")
    monkeypatch.setattr(certificates, "ensure_ca", lambda: public)
    monkeypatch.setattr(certificates, "windows_trust_supported", lambda: True)
    monkeypatch.setattr(certificates, "is_ca_trusted", lambda: False)
    monkeypatch.setattr(certificates.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", "policy denied"))
    with pytest.raises(config.PistolError, match="policy denied"):
        certificates.trust_ca()
    assert public.is_file()


def test_explicit_cli_trust_when_already_trusted_reports_no_change(monkeypatch, capsys):
    certificates.ensure_ca()
    monkeypatch.setattr(certificates, "is_ca_trusted", lambda: True)
    original_run = certificates.subprocess.run

    def no_enrollment(command, **kwargs):
        if command[0] == "certutil.exe":
            pytest.fail("trust store should not be changed")
        return original_run(command, **kwargs)

    monkeypatch.setattr(certificates.subprocess, "run", no_enrollment)
    assert main(["config", "--trust-ca"]) == 0
    output = capsys.readouterr().out
    assert "no trust-store change was required" in output
    assert "CA trusted" not in output


def test_real_noninteractive_stdin_keeps_ca_untrusted():
    completed = subprocess.run(
        [sys.executable, "-m", "pistol", "config", "--https", "true"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert completed.returncode == 0
    assert "Proceed?" not in completed.stdout
    assert "HTTPS remains enabled, but the CA is not trusted" in completed.stdout
    assert "pistol config --trust-ca" in completed.stdout
    assert config.global_config()["https"] is True
    assert certificates.ca_exists()
    assert certificates.is_ca_trusted() is False


@pytest.mark.skipif(os.name != "nt", reason="Windows current-user Root store")
def test_real_read_only_current_user_trust_check_for_new_ca():
    certificates.ensure_ca()
    assert certificates.is_ca_trusted() is False
