"""Load private machine-local Obsidian credentials without consuming MCP stdin."""
import json
import os
import stat
import sys
from pathlib import Path

MCP_OBSIDIAN_PACKAGE = "mcp-obsidian==0.2.2"


def trusted_uvx() -> Path:
    """Resolve uvx without consulting mutable PATH and reject writable binaries."""
    configured = os.environ.get("K2B_UVX_BIN")
    candidates = (
        [Path(configured).expanduser()]
        if configured
        else [Path("/opt/homebrew/bin/uvx"), Path("/usr/local/bin/uvx")]
    )
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
            metadata = resolved.stat()
        except OSError:
            continue
        if (
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_uid in {0, os.getuid()}
            and not metadata.st_mode & 0o022
            and os.access(resolved, os.X_OK)
        ):
            return resolved
    raise ValueError("trusted uvx executable unavailable")


vault = Path(os.environ.get("K2B_VAULT_PATH", str(Path.home() / "Projects/K2B-Vault")))
path = vault / ".obsidian/plugins/obsidian-local-rest-api/data.json"
try:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, encoding="utf-8") as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise ValueError("plugin credentials must be an owned private regular file (chmod 600)")
        key = json.load(handle).get("apiKey")
    if not isinstance(key, str) or not key.strip():
        raise ValueError("local plugin API key missing")
    uvx = trusted_uvx()
    os.environ["OBSIDIAN_API_KEY"] = key
    os.execv(
        str(uvx),
        [str(uvx), "--from", MCP_OBSIDIAN_PACKAGE, "mcp-obsidian"],
    )
except (OSError, ValueError, AttributeError):
    print("Obsidian MCP unavailable: verify private local plugin settings and uvx; no credential printed", file=sys.stderr)
    sys.exit(1)
