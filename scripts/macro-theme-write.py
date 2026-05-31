#!/usr/bin/env python3
"""Atomically write an agent-generated Chat-2 macro-theme into K2Bi-Vault.

Codex Checkpoint-2 F5: the conductor's prose `flock` was theatre -- a bash lock
that releases before the separate Write-tool edits run, so two concurrent Chat-2
runs could derive the same `_2` slug or race the index append. This helper holds
a SINGLE fcntl.flock across slug-derivation + atomic theme write + index-row
append, so the whole critical section is serialized.

Usage:
    macro-theme-write.py <content-file> <slug-base>

<content-file> : a temp file containing the FULL theme markdown (frontmatter +
                 body), written by the agent's Write tool.
<slug-base>    : a human phrase for the slug/title (e.g. the distilled topic).

Prints the final theme path (with any _2/_3 auto-version suffix) to stdout.
Never overwrites an existing theme. Honors K2BI_VAULT_PATH.

Gates the content with `_verify_theme_gate` BEFORE publishing: a theme that
fails the gate is never moved to `theme_<slug>.md` and never appended to the
index, so a bad theme can never become a visible/K2Bi-promotable artifact
(Codex Checkpoint-2 r4). Exit codes: 0 = published, 2 = usage, 4 = gate
rejected (nothing published/indexed), 1 = other error.
"""
import fcntl
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from orchestrator_store import _verify_theme_gate  # noqa: E402

LOCK_PATH = "/tmp/k2b-orch-macro-themes.lock"


def _slugify(s: str) -> str:
    words = re.findall(r"[a-z0-9]+", s.lower())[:6]
    return "-".join(words) or "theme"


def _fm_value(content: str, key: str) -> str | None:
    """First-line value of a top-level frontmatter key (good enough for the
    single-line `date` / `candidate-count` fields the index row needs)."""
    m = re.search(rf"(?m)^{re.escape(key)}:[ \t]*(.+)$", content)
    return m.group(1).strip() if m else None


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: macro-theme-write.py <content-file> <slug-base>", file=sys.stderr)
        return 2
    content_file, slug_base = sys.argv[1], sys.argv[2]
    vault = os.environ.get("K2BI_VAULT_PATH") or os.path.expanduser("~/Projects/K2Bi-Vault")
    macro = os.path.join(vault, "wiki", "macro-themes")
    if not os.path.isdir(macro):
        print(f"macro-themes dir missing: {macro}", file=sys.stderr)
        return 1
    try:
        with open(content_file, encoding="utf-8") as f:
            content = f.read()
    except OSError as exc:
        print(f"content file unreadable: {exc}", file=sys.stderr)
        return 1

    lf = open(LOCK_PATH, "w")
    fcntl.flock(lf, fcntl.LOCK_EX)
    try:
        base = _slugify(slug_base)
        slug, n = base, 1
        while os.path.exists(os.path.join(macro, f"theme_{slug}.md")):
            n += 1
            slug = f"{base}_{n}"
        theme_path = os.path.join(macro, f"theme_{slug}.md")

        fd, tmp = tempfile.mkstemp(prefix=".tmp_theme_", suffix=".md", dir=macro)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            # Codex Checkpoint-2 r4 (HIGH): gate the content BEFORE publishing.
            # The old order published `theme_<slug>.md` + appended the index row
            # FIRST, then `verify-theme` validated -- so a theme that fails the
            # gate (extra unsupported body ticker, no body table, ARK/ledger
            # mismatch) still landed as a visible, K2Bi-promotable `candidates-
            # pending-review` row. The orchestrator rejection only blocked task
            # completion, not Keith promoting the displayed-only bad row. Run the
            # SAME gate on the temp file (which is `.tmp_theme_*.md` -- neither
            # `theme_*.md`-named nor index-referenced, so invisible to promotion)
            # and refuse to publish/index on failure. `verify-theme` stays as the
            # authoritative task-completion gate (defense in depth, same fn).
            ok, reason = _verify_theme_gate(tmp)
            if not ok:
                os.unlink(tmp)
                print(f"theme gate REJECTED (not published, not indexed): {reason}", file=sys.stderr)
                return 4
            os.replace(tmp, theme_path)
        except Exception:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

        date = _fm_value(content, "date") or ""
        cc = _fm_value(content, "candidate-count") or "?"
        title = re.sub(r"\s+", " ", slug_base).strip()[:90] or slug
        # Match the existing macro-themes index row shape.
        row = f"| [[theme_{slug}\\|{title}]] | {date} | {cc} | candidates-pending-review |\n"
        with open(os.path.join(macro, "index.md"), "a", encoding="utf-8") as f:
            f.write(row)

        print(theme_path)
        return 0
    finally:
        fcntl.flock(lf, fcntl.LOCK_UN)
        lf.close()


if __name__ == "__main__":
    sys.exit(main())
