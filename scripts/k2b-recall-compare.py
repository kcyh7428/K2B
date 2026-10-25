#!/usr/bin/env python3
"""Run the offline recall comparison against a fixed private vault snapshot."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import k2b_context
import recall_experiment


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--baseline-module", type=Path, required=True,
                        help="trusted unmodified local k2b_context.py saved before the repair")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--descriptions", type=Path, required=True)
    parser.add_argument("--split", choices=["diagnostic", "heldout", "all"], default="diagnostic")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--packet-dir", type=Path)
    args = parser.parse_args()
    cli = _load(ROOT / "scripts/k2b-context.py", "context_cli")
    cli._reject_unsafe_output(args.output, args.vault)
    if args.packet_dir:
        cli._reject_unsafe_output(args.packet_dir, args.vault)
    cases = json.loads(args.cases.read_text())
    if args.split == "all":
        selected = cases
    elif args.split == "heldout":
        selected = {g: rows for g, rows in cases.items() if g == "heldout"}
    else:
        selected = {g: rows for g, rows in cases.items() if g != "heldout"}
    descriptions = recall_experiment.load_descriptions(json.loads(args.descriptions.read_text()), args.vault)
    baseline = _load(args.baseline_module, "baseline_context")

    def save_packet(variant, case, packet):
        if args.packet_dir:
            path = args.packet_dir / variant / (case + ".json")
            cli._reject_unsafe_output(path, args.vault)
            cli._write_private_json(path, packet)

    inputs = {name: hashlib.sha256(path.read_bytes()).hexdigest()
                        for name, path in [("cases", args.cases), ("descriptions", args.descriptions),
                                           ("baseline", args.baseline_module), ("repaired", ROOT/"scripts/lib/k2b_context.py")]}
    report = recall_experiment.compare(baseline, k2b_context, args.vault, selected,
                                       descriptions=descriptions, packet_sink=save_packet)
    if hashlib.sha256((ROOT/"scripts/lib/k2b_context.py").read_bytes()).hexdigest() != inputs["repaired"]:
        raise ValueError("retrieval implementation changed during comparison; rerun the fixed candidate")
    report["inputs"] = inputs
    cli._write_private_json(args.output, report)
    for variant in ["baseline", "repaired", "bm25", "repaired_summaries", "bm25_summaries"]:
        rows = [r for r in report["results"] if r["variant"] == variant]
        print(json.dumps({"variant": variant, "source_hits": sum(r["source_hit"] for r in rows),
                          "cases": len(rows), "bindings_valid": sum(r["binding_valid"] for r in rows),
                          "passage_checks": sum(r["passage_check"] is True for r in rows)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
