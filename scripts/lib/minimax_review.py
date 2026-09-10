"""Standalone adversarial code reviewer (Kimi K2.7 by default; historically MiniMax M2.7).

Phase A MVP: working-tree scope, single-shot, JSON output validated against
Codex's review-output schema. Touches nothing in /ship or the codex plugin.
"""

import argparse
import json
import os
import re
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
) -> Path:
    archive_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    out = archive_dir / f"{ts}_{scope}.json"
    record = {
        "timestamp_utc": ts,
        "scope": scope,
        "model": model,
        "usage": usage,
        "parsed": parsed,
        "raw_text": raw_text,
        "prompt_chars": len(prompt),
        "response_id": response.get("id"),
    }
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    return out


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
        )
    except MinimaxError as e:
        print(f"[minimax-review] FAIL: {e}", file=sys.stderr)
        return 2

    raw_text = extract_assistant_text(response)
    usage = extract_token_usage(response)
    parsed = extract_json_object(raw_text)

    archive_dir = Path(args.archive_dir)
    if not args.no_archive:
        out = archive(
            archive_dir,
            scope=args.scope,
            model=args.model,
            parsed=parsed,
            raw_text=raw_text,
            prompt=prompt,
            response=response,
            usage=usage,
        )
        append_usage_log(archive_dir, args.model, args.scope, usage)
        print(f"[minimax-review] archived: {out.relative_to(REPO_ROOT)}", file=sys.stderr)

    if parsed is None:
        print(
            "[minimax-review] could not parse JSON from response. "
            "See archive for raw output.",
            file=sys.stderr,
        )
        if args.json:
            print(json.dumps({"error": "unparseable", "raw": raw_text}, indent=2))
        else:
            print("# kimi-k2.7-code review -- UNPARSEABLE\n")
            print("Raw response (truncated to 4KB):\n")
            print("```\n" + raw_text[:4096] + "\n```")
        return 3

    if args.json:
        print(json.dumps(parsed, indent=2, ensure_ascii=False))
    else:
        print(render_markdown(parsed, args.model, usage))
    return 0


if __name__ == "__main__":
    sys.exit(main())
