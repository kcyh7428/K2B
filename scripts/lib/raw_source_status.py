"""Pure raw-source metadata classifier for K2B Task 4.

classify(relative_path, text) inspects already-read text only. It performs no
filesystem, network, provider, or write operations. PyYAML safe_load and the
standard library are the only dependencies.
"""

from __future__ import annotations

import re

import yaml


FRONTMATTER_READ_LIMIT_BYTES = 256 * 1024
FRONTMATTER_RE = re.compile(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.DOTALL)

TECHNICAL_ROOTS = (
    "raw/orchestrator-inbox/",
    "raw/orchestrator-results/",
)
TECHNICAL_TYPES = frozenset(
    {
        "orchestrator-result",
        "orchestrator-task",
        "orchestrator-status",
        "orchestrator-inbox",
    }
)

_MISSING_FRONTMATTER_REASON = "no frontmatter present; compilation status unknown"


def _is_technical_path(relative_path: str) -> bool:
    if not isinstance(relative_path, str):
        return False
    normalized = relative_path.replace("\\", "/").lstrip("/")
    return any(normalized.startswith(root) for root in TECHNICAL_ROOTS)


def _normalize_compiled(raw_value) -> str | None:
    """Mirror old compiled_value: None stays "", others stringified+lowered.

    Returns None only for unreadable handles when raw_value is not scalar-ish;
    the caller decides the unreadable case. For scalars this returns a string.
    """
    if raw_value is None:
        return ""
    value = str(raw_value).strip()
    return value.lower()


def _legacy_completion(compiled: str) -> bool:
    return compiled in {"true", "yes", "1"}


def _legacy_disposition(compiled: str) -> str | None:
    if compiled == "skipped":
        return "skipped"
    if compiled == "deferred":
        return "deferred"
    return None


def _parse_frontmatter(text: str) -> tuple[str, object]:
    """Return (state, payload).

    state is 'parsed' (payload is the mapping), 'missing' (payload None), or
    'unreadable' (payload is a short reason string).
    """
    # Match the existing helper's BOM/leading whitespace handling, then cap.
    stripped = text.lstrip("\ufeff \t\r\n") if isinstance(text, str) else ""
    if not stripped:
        return "missing", None

    limited = stripped
    encoded = stripped.encode("utf-8", errors="replace")
    if len(encoded) > FRONTMATTER_READ_LIMIT_BYTES:
        limited = encoded[:FRONTMATTER_READ_LIMIT_BYTES].decode("utf-8", errors="replace")

    if not limited.startswith("---"):
        return "missing", None

    match = FRONTMATTER_RE.match(limited)
    if not match:
        return "unreadable", "unterminated or malformed opening frontmatter"

    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        return "unreadable", f"invalid YAML frontmatter ({exc.__class__.__name__})"
    except RecursionError:
        return "unreadable", "frontmatter too deeply nested to parse"

    if not isinstance(data, dict):
        return "unreadable", "frontmatter is not a closed YAML mapping"
    return "parsed", data


def classify(relative_path: str, text: str) -> dict:
    """Classify source ownership and legacy metadata without claiming digestion."""
    if not isinstance(relative_path, str) or not isinstance(text, str):
        raise ValueError("raw classification requires path and text strings")
    metadata_state, payload = _parse_frontmatter(text)
    declared = str(payload.get("type", "")).strip().casefold() if isinstance(payload, dict) else ""
    technical_path = _is_technical_path(relative_path)
    technical = technical_path or declared in TECHNICAL_TYPES
    compiled = None
    if metadata_state == "unreadable":
        reason = "unreadable metadata: " + str(payload)
    elif metadata_state == "missing":
        reason = _MISSING_FRONTMATTER_REASON
    elif "compiled" not in payload:
        reason = "compiled key absent; integration is unmeasured"
    elif not isinstance(payload["compiled"], (str, bool, int, float, type(None))):
        metadata_state = "unreadable"
        reason = "compiled value is not a scalar"
    else:
        compiled = _normalize_compiled(payload["compiled"])
        reason = "legacy compilation flag is not integration proof"
    if technical:
        owner = "designated orchestrator raw directory" if technical_path else "orchestrator metadata type"
        reason = owner + "; " + reason
    return {
        "classification": "technical_record" if technical else "human_capture",
        "metadata_state": metadata_state,
        "compiled": compiled,
        "legacy_disposition": _legacy_disposition(compiled) if compiled is not None else None,
        "legacy_completed": bool(compiled is not None and _legacy_completion(compiled)),
        "reason": reason,
    }
