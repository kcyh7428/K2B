#!/usr/bin/env python3
"""CLI for bounded, source-linked resume-brief preparation.

The current conversational Codex model produces the semantic draft; this CLI
gathers bounded evidence, validates the draft against the packet, and renders
the brief.  It never calls a provider, writes the vault or launches another
agent, and every artifact it writes stays machine-local.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import k2b_context  # noqa: E402
from k2b_bootstrap import default_read_vault, read_copy_status, read_copy_health_notice  # noqa: E402


def _default_vault() -> Path:
    try:
        return default_read_vault()
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise k2b_context.InvalidInput(
            "invalid_read_setting",
            "Check ~/.local/state/k2b/read-vault.json and its local root; "
            "or use --vault with the approved NAS alias after mount preflight.",
        ) from exc


def _default_state_root() -> Path:
    return Path(
        os.environ.get(
            "K2B_AUTOMATIC_MEMORY_STATE_ROOT",
            str(Path.home() / ".local" / "state" / "k2b" / "automatic-memory"),
        )
    ).expanduser()


def _default_coverage_receipt() -> Path:
    return Path(
        os.environ.get(
            "K2B_CAPTURE_STATUS_FILE",
            str(Path.home() / ".local" / "state" / "k2b" / "capture-status.json"),
        )
    ).expanduser()


def _reject_unsafe_output(path: Path, vault: Path) -> None:
    """Keep explicit packet/brief/render output machine-local and non-escaping.

    The derived artifacts are private, disposable and must never land in the
    vault or follow a symlink/parent escape into it.  A literal ``..`` part, a
    symlinked final path, or a resolved path inside the vault all fail closed.
    """

    raw = Path(path).expanduser()
    if any(part == ".." for part in raw.parts):
        raise k2b_context.InvalidInput(
            "output_not_private", "output path must not contain '..' segments"
        )
    if raw.is_symlink():
        raise k2b_context.InvalidInput(
            "output_not_private", "output path must not be a symlink"
        )
    copy_root = Path.home() / "Projects/K2B-Vault-Offline/SynologyDrive"
    protected_paths = (Path.home() / "Projects/K2B-Vault", Path.home() / "Projects/K2B-NAS/K2B-Vault",
                       copy_root, Path("/Volumes"))
    def within(path, roots):
        folded = str(path).casefold()
        return any(folded == str(root).casefold() or folded.startswith(str(root).casefold() + "/")
                   for root in roots)
    # Refuse a supplied NAS alias before resolution can erase its spelling.
    # Fixed physical bindings also catch outputs reached through other links.
    if within(raw.absolute(), protected_paths):
        raise k2b_context.InvalidInput("output_not_private", "output artifact must stay outside the vault")
    try:
        resolved = raw.resolve()
        vault_resolved = Path(vault).expanduser().resolve()
    except OSError as exc:
        raise k2b_context.InvalidInput(
            "output_not_private", "output path is not resolvable"
        ) from exc
    protected = (vault_resolved, copy_root.resolve(), *protected_paths)
    if within(resolved, protected):
        raise k2b_context.InvalidInput(
            "output_not_private", "output artifact must stay outside the vault"
        )


def _write_private_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _read_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise k2b_context.InvalidInput(
            "unreadable_input", f"{label} is unreadable or malformed: {path}"
        ) from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="K2B resume-brief preparation")
    sub = parser.add_subparsers(dest="cmd", required=True)

    gather = sub.add_parser("gather", help="bounded, read-only evidence gathering")
    gather.add_argument("--query", required=True)
    gather.add_argument("--topic-note", default=None)
    gather.add_argument(
        "--raw-capture",
        action="append",
        default=[],
        help="vault-relative raw capture explicitly selected for this topic",
    )
    gather.add_argument("--vault", type=Path)
    gather.add_argument("--published-memory", type=Path, default=None)
    gather.add_argument("--semantic-shelf", type=Path, default=None)
    gather.add_argument(
        "--provisional-root",
        type=Path,
        default=_default_state_root(),
        help="machine-local automatic-memory state root holding the outbox",
    )
    gather.add_argument(
        "--no-provisional-memory",
        action="store_true",
        help="opt out of reading the local provisional outbox",
    )
    gather.add_argument("--coverage-receipt", type=Path, default=None)
    gather.add_argument(
        "--extra-term",
        action="append",
        default=[],
        help="one bounded follow-up term suggested by the model",
    )
    gather.add_argument("--now")
    gather.add_argument("--max-sources", type=int, default=k2b_context.MAX_SOURCES)
    gather.add_argument("--max-evidence", type=int, default=k2b_context.MAX_EVIDENCE)
    gather.add_argument("--max-bytes", type=int, default=k2b_context.MAX_PACKET_BYTES)
    gather.add_argument("--output", type=Path, required=True)

    validate = sub.add_parser("validate", help="validate a draft against its packet")
    validate.add_argument("--packet", type=Path, required=True)
    validate.add_argument("--draft", type=Path, required=True)
    validate.add_argument("--vault", type=Path)
    validate.add_argument("--published-memory", type=Path, default=None)
    validate.add_argument(
        "--provisional-root",
        type=Path,
        default=_default_state_root(),
        help="machine-local automatic-memory state root holding the outbox",
    )
    validate.add_argument(
        "--no-provisional-memory",
        action="store_true",
        help="opt out of verifying against the local provisional outbox",
    )
    validate.add_argument("--now")
    validate.add_argument("--output", type=Path, required=True)

    render = sub.add_parser("render", help="render a validated brief")
    render.add_argument("--brief", type=Path, required=True)
    render.add_argument("--format", choices=("markdown", "json"), default="markdown")
    render.add_argument("--vault", type=Path)
    render.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.vault is None:
            args.vault = _default_vault()
        read_status = read_copy_status(args.vault)
        if read_status:
            read_notice = "Reading Synology Drive local copy; it may lag NAS. Last NAS comparison: " + str(
                read_status["last_nas_comparison_utc"] or "unverified")
            if read_status["setting_invalid"]:
                read_notice += ". Configured read setting is invalid; check ~/.local/state/k2b/read-vault.json."
            read_notice += ". " + read_copy_health_notice(read_status)
            print(read_notice, file=sys.stderr)
        if args.cmd == "gather":
            if args.max_sources < 0 or args.max_evidence < 0 or args.max_bytes < 1024:
                raise k2b_context.InvalidInput(
                    "invalid_bounds", "gather bounds must be positive"
                )
            _reject_unsafe_output(args.output, args.vault)
            provisional_root = (
                None if args.no_provisional_memory else args.provisional_root
            )
            packet = k2b_context.gather(
                args.query,
                args.vault,
                topic_note=args.topic_note,
                raw_sources=args.raw_capture,
                published_memory=args.published_memory,
                semantic_shelf=args.semantic_shelf,
                provisional_root=provisional_root,
                coverage_receipt=args.coverage_receipt or _default_coverage_receipt(),
                coverage_state_root=args.provisional_root,
                extra_terms=args.extra_term,
                now=args.now,
                max_sources=args.max_sources,
                max_evidence=args.max_evidence,
                max_bytes=args.max_bytes,
            )
            _write_private_json(args.output, packet)
            print(
                json.dumps(
                    {
                        "status": packet["status"],
                        "packet_id": packet["packet_id"],
                        "output": str(args.output),
                        "sources": packet["coverage"]["sources"],
                        "evidence": packet["coverage"]["evidence"],
                        "partial": packet["partial"],
                        "reasons": packet["reasons"],
                        "read_copy": read_copy_status(args.vault),
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.cmd == "validate":
            _reject_unsafe_output(args.output, args.vault)
            packet = _read_json(args.packet, "packet")
            draft = _read_json(args.draft, "draft")
            provisional_root = (
                None if args.no_provisional_memory else args.provisional_root
            )
            brief = k2b_context.validate_draft(
                packet,
                draft,
                vault_root=args.vault,
                published_memory=args.published_memory,
                provisional_root=provisional_root,
                now=args.now,
            )
            _write_private_json(args.output, brief)
            print(
                json.dumps(
                    {
                        "status": "validated",
                        "brief_id": brief["brief_id"],
                        "packet_id": brief["packet_id"],
                        "output": str(args.output),
                        "warnings": brief["warnings"],
                        "partial": brief["partial"],
                        "read_copy": read_status,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.cmd == "render":
            brief = _read_json(args.brief, "brief")
            # Resolve verified vault-relative citation targets against this
            # Mac's vault so the desktop chat receives absolute local links.
            text = k2b_context.render_brief(
                brief, fmt=args.format, vault_root=args.vault
            )
            if read_status and args.format == "markdown":
                text += "\n" + read_notice + "\n"
            if args.output:
                _reject_unsafe_output(args.output, args.vault)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(text, encoding="utf-8")
                os.chmod(args.output, 0o600)
            else:
                sys.stdout.write(text)
            return 0
    except k2b_context.ContextError as exc:
        print(
            json.dumps(
                {"status": "error", "reason": exc.reason, "detail": exc.detail},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return exc.exit_code
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "reason": "unexpected_error",
                    "detail": f"{type(exc).__name__}",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
