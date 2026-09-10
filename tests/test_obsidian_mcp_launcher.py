import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_obsidian_launcher_uses_only_private_local_plugin_key(tmp_path):
    data = tmp_path / "vault/.obsidian/plugins/obsidian-local-rest-api/data.json"
    data.parent.mkdir(parents=True)
    data.write_text(json.dumps({"apiKey": "fixture-key"}))
    data.chmod(0o600)
    binary = tmp_path / "uvx"
    binary.write_text("#!/usr/bin/env python3\nimport os,sys\nassert os.environ['OBSIDIAN_API_KEY']=='fixture-key'\nassert sys.argv[1:]==['--from','mcp-obsidian==0.2.2','mcp-obsidian']\nassert sys.stdin.readline().strip()=='mcp-handshake'\nprint('local credential loaded')\n")
    binary.chmod(0o700)
    env = {
        **os.environ,
        "K2B_VAULT_PATH": str(tmp_path / "vault"),
        "K2B_UVX_BIN": str(binary),
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
    }
    command = ["bash", str(ROOT / "scripts/run-obsidian-mcp.sh")]
    result = subprocess.run(command, env=env, input="mcp-handshake\n", capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "local credential loaded"
    assert "fixture-key" not in result.stdout + result.stderr
    data.chmod(0o644)
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "fixture-key" not in result.stdout + result.stderr


def test_obsidian_launcher_rejects_writable_uvx_before_export(tmp_path):
    data = tmp_path / "vault/.obsidian/plugins/obsidian-local-rest-api/data.json"
    data.parent.mkdir(parents=True)
    data.write_text(json.dumps({"apiKey": "fixture-key"}))
    data.chmod(0o600)
    binary = tmp_path / "uvx"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o777)
    env = {
        **os.environ,
        "K2B_VAULT_PATH": str(tmp_path / "vault"),
        "K2B_UVX_BIN": str(binary),
    }

    result = subprocess.run(
        ["bash", str(ROOT / "scripts/run-obsidian-mcp.sh")],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "fixture-key" not in result.stdout + result.stderr
