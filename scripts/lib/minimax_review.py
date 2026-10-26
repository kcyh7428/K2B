"""Standalone adversarial code reviewer (Kimi K2.7 by default; historically MiniMax M2.7).

Phase A MVP: working-tree scope, single-shot, JSON output validated against
Codex's review-output schema. Touches nothing in /ship or the codex plugin.
"""

import argparse
import json
import math
import os
import re
import secrets
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

from minimax_common import (
    MinimaxError,
    chat_completion,
    extract_assistant_text,
    extract_token_usage,
)

_ACCEPTED_FINISH_REASONS = frozenset({"end_turn", "stop"})


def _type_matches(value: object, expected_type: str) -> bool:
    """Return True when value matches the JSON Schema primitive type.

    JSON booleans are not numbers or integers; the stdlib json module parses
    them as Python bool, which subclasses int, so the explicit bool check must
    come before the numeric checks.
    """
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "number":
        return (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and (not isinstance(value, float) or math.isfinite(value))
        )
    if expected_type == "integer":
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return True
        if isinstance(value, float):
            return math.isfinite(value) and value.is_integer()
        return False
    if expected_type == "null":
        return value is None
    return False


def _json_enum_equal(candidate: object, expected: object) -> bool:
    """Primitive JSON equality with numeric int/float equivalence.

    ``True``/``False`` are never equal to numeric values. Objects and arrays
    are unsupported and always return False (callers reject object/list enum
    entries before reaching membership checks).
    """
    if isinstance(candidate, bool) or isinstance(expected, bool):
        return isinstance(candidate, bool) and isinstance(expected, bool) and candidate == expected
    if isinstance(candidate, (int, float)) and isinstance(expected, (int, float)):
        return candidate == expected
    if isinstance(candidate, (dict, list)) or isinstance(expected, (dict, list)):
        return False
    return candidate == expected


def _validate_schema(
    value: object,
    schema: dict,
    path: str,
    errors: list[str],
) -> None:
    """Recursively validate value against the supported schema subset.

    Supports: type (single string), required, additionalProperties,
    enum, minLength, minimum, maximum, and array items. This is deliberately
    scoped to review-output.schema.json's actual constructs so no schema
    library is required.
    """
    if not isinstance(schema, dict):
        errors.append(f"{path}: schema node is not an object")
        return

    expected_type = schema.get("type")
    if isinstance(expected_type, str):
        if not _type_matches(value, expected_type):
            errors.append(
                f"{path}: expected type {expected_type}, got "
                f"{type(value).__name__}"
            )
            return

    if "enum" in schema:
        enum_values = schema["enum"]
        if not isinstance(enum_values, list):
            errors.append(f"{path}: schema enum is not a list")
        elif not any(_json_enum_equal(value, item) for item in enum_values):
            errors.append(f"{path}: value not in enum {enum_values!r}")

    if isinstance(value, str) and "minLength" in schema:
        min_length = schema["minLength"]
        if isinstance(min_length, int) and not isinstance(min_length, bool):
            if len(value) < min_length:
                errors.append(
                    f"{path}: string length {len(value)} < minLength "
                    f"{min_length}"
                )

    if (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
    ):
        minimum = schema.get("minimum")
        if (
            isinstance(minimum, (int, float))
            and not isinstance(minimum, bool)
            and value < minimum
        ):
            errors.append(f"{path}: value {value} < minimum {minimum}")
        maximum = schema.get("maximum")
        if (
            isinstance(maximum, (int, float))
            and not isinstance(maximum, bool)
            and value > maximum
        ):
            errors.append(f"{path}: value {value} > maximum {maximum}")

    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        if isinstance(required, list):
            for key in required:
                if key not in value:
                    errors.append(f"{path}: missing required field {key!r}")
        additional = schema.get("additionalProperties", True)
        if additional is False and isinstance(properties, dict):
            for key in value:
                if key not in properties:
                    errors.append(f"{path}: unexpected field {key!r}")
        if isinstance(properties, dict):
            for key, child_schema in properties.items():
                if key in value:
                    _validate_schema(
                        value[key], child_schema, f"{path}.{key}", errors
                    )
    elif isinstance(value, list):
        items_schema = schema.get("items")
        if isinstance(items_schema, dict):
            for idx, child in enumerate(value):
                _validate_schema(
                    child, items_schema, f"{path}[{idx}]", errors
                )


def _schema_coverage_error(schema: object) -> str | None:
    """Recursively verify the schema only uses the supported keyword subset.

    Supported subset (deliberately narrow):
      * ``$schema`` (string), ``type`` (single known primitive string),
        ``additionalProperties`` (bool only), ``required`` (list of strings),
        ``properties`` (object of child schemas), ``enum`` (non-empty list of
        primitive JSON values excluding objects/arrays), ``minLength``
        (non-negative int, bool rejected), ``minimum``/``maximum`` (finite
        non-bool numbers), and ``items`` (single schema object).
      * Every ``properties`` child and ``items`` child is walked, so
        unsupported keywords on absent optional branches still fail closed.
      * Composition keywords (``allOf``, ``anyOf``, ``oneOf``, ``not``,
        ``$ref``, etc.) and any unknown keyword are rejected.
      * Object/list ``additionalProperties`` schemas are rejected; only bool
        is accepted. ``items`` array form is rejected.
    Returns None when the schema is fully supported, else a bounded message
    that never includes candidate data.
    """
    allowed = {
        "$schema",
        "type",
        "additionalProperties",
        "required",
        "properties",
        "enum",
        "minLength",
        "minimum",
        "maximum",
        "items",
        # Standard annotation-only keys. They have no evaluation effect in
        # this limited contract (all reference/composition/evaluation
        # constructs remain explicitly unsupported), so allowing them cannot
        # widen accepted shapes; it only stops benign metadata from halting
        # review. Unknown evaluation keywords still fail closed below.
        "title",
        "description",
        "$id",
        "$comment",
        "default",
        "examples",
        "deprecated",
    }
    primitive_types = {
        "object",
        "array",
        "string",
        "integer",
        "number",
        "boolean",
        "null",
    }

    def is_finite_number(node: object) -> bool:
        if isinstance(node, bool) or not isinstance(node, (int, float)):
            return False
        try:
            return node == node and node not in (float("inf"), float("-inf"))
        except (TypeError, ValueError):
            return False

    def walk(node: object, path: str, errors: list[str]) -> None:
        if not isinstance(node, dict):
            errors.append(f"{path}: schema node is not an object")
            return
        if not node:
            # Empty schema node is only meaningful when it is a nested child;
            # the caller validates the root separately. Accept as permissive
            # primitive shape but flag at root for safety.
            if path == "$":
                errors.append("$: empty schema node unsupported")
            return
        for key in node:
            if key not in allowed:
                errors.append(f"{path}: unsupported schema keyword {key!r}")
        if "$schema" in node:
            if not isinstance(node["$schema"], str):
                errors.append(f"{path}.$schema: must be a string")
        if "type" in node:
            type_value = node["type"]
            if not isinstance(type_value, str):
                errors.append(f"{path}.type: must be a single primitive string")
            elif type_value not in primitive_types:
                errors.append(f"{path}.type: unsupported type {type_value!r}")
        if "additionalProperties" in node:
            additional = node["additionalProperties"]
            if not isinstance(additional, bool):
                errors.append(
                    f"{path}.additionalProperties: only boolean values are supported"
                )
        if "required" in node:
            required = node["required"]
            if not isinstance(required, list):
                errors.append(f"{path}.required: must be a list of strings")
            else:
                for idx, entry in enumerate(required):
                    if not isinstance(entry, str):
                        errors.append(
                            f"{path}.required[{idx}]: must be a string"
                        )
        if "enum" in node:
            enum_values = node["enum"]
            if not isinstance(enum_values, list):
                errors.append(f"{path}.enum: must be a list")
            elif not enum_values:
                errors.append(f"{path}.enum: must be a non-empty list")
            else:
                for idx, entry in enumerate(enum_values):
                    if isinstance(entry, (dict, list)):
                        errors.append(
                            f"{path}.enum[{idx}]: object/list enum entries are unsupported"
                        )
                    elif isinstance(entry, float):
                        if not is_finite_number(entry):
                            errors.append(
                                f"{path}.enum[{idx}]: enum numbers must be finite"
                            )
                    elif isinstance(entry, bool):
                        # Booleans are primitive JSON values; allowed in enum.
                        continue
                    elif entry is None or isinstance(entry, (str, int)):
                        continue
                    else:
                        errors.append(
                            f"{path}.enum[{idx}]: unsupported enum value type"
                        )
        if "minLength" in node:
            min_length = node["minLength"]
            if (
                isinstance(min_length, bool)
                or not isinstance(min_length, int)
                or min_length < 0
            ):
                errors.append(
                    f"{path}.minLength: must be a non-negative integer"
                )
        if "minimum" in node:
            minimum = node["minimum"]
            if not is_finite_number(minimum):
                errors.append(f"{path}.minimum: must be a finite number")
        if "maximum" in node:
            maximum = node["maximum"]
            if not is_finite_number(maximum):
                errors.append(f"{path}.maximum: must be a finite number")
        if "properties" in node:
            properties = node["properties"]
            if not isinstance(properties, dict):
                errors.append(f"{path}.properties: must be an object")
            else:
                for key, child in properties.items():
                    if not isinstance(key, str):
                        errors.append(
                            f"{path}.properties: keys must be strings"
                        )
                        continue
                    walk(child, f"{path}.properties.{key}", errors)
        if "items" in node:
            items = node["items"]
            if isinstance(items, list):
                errors.append(
                    f"{path}.items: array form is unsupported"
                )
            elif not isinstance(items, dict):
                errors.append(f"{path}.items: must be a schema object")
            else:
                walk(items, f"{path}.items", errors)

    errors: list[str] = []
    walk(schema, "$", errors)
    if not errors:
        return None
    joined = "; ".join(errors[:8])
    if len(joined) > 800:
        joined = joined[:800] + "..."
    return joined


def validate_review_output(parsed: object, schema: dict) -> str | None:
    """Return None when parsed fully matches schema, else a bounded message."""
    coverage_error = _schema_coverage_error(schema)
    if coverage_error is not None:
        return f"unsupported schema: {coverage_error}"
    errors: list[str] = []
    _validate_schema(parsed, schema, "$", errors)
    if not errors:
        return None
    joined = "; ".join(errors[:8])
    if len(joined) > 800:
        joined = joined[:800] + "..."
    return joined


def _safe_validation_error(exc: object) -> str:
    """Bounded, non-sensitive summary of a validation failure."""
    if isinstance(exc, str):
        text = exc
    else:
        text = f"{type(exc).__name__}: {exc}"
    text = text.replace("\n", " ")
    if len(text) > 800:
        text = text[:800] + "..."
    return text


def extract_finish_reason(response: dict | None) -> str | None:
    """Return the provider finish_reason string, or None when unavailable."""
    if not isinstance(response, dict):
        return None
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    reason = first.get("finish_reason")
    return reason if isinstance(reason, str) else None


def extract_response_model(response: dict | None, requested: str) -> str:
    """Return the actual returned model id, falling back to the requested one."""
    if isinstance(response, dict):
        actual = response.get("model")
        if isinstance(actual, str) and actual:
            return actual
    return requested

LIB_DIR = Path(__file__).resolve().parent
REPO_ROOT = Path(
    subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip()
)
PROMPT_PATH = LIB_DIR / "adversarial-review.md"
SCHEMA_PATH = LIB_DIR / "review-output.schema.json"
DEFAULT_ARCHIVE_DIR = REPO_ROOT / ".minimax-reviews"

MAX_FILE_BYTES = 256 * 1024  # skip large files; M2.7 has 200K context but stay sane
DIFF_SCOPE_OPTIONAL_MARKDOWN_SUFFIXES = {".md"}
DIFF_SCOPE_OPTIONAL_MARKDOWN_BYTES = 128 * 1024
BINARY_SNIFF_BYTES = 4096


def _select_diff_chunk(diff: str, spec: str | None) -> str:
    """Return one exhaustive contiguous line chunk of a unified diff.

    Large review payloads can be rejected by the provider before inference.
    `K2B_REVIEW_DIFF_CHUNK=I/N` lets the ship workflow review the same staged
    path in N auditable calls. The integer partition is gap-free: joining
    chunks 1..N reproduces every original diff line exactly once.
    """
    if not spec:
        return diff
    match = re.fullmatch(r"([1-9][0-9]*)/([1-9][0-9]*)", spec.strip())
    if not match:
        raise ValueError("K2B_REVIEW_DIFF_CHUNK must use I/N positive integers")
    index, total = (int(value) for value in match.groups())
    if index > total:
        raise ValueError("K2B_REVIEW_DIFF_CHUNK index must not exceed total")
    lines = diff.splitlines(keepends=True)
    if not lines:
        raise ValueError("K2B_REVIEW_DIFF_CHUNK cannot partition an empty diff")
    if total > len(lines):
        raise ValueError(
            "K2B_REVIEW_DIFF_CHUNK total must not exceed the diff line count"
        )
    start = len(lines) * (index - 1) // total
    end = len(lines) * index // total
    if end <= start:
        raise ValueError("K2B_REVIEW_DIFF_CHUNK selected an empty chunk")
    marker = (
        f"# K2B_REVIEW_DIFF_CHUNK {index}/{total}: original diff lines "
        f"{start + 1}-{end} of {len(lines)}\n"
    )

    # Integer line partitioning can begin halfway through a file or hunk.
    # Repeat the active attribution headers before the payload so findings in
    # later chunks can still name the correct file and line range. These lines
    # are explicitly outside the payload: only the payload is used for the
    # gap-free/exhaustive coverage contract.
    attribution: list[str] = []
    payload_first = lines[start]
    structural_prefixes = ("diff --git ", "index ", "--- ", "+++ ", "@@ ")
    if start and not payload_first.startswith(structural_prefixes):
        file_header: list[str] = []
        hunk_header: str | None = None
        for line in lines[:start]:
            if line.startswith("diff --git "):
                file_header = [line]
                hunk_header = None
            elif line.startswith("--- ") or line.startswith("+++ "):
                file_header.append(line)
            elif line.startswith("@@ "):
                hunk_header = line
        # Only repeat a complete file header. If the cut falls within header
        # metadata, the structural payload line above suppresses attribution.
        complete_file_header = (
            file_header
            and any(line.startswith("--- ") for line in file_header)
            and any(line.startswith("+++ ") for line in file_header)
        )
        if complete_file_header:
            attribution.extend(file_header)
        if complete_file_header and hunk_header:
            attribution.append(hunk_header)

    context = ""
    if attribution:
        context = (
            "# Attribution context repeated from before this chunk; "
            "not part of the review payload\n"
            + "".join(attribution)
        )
    return (
        marker
        + context
        + "# K2B_REVIEW_DIFF_PAYLOAD_START\n"
        + "".join(lines[start:end])
    )


def run_git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=cwd or REPO_ROOT, text=True, errors="replace"
    )


def is_binary(path: Path) -> bool:
    try:
        chunk = path.read_bytes()[:BINARY_SNIFF_BYTES]
    except OSError:
        return True
    if b"\x00" in chunk:
        return True
    return False


def gather_working_tree_context(
    repo_root: Path | None = None,
) -> tuple[str, list[str]]:
    """Return (context_text, changed_file_list) for working-tree scope.

    Includes:
      - git status --short (overview)
      - diffstat
      - diff vs HEAD for tracked changes
      - full content of each changed/untracked file (truncated if huge)
    """
    root = repo_root or REPO_ROOT
    status = run_git("status", "--short", cwd=root)
    changed_files: list[str] = []
    for line in status.splitlines():
        if not line.strip():
            continue
        # status format: "XY path" or "XY orig -> new"
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        changed_files.append(path.strip().strip('"'))

    if not changed_files:
        return "", []

    diffstat = run_git("diff", "HEAD", "--stat", cwd=root)
    diff = run_git("diff", "HEAD", cwd=root)

    sections: list[str] = []
    sections.append("## git status --short\n```\n" + status.rstrip() + "\n```")
    if diffstat.strip():
        sections.append("## diffstat (HEAD)\n```\n" + diffstat.rstrip() + "\n```")
    if diff.strip():
        sections.append("## diff vs HEAD\n```diff\n" + diff.rstrip() + "\n```")

    sections.append("## Full file contents (changed and untracked)")
    for rel in sorted(set(changed_files)):
        path = root / rel
        if not path.exists():
            sections.append(f"### {rel}\n_(deleted)_")
            continue
        if path.is_dir():
            sections.append(f"### {rel}\n_(directory)_")
            continue
        if is_binary(path):
            sections.append(f"### {rel}\n_(binary, skipped)_")
            continue
        try:
            data = path.read_bytes()
        except OSError as e:
            sections.append(f"### {rel}\n_(unreadable: {e})_")
            continue
        truncated_note = ""
        if len(data) > MAX_FILE_BYTES:
            data = data[:MAX_FILE_BYTES]
            truncated_note = f"\n_(truncated to {MAX_FILE_BYTES} bytes)_"
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
        # Add line numbers so the model can reference line_start / line_end accurately
        numbered = "\n".join(
            f"{i + 1:5d}  {line}" for i, line in enumerate(text.splitlines())
        )
        sections.append(
            f"### {rel}{truncated_note}\n```\n{numbered}\n```"
        )

    return "\n\n".join(sections), sorted(set(changed_files))


def gather_diff_scoped_context(
    files: list[str],
    repo_root: Path | None = None,
    optional_markdown_bytes: int | None = None,
) -> tuple[str, list[str]]:
    """Return (context_text, file_list) restricted to the given files.

    Reviews the staged index when a requested path is staged, or the working
    tree when it is not. A staged path with an unstaged/untracked overlay fails
    closed so the reviewed bytes cannot differ from the bytes later committed.
    Markdown file bodies are optional and share a cumulative budget. Other
    dirty files are not included -- this is the "review only what I asked for"
    gatherer.
    """
    root = repo_root or REPO_ROOT
    if not files:
        return "", []
    files_sorted = sorted(set(files))
    sections: list[str] = []
    optional_markdown_bytes_used = 0
    markdown_budget_warning = False
    markdown_budget = (
        DIFF_SCOPE_OPTIONAL_MARKDOWN_BYTES
        if optional_markdown_bytes is None
        else optional_markdown_bytes
    )
    sections.append("## diff-scoped review (explicit file list)")
    for rel in files_sorted:
        path = root / rel if not Path(rel).is_absolute() else Path(rel)
        try:
            status = run_git("status", "--short", "--", rel, cwd=root).rstrip()
            staged_paths = run_git(
                "diff", "--cached", "--name-only", "--", rel, cwd=root
            ).strip()
            unstaged_paths = run_git(
                "diff", "--name-only", "--", rel, cwd=root
            ).strip()
            untracked_paths = run_git(
                "ls-files", "--others", "--exclude-standard", "--", rel, cwd=root
            ).strip()
        except subprocess.CalledProcessError:
            status = ""
            staged_paths = ""
            unstaged_paths = ""
            untracked_paths = ""

        staged = bool(staged_paths)
        if staged and (unstaged_paths or untracked_paths):
            raise ValueError(
                f"staged path has an unstaged or untracked overlay: {rel}"
            )

        try:
            diff_args = ["diff"]
            if staged:
                diff_args.append("--cached")
            diff_args.extend(["--", rel])
            diff = run_git(*diff_args, cwd=root).rstrip()
            if diff:
                diff = _select_diff_chunk(
                    diff, os.environ.get("K2B_REVIEW_DIFF_CHUNK")
                ).rstrip()
        except subprocess.CalledProcessError:
            diff = ""
        sections.append(f"### {rel}")
        if status:
            sections.append("```\n" + status + "\n```")
        else:
            sections.append("_(no staged or working-tree change)_")
        if diff:
            sections.append("```diff\n" + diff + "\n```")
            if os.environ.get("K2B_REVIEW_DIFF_ONLY") == "1":
                sections.append("_(diff-only review: unchanged file body omitted)_")
                continue
        if staged:
            try:
                data = subprocess.check_output(
                    ["git", "show", f":{rel}"], cwd=root
                )
            except subprocess.CalledProcessError:
                sections.append("_(file absent from staged index)_")
                continue
            if b"\x00" in data[:BINARY_SNIFF_BYTES]:
                sections.append("_(binary staged content, skipped)_")
                continue
        else:
            if not path.exists():
                sections.append("_(file missing from working tree)_")
                continue
            if path.is_dir():
                sections.append("_(directory, skipped)_")
                continue
            if is_binary(path):
                sections.append("_(binary, skipped)_")
                continue
            try:
                data = path.read_bytes()
            except OSError as e:
                sections.append(f"_(unreadable: {e})_")
                continue
        truncated_note = ""
        if len(data) > MAX_FILE_BYTES:
            data = data[:MAX_FILE_BYTES]
            truncated_note = f"\n_(truncated to {MAX_FILE_BYTES} bytes)_"
        if path.suffix.lower() in DIFF_SCOPE_OPTIONAL_MARKDOWN_SUFFIXES:
            if (
                optional_markdown_bytes_used + len(data)
                > markdown_budget
            ):
                sections.append(
                    "_(full file omitted for markdown diff review: markdown full-content budget exhausted; review below is limited to the unified diff)_"
                )
                if not markdown_budget_warning:
                    markdown_budget_warning = True
                    print(
                        "[minimax-review] warning: markdown full-content budget exhausted; "
                        "remaining markdown files are omitted from inline content.",
                        file=sys.stderr,
                    )
                continue
            optional_markdown_bytes_used += len(data)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
        numbered = "\n".join(
            f"{i + 1:5d}  {line}" for i, line in enumerate(text.splitlines())
        )
        sections.append(f"```\n{numbered}\n```{truncated_note}")
    return "\n\n".join(sections), files_sorted


def gather_file_list_context(
    paths: list[str],
    repo_root: Path | None = None,
) -> tuple[str, list[str]]:
    """Return (context_text, file_list) for an explicit list of file paths.

    No git context. Missing files and directories are skipped with a
    stderr warning -- never crash. Useful for ad-hoc "review these files"
    runs not tied to a diff or a plan.
    """
    root = repo_root or REPO_ROOT
    if not paths:
        return "", []
    sections: list[str] = []
    sections.append("## file-list review (no git context)")
    included: list[str] = []
    for rel in paths:
        path = (root / rel) if not Path(rel).is_absolute() else Path(rel)
        if not path.exists():
            print(
                f"[minimax-review] warning: skipping missing file: {rel}",
                file=sys.stderr,
            )
            continue
        if path.is_dir():
            print(
                f"[minimax-review] warning: skipping directory: {rel}",
                file=sys.stderr,
            )
            continue
        if is_binary(path):
            sections.append(f"### {rel}\n_(binary, skipped)_")
            included.append(rel)
            continue
        try:
            data = path.read_bytes()
        except OSError as e:
            sections.append(f"### {rel}\n_(unreadable: {e})_")
            continue
        truncated_note = ""
        if len(data) > MAX_FILE_BYTES:
            data = data[:MAX_FILE_BYTES]
            truncated_note = f"\n_(truncated to {MAX_FILE_BYTES} bytes)_"
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
        numbered = "\n".join(
            f"{i + 1:5d}  {line}" for i, line in enumerate(text.splitlines())
        )
        sections.append(f"### {rel}{truncated_note}\n```\n{numbered}\n```")
        included.append(rel)
    return "\n\n".join(sections), included


WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")

# Path references: matched in three forms (anchored on common punctuation):
#   1. Absolute path: starts with '/', any depth, no extension required
#   2. Relative path with known extension: scripts/foo.py, docs/notes.md
#   3. Top-level filename with known extension: README.md, foo.sh
# Tokens containing '/' but NO known extension (e.g. prose like "gather/run_git",
# "abs/rel") are intentionally NOT matched -- they generated false-positive
# '_(file missing)_' noise on plans containing slash-separated identifiers.
# `K2B-Vault/...` shorthand is NOT specially handled -- callers wanting vault
# files use absolute paths (K2B-Vault is a sibling of the repo, not a subdir).
_PATH_EXT = "py|sh|md|json|ya?ml|toml|js|ts|tsx|jsx|html|css|sql|txt|env"
PATH_REF_RE = re.compile(
    r"(?:^|[\s`(\[<,;])"
    r"("
    r"/(?:[\w.\-]+/)*[\w.\-]+"                                 # absolute path
    r"|"
    r"(?:[\w.\-]+/)+[\w.\-]+\.(?:" + _PATH_EXT + ")"            # rel path + ext
    r"|"
    r"[\w][\w.\-]*\.(?:" + _PATH_EXT + ")"                      # bare filename + ext
    r")"
    r"(?=[\s`)\]>.,;:!?]|$)",
    re.MULTILINE,
)


def _resolve_wikilink(token: str, root: Path) -> Path | None:
    """Resolve a bare [[wikilink]] target by searching wiki/ then raw/.

    Returns the first matching .md file, or repo-root-relative <token>.md as
    a final fallback. None means we couldn't identify any file.
    """
    for subdir in ("wiki", "raw"):
        base = root / subdir
        if not base.is_dir():
            continue
        for ext in (".md", ""):
            for match in base.rglob(f"{token}{ext}"):
                if match.is_file():
                    return match
    candidate = root / f"{token}.md"
    if candidate.is_file():
        return candidate
    return None


def _resolve_path_ref(token: str, root: Path) -> Path:
    """Resolve a path token (abs or rel) to a Path.

    Returns the candidate Path (whether or not it exists). Caller checks
    `.is_file()` -- missing files are marked in the output, never silently
    dropped (per the Phase A 'mark, don't drop' rule).
    """
    if Path(token).is_absolute():
        return Path(token)
    return root / token


def gather_plan_context(
    plan_path: str,
    repo_root: Path | None = None,
) -> tuple[str, list[str]]:
    """Return (context_text, file_list) for a plan file and its references.

    Parses [[wikilinks]] (resolved via wiki/ then raw/ search), inline path
    references (any token containing '/' or ending in a known file extension),
    and absolute paths.

    Failure modes (intentionally distinct):
      - Unparseable wikilink (no file matches the search) -> warn to stderr,
        skip. We can't mark what we couldn't identify.
      - Path ref that resolves to a missing file -> include `### <token>`
        section with `_(file missing)_` marker. Caller knows exactly which
        file was meant; reviewer needs to see the gap.
    """
    root = repo_root or REPO_ROOT
    plan_full = (
        Path(plan_path) if Path(plan_path).is_absolute() else (root / plan_path)
    )
    if not plan_full.is_file():
        raise FileNotFoundError(f"plan not found: {plan_full}")

    plan_text = plan_full.read_text(errors="replace")

    found_refs: list[tuple[str, Path]] = []  # (display_name, real_path)
    missing_refs: list[str] = []  # display_name only
    seen: set[str] = set()

    def _track(display: str, real: Path | None) -> None:
        if display in seen or display == plan_path:
            return
        seen.add(display)
        if real is not None and real.is_file():
            found_refs.append((display, real))
        else:
            missing_refs.append(display)

    for match in WIKILINK_RE.finditer(plan_text):
        token = match.group(1).strip()
        resolved = _resolve_wikilink(token, root)
        if resolved is None:
            print(
                f"[minimax-review] warning: unresolvable wikilink: [[{token}]]",
                file=sys.stderr,
            )
            continue
        try:
            display = str(resolved.relative_to(root))
        except ValueError:
            display = str(resolved)
        _track(display, resolved)

    for match in PATH_REF_RE.finditer(plan_text):
        token = match.group(1).strip()
        resolved = _resolve_path_ref(token, root)
        try:
            display = str(resolved.relative_to(root))
        except ValueError:
            display = token  # absolute path or out-of-tree
        _track(display, resolved if resolved.is_file() else None)

    sections: list[str] = []
    sections.append("## plan-scoped review")
    sections.append(f"### {plan_path} (plan)")
    numbered_plan = "\n".join(
        f"{i + 1:5d}  {line}" for i, line in enumerate(plan_text.splitlines())
    )
    sections.append(f"```\n{numbered_plan}\n```")

    if found_refs or missing_refs:
        sections.append("### Referenced files")
        for display, real in found_refs:
            if is_binary(real):
                sections.append(f"#### {display}\n_(binary, skipped)_")
                continue
            try:
                data = real.read_bytes()
            except OSError as e:
                sections.append(f"#### {display}\n_(unreadable: {e})_")
                continue
            truncated_note = ""
            if len(data) > MAX_FILE_BYTES:
                data = data[:MAX_FILE_BYTES]
                truncated_note = f"\n_(truncated to {MAX_FILE_BYTES} bytes)_"
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = data.decode("utf-8", errors="replace")
            numbered = "\n".join(
                f"{i + 1:5d}  {line}" for i, line in enumerate(text.splitlines())
            )
            sections.append(f"#### {display}{truncated_note}\n```\n{numbered}\n```")
        for display in missing_refs:
            sections.append(f"#### {display}\n_(file missing)_")

    file_list = [plan_path] + [d for d, _ in found_refs] + missing_refs
    return "\n\n".join(sections), file_list


_SECRET_KEY_RE = re.compile(
    r"(?i)(?:^|.*_)(?:"
    r"api_?key|access_key|private_key|access_token|auth_token|bearer_token|refresh_token|token|"
    r"secret|password|passwd|credential|credentials|cookie|authorization"
    r")$"
)
_PLACEHOLDER_RE = re.compile(r"\$\{[A-Z][A-Z0-9_]*\}|\$[A-Z][A-Z0-9_]*")


def _is_secret_key(key: str) -> bool:
    """Match both environment-style and header-style credential names."""
    return bool(_SECRET_KEY_RE.fullmatch(key.replace("-", "_")))


def _redact_review_secrets(content: str) -> str:
    """Remove credential values from review context without hiding config.

    Review diffs routinely contain deleted JSON, shell/env, YAML, and TOML.
    Match credential *field semantics* rather than every occurrence of TOKEN
    so harmless settings such as MAX_TOKENS and TOKENIZERS_PARALLELISM remain
    reviewable. Environment placeholders are configuration, not credentials.
    """
    assignment = re.compile(
        r"(?im)^(?P<prefix>\s*(?:\d+\s{2,})?[+-]?\s*(?:export\s+)?[\"']?"
        r"(?P<key>[A-Z][A-Z0-9_-]*)[\"']?\s*(?::|=)\s*)"
        r"(?P<quote>[\"']?)(?P<value>.*?)(?P=quote)"
        r"(?P<suffix>\s*,?\s*(?:#.*)?)$"
    )

    def redact_assignment(match: re.Match[str]) -> str:
        if not _is_secret_key(match.group("key")):
            return match.group(0)
        value = match.group("value").strip()
        if _PLACEHOLDER_RE.fullmatch(value) or value == "[REDACTED]":
            return match.group(0)
        quote = match.group("quote")
        return (
            match.group("prefix")
            + quote
            + "[REDACTED]"
            + quote
            + match.group("suffix")
        )

    content = assignment.sub(redact_assignment, content)

    # Compact JSON and inline documentation may not place the assignment at
    # the start of a line. Keep delimiters intact while redacting quoted JSON
    # credential fields wherever they appear.
    json_assignment = re.compile(
        r'(?i)(?P<prefix>"(?P<key>[A-Z][A-Z0-9_-]*)"\s*:\s*")'
        r'(?P<value>(?:\\.|[^"\\])*)"'
    )

    def redact_json_assignment(match: re.Match[str]) -> str:
        if not _is_secret_key(match.group("key")):
            return match.group(0)
        if _PLACEHOLDER_RE.fullmatch(match.group("value")):
            return match.group(0)
        return match.group("prefix") + "[REDACTED]" + '"'

    content = json_assignment.sub(redact_json_assignment, content)

    single_quoted_assignment = re.compile(
        r"(?i)(?P<prefix>'(?P<key>[A-Z][A-Z0-9_-]*)'\s*:\s*')"
        r"(?P<value>(?:\\.|[^'\\])*)'"
    )

    def redact_single_quoted_assignment(match: re.Match[str]) -> str:
        if not _is_secret_key(match.group("key")):
            return match.group(0)
        if _PLACEHOLDER_RE.fullmatch(match.group("value")):
            return match.group(0)
        return match.group("prefix") + "[REDACTED]'"

    content = single_quoted_assignment.sub(
        redact_single_quoted_assignment, content
    )

    # Command lines can carry several flags, so they are not line-level
    # assignments. Preserve the flag while replacing its following value.
    cli_flag = re.compile(
        r"(?i)(?P<prefix>--(?:api-?key|access-token|auth-token|bearer-token|"
        r"refresh-token|token|secret|password|credential)(?:=|\s+))"
        r"(?:(?P<double_quote>\")(?P<double_value>(?:\\.|[^\"\\])*)\"|"
        r"(?P<single_quote>')(?P<single_value>(?:\\.|[^'\\])*)'|"
        r"(?P<bare_value>[^\s\"']+))"
    )

    def redact_flag(match: re.Match[str]) -> str:
        value = (
            match.group("double_value")
            or match.group("single_value")
            or match.group("bare_value")
            or ""
        )
        if _PLACEHOLDER_RE.fullmatch(value):
            return match.group(0)
        quote = '"' if match.group("double_quote") else "'" if match.group("single_quote") else ""
        return match.group("prefix") + quote + "[REDACTED]" + quote

    content = cli_flag.sub(redact_flag, content)

    authorization_header = re.compile(
        r"(?i)(?P<prefix>(?:authorization\s*:\s*(?:bearer|basic)\s+|"
        r"(?:x-)?api-key\s*:\s*))"
        r"(?P<value>[^\s\"']+)"
    )
    content = authorization_header.sub(
        lambda match: match.group("prefix") + "[REDACTED]", content
    )

    # Finally catch common self-identifying token formats even when they are
    # embedded in prose or a command whose field name is unavailable.
    token_patterns = (
        r"github_pat_[A-Za-z0-9_]{20,}",
        r"gh[pousr]_[A-Za-z0-9]{20,}",
        r"sk-[A-Za-z0-9_-]{16,}",
    )
    for pattern in token_patterns:
        content = re.sub(pattern, "[REDACTED]", content)
    return content


def build_prompt(target_label: str, focus: str, content: str, schema_text: str) -> str:
    template = PROMPT_PATH.read_text()
    # Deleted configuration can contain old credentials. Never send these to a
    # reviewer, even when the replacement correctly uses an env placeholder.
    content = _redact_review_secrets(content)
    return (
        template.replace("{{TARGET_LABEL}}", target_label)
        .replace("{{USER_FOCUS}}", focus or "No extra focus provided.")
        .replace("{{OUTPUT_SCHEMA}}", schema_text)
        .replace("{{REVIEW_INPUT}}", content)
    )


def extract_json_object(text: str) -> dict | None:
    """Try strict json.loads first, then regex-extract the first {...} block."""
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Kimi wraps JSON in ```json ... ``` fences. Use raw_decode instead of a
    # regex brace-match: raw_decode scans linearly with a real JSON parser,
    # so it cannot catastrophic-backtrack on pathological input (flagged by
    # Kimi itself during the /ship review that introduced this fix).
    fence = re.search(r"```(?:json)?\s*", text)
    if fence:
        rest = text[fence.end():]
        brace = rest.find("{")
        if brace != -1:
            try:
                obj, _ = json.JSONDecoder().raw_decode(rest[brace:])
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                pass
    # Greedy first-{ to last-} fallback
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

# Stable, model-name-independent verdict marker emitted in markdown mode only,
# after the response has passed finish_reason and schema validation. The
# runner never treats this display text as approval authority. It consumes the
# separate attempt result. Value is lowercase to match the review JSON contract.
MACHINE_VERDICT_PREFIX = "K2B_REVIEW_VERDICT:"
MACHINE_VERDICT_VALUES = frozenset({"approve", "needs-attention"})


def render_machine_marker(parsed: dict) -> str:
    """Return the standalone machine verdict marker for validated output."""
    verdict = parsed.get("verdict")
    if verdict not in MACHINE_VERDICT_VALUES:
        raise ValueError("cannot render marker for invalid verdict")
    return f"{MACHINE_VERDICT_PREFIX} {verdict}"


def render_markdown(parsed: dict, model: str, usage: dict) -> str:
    verdict = parsed.get("verdict", "?")
    summary = parsed.get("summary", "(no summary)")
    findings = parsed.get("findings") or []
    next_steps = parsed.get("next_steps") or []

    findings_sorted = sorted(
        findings, key=lambda f: SEVERITY_ORDER.get(f.get("severity", "low"), 99)
    )

    badge = "APPROVE" if verdict == "approve" else "NEEDS-ATTENTION"
    lines: list[str] = []
    lines.append(f"# {model} review -- {badge}")
    lines.append("")
    lines.append(f"**Summary:** {summary}")
    lines.append("")
    lines.append(
        f"**Tokens:** prompt={usage.get('prompt_tokens')}  "
        f"completion={usage.get('completion_tokens')}  "
        f"total={usage.get('total_tokens')}"
    )
    lines.append("")
    if not findings_sorted:
        lines.append("_No findings._")
    else:
        lines.append(f"## Findings ({len(findings_sorted)})")
        lines.append("")
        for i, f in enumerate(findings_sorted, 1):
            sev = (f.get("severity") or "?").upper()
            conf = f.get("confidence")
            conf_pct = f"{int(conf * 100)}%" if isinstance(conf, (int, float)) else "?"
            lines.append(
                f"### {i}. [{sev}] {f.get('title', '(untitled)')}  ({conf_pct} conf)"
            )
            lines.append(
                f"`{f.get('file', '?')}` lines "
                f"{f.get('line_start', '?')}-{f.get('line_end', '?')}"
            )
            lines.append("")
            lines.append(f.get("body", ""))
            rec = f.get("recommendation")
            if rec:
                lines.append("")
                lines.append(f"**Recommendation:** {rec}")
            lines.append("")
    if next_steps:
        lines.append("## Next steps")
        for step in next_steps:
            lines.append(f"- {step}")
        lines.append("")
    lines.append("---")
    lines.append(render_machine_marker(parsed))
    lines.append("")
    return "\n".join(lines)


def archive(
    archive_dir: Path,
    *,
    scope: str,
    model: str,
    parsed: dict | None,
    raw_text: str,
    prompt: str,
    response: dict,
    usage: dict,
    actual_model: str | None = None,
    finish_reason: str | None = None,
    requested_max_tokens: int | None = None,
    final_text_length: int | None = None,
    validation_error: str | None = None,
) -> Path:
    archive_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    record = {
        "timestamp_utc": ts,
        "scope": scope,
        "model": actual_model or model,
        "requested_model": model,
        "usage": usage,
        "parsed": parsed,
        "raw_text": raw_text,
        "prompt_chars": len(prompt),
        "response_id": response.get("id") if isinstance(response, dict) else None,
        "finish_reason": finish_reason,
        "requested_max_tokens": requested_max_tokens,
        "final_text_length": (
            final_text_length
            if final_text_length is not None
            else len(raw_text or "")
        ),
        "validation_error": validation_error,
    }
    payload = json.dumps(record, indent=2, ensure_ascii=False)
    # Prefer the canonical {ts}_{scope}.json name. Open with O_EXCL so a
    # pre-existing file is never overwritten; only on FileExistsError do we
    # add a random suffix (and retry) to preserve collision safety and mode.
    for _ in range(16):
        suffix = ""
        if _:
            suffix = "_" + secrets.token_hex(4)
        out = archive_dir / f"{ts}_{scope}{suffix}.json"
        try:
            fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        return out
    raise RuntimeError(f"could not create unique archive file in {archive_dir}")


def _display_archive_path(path: Path) -> str:
    """Render an archive path relative to REPO_ROOT when possible."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def append_usage_log(archive_dir: Path, model: str, scope: str, usage: dict) -> None:
    archive_dir.mkdir(parents=True, exist_ok=True)
    log = archive_dir / "usage.log"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = (
        f"{ts}\t{model}\t{scope}\t"
        f"prompt={usage.get('prompt_tokens')}\t"
        f"completion={usage.get('completion_tokens')}\t"
        f"total={usage.get('total_tokens')}\n"
    )
    with log.open("a") as f:
        f.write(line)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Standalone Kimi K2.7 Code adversarial code reviewer."
    )
    parser.add_argument(
        "--scope",
        default="working-tree",
        choices=["working-tree", "diff", "plan", "files"],
        help=(
            "Context gatherer: 'working-tree' (default, Phase A behavior), "
            "'diff' (only --files paths + their diffs), "
            "'plan' (--plan path + files it references), "
            "'files' (just --files paths, no git context)"
        ),
    )
    parser.add_argument(
        "--plan",
        default=None,
        help="Plan file path (required when --scope plan)",
    )
    parser.add_argument(
        "--files",
        default=None,
        help="Comma-separated list of paths (required when --scope diff or files)",
    )
    # Default model tracks the active provider: Kimi's kimi-k2.7-code when
    # K2B_LLM_PROVIDER=kimi (current default). MiniMax is not a live rollback
    # path; callers can still pass --model to override Kimi model variants.
    provider = os.environ.get("K2B_LLM_PROVIDER", "kimi").lower()
    if provider not in {"kimi", "minimax"}:
        print(
            f"[minimax-review] unsupported provider: {provider!r}; "
            "expected 'kimi'.",
            file=sys.stderr,
        )
        return 1
    if provider == "minimax":
        print(
            "[minimax-review] K2B_LLM_PROVIDER=minimax is deprecated and "
            "disabled (MiniMax subscription expired). Set "
            "K2B_LLM_PROVIDER=kimi.",
            file=sys.stderr,
        )
        return 1
    if provider == "kimi":
        _default_model = os.environ.get("K2B_LLM_MODEL", "kimi-k2.7-code")
        if not _default_model.lower().startswith("kimi-"):
            print(
                "[minimax-review] K2B_LLM_MODEL must be a Kimi model id "
                "when K2B_LLM_PROVIDER=kimi.",
                file=sys.stderr,
            )
            return 1
    parser.add_argument(
        "--model",
        default=_default_model,
        help=f"Model id (default {_default_model})",
    )
    parser.add_argument(
        "--focus",
        default="",
        help="Optional focus text passed into the adversarial template",
    )
    parser.add_argument(
        "--builder-family",
        choices=["openai", "anthropic", "kimi", "other"],
        default=None,
        help=(
            "Optional /ship audit metadata. Rejects Kimi-built diffs because "
            "the direct Kimi reviewer is not independent for that family."
        ),
    )
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="Audit flag for official /ship calls; direct Kimi has no fallback.",
    )
    parser.add_argument(
        "--other-reviewer-reason",
        default=None,
        help="Audit reason when --builder-family other chooses direct Kimi.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=16384,
        help="Max completion tokens (default 16384; 4096 truncates rich reviews)",
    )
    parser.add_argument(
        "--archive-dir",
        default=str(DEFAULT_ARCHIVE_DIR),
        help="Where to archive raw + parsed output",
    )
    parser.add_argument(
        "--no-archive",
        action="store_true",
        help="Skip writing the archive file",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit parsed JSON to stdout instead of rendered markdown",
    )
    args = parser.parse_args()
    if args.builder_family == "kimi":
        print(
            "[minimax-review] builder-family kimi cannot be reviewed by Kimi",
            file=sys.stderr,
        )
        return 1
    if args.builder_family == "openai" and not args.no_fallback:
        print(
            "[minimax-review] builder-family openai requires --no-fallback",
            file=sys.stderr,
        )
        return 1
    if args.builder_family == "other" and not args.other_reviewer_reason:
        print(
            "[minimax-review] builder-family other requires --other-reviewer-reason",
            file=sys.stderr,
        )
        return 1
    if args.builder_family and os.environ.get("K2B_REVIEW_DIFF_CHUNK"):
        print(
            "[minimax-review] K2B_REVIEW_DIFF_CHUNK is not allowed for an "
            "official builder-family review; split the explicit file list "
            "instead so every selected diff is reviewed.",
            file=sys.stderr,
        )
        return 1

    schema_text = SCHEMA_PATH.read_text()

    print(f"[minimax-review] gathering {args.scope} context...", file=sys.stderr)
    if args.scope == "working-tree":
        context, changed = gather_working_tree_context()
        if not changed:
            print(
                "[minimax-review] no working-tree changes; nothing to review.",
                file=sys.stderr,
            )
            return 0
    elif args.scope == "diff":
        if not args.files:
            print("[minimax-review] --scope diff requires --files", file=sys.stderr)
            return 1
        file_list = [p.strip() for p in args.files.split(",") if p.strip()]
        if not file_list:
            print(
                "[minimax-review] --scope diff: --files parsed to empty list",
                file=sys.stderr,
            )
            return 1
        try:
            context, changed = gather_diff_scoped_context(file_list)
        except ValueError as exc:
            print(f"[minimax-review] diff scope refused: {exc}", file=sys.stderr)
            return 1
    elif args.scope == "plan":
        if not args.plan:
            print("[minimax-review] --scope plan requires --plan", file=sys.stderr)
            return 1
        try:
            context, changed = gather_plan_context(args.plan)
        except FileNotFoundError as e:
            print(f"[minimax-review] {e}", file=sys.stderr)
            return 1
    elif args.scope == "files":
        if not args.files:
            print("[minimax-review] --scope files requires --files", file=sys.stderr)
            return 1
        file_list = [p.strip() for p in args.files.split(",") if p.strip()]
        if not file_list:
            print(
                "[minimax-review] --scope files: --files parsed to empty list",
                file=sys.stderr,
            )
            return 1
        context, changed = gather_file_list_context(file_list)
    else:
        print(f"[minimax-review] unknown scope: {args.scope}", file=sys.stderr)
        return 1
    print(
        f"[minimax-review] {len(changed)} changed files, "
        f"{len(context)} chars of context",
        file=sys.stderr,
    )

    if args.scope == "working-tree":
        # Phase A wording preserved verbatim -- byte-for-byte back-compat for
        # the prompt MiniMax sees. Do not alter.
        target_label = (
            f"working tree of {REPO_ROOT.name} ({len(changed)} files changed)"
        )
    elif args.scope == "diff":
        target_label = (
            f"diff-scoped review of {REPO_ROOT.name} ({len(changed)} files)"
        )
    elif args.scope == "plan":
        target_label = f"plan {args.plan} ({len(changed)} files referenced)"
    else:  # files
        target_label = (
            f"explicit file list ({len(changed)} files, repo {REPO_ROOT.name})"
        )
    prompt = build_prompt(target_label, args.focus, context, schema_text)

    review_content_sha256: str | None = None
    try:
        from review_result import write_review_input
        review_content_sha256 = write_review_input(prompt)
    except Exception as exc:
        print(
            "[minimax-review] FAIL: review input snapshot failed "
            f"({type(exc).__name__})",
            file=sys.stderr,
        )
        return 3

    print(
        f"[minimax-review] calling {args.model} ({len(prompt)} prompt chars)...",
        file=sys.stderr,
    )
    try:
        response = chat_completion(
            model=args.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=args.max_tokens,
            temperature=0.2,
            strict_finish_reason=True,
        )
    except MinimaxError as e:
        print(f"[minimax-review] FAIL: {e}", file=sys.stderr)
        return 2

    raw_text = extract_assistant_text(response)
    usage = extract_token_usage(response)
    finish_reason = extract_finish_reason(response)
    actual_model = extract_response_model(response, args.model)
    final_text_length = len(raw_text or "")

    validation_error: str | None = None
    parsed: dict | None = None
    if finish_reason is None:
        validation_error = "missing finish_reason"
    elif finish_reason not in _ACCEPTED_FINISH_REASONS:
        validation_error = f"unexpected finish_reason {finish_reason!r}"
    elif not isinstance(raw_text, str) or not raw_text.strip():
        validation_error = "blank final text"
    else:
        try:
            candidate = extract_json_object(raw_text)
        except Exception as exc:  # pragma: no cover - defensive
            candidate = None
            validation_error = _safe_validation_error(exc)
        if validation_error is None:
            if candidate is None:
                validation_error = "unparseable JSON"
            else:
                try:
                    schema = json.loads(SCHEMA_PATH.read_text())
                except Exception as exc:  # pragma: no cover - defensive
                    schema = None
                    validation_error = _safe_validation_error(exc)
                if schema is not None:
                    schema_error = validate_review_output(candidate, schema)
                    if schema_error is not None:
                        validation_error = schema_error
                    else:
                        parsed = candidate
                        for _finding in parsed.get("findings") or []:
                            for _line_key in ("line_start", "line_end"):
                                _line_val = _finding.get(_line_key)
                                if isinstance(_line_val, float) and _line_val.is_integer():
                                    _finding[_line_key] = int(_line_val)

    archive_dir = Path(args.archive_dir)
    if not args.no_archive:
        try:
            out = archive(
                archive_dir,
                scope=args.scope,
                model=args.model,
                parsed=parsed,
                raw_text=raw_text or "",
                prompt=prompt,
                response=response,
                usage=usage,
                actual_model=actual_model,
                finish_reason=finish_reason,
                requested_max_tokens=args.max_tokens,
                final_text_length=final_text_length,
                validation_error=validation_error,
            )
            append_usage_log(archive_dir, actual_model, args.scope, usage)
        except (OSError, RuntimeError) as exc:
            print(
                "[minimax-review] FAIL: archive/usage-log failed "
                f"({type(exc).__name__})",
                file=sys.stderr,
            )
            return 3
        print(
            f"[minimax-review] archived: {_display_archive_path(out)}",
            file=sys.stderr,
        )

    if validation_error is None and parsed is not None:
        try:
            from review_result import write_attempt_result
            write_attempt_result(
                parsed,
                finish_reason=finish_reason,
                model=actual_model,
                content_sha256=review_content_sha256,
            )
        except Exception as exc:
            print(
                "[minimax-review] FAIL: attempt result write failed "
                f"({type(exc).__name__})",
                file=sys.stderr,
            )
            parsed = None
            validation_error = "attempt result write failed"

    if validation_error is not None or parsed is None:
        diag = (
            f"finish={finish_reason!r} model={actual_model!r} "
            f"max_tokens={args.max_tokens} text_len={final_text_length} "
            f"usage={{\n  prompt={usage.get('prompt_tokens')} "
            f"completion={usage.get('completion_tokens')} "
            f"total={usage.get('total_tokens')}}} "
            f"validation_error={validation_error!r}"
        )
        print(f"[minimax-review] FAIL: {diag}", file=sys.stderr)
        if args.json:
            print(json.dumps({
                "error": "invalid_review",
                "finish_reason": finish_reason,
                "model": actual_model,
                "requested_max_tokens": args.max_tokens,
                "final_text_length": final_text_length,
                "usage": usage,
                "validation_error": validation_error,
            }, indent=2))
        else:
            print("# kimi-k2.7-code review -- UNPARSEABLE\n")
            print(
                "Reviewer response did not satisfy the review-output contract \n"
                f"(finish={finish_reason!r}, model={actual_model!r}, "
                f"max_tokens={args.max_tokens}, text_len={final_text_length})."
            )
            print()
            print(
                "A transport success is not an approving verdict. Inspect the "
                "archived evidence, then rerun explicitly with an adequate "
                "max_tokens and deadline; do not treat this failure as approval."
            )
        return 3

    if args.json:
        print(json.dumps(parsed, indent=2, ensure_ascii=False))
    else:
        print(render_markdown(parsed, actual_model, usage))
    return 0


if __name__ == "__main__":
    sys.exit(main())
