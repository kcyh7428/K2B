#!/usr/bin/env python3
"""OCR accuracy gate (Ship 1B).

Runs every image in tests/washing-machine/fixtures/images/ocr-expected.json
through scripts/minimax-vlm.sh and computes per-image field-match accuracy.

Field match = case-insensitive substring match of the expected value in the
OCR content string. Per-image accuracy = matched_fields / total_fields.
Corpus accuracy = mean of per-image accuracies. Exits 0 if corpus accuracy
>= threshold (default 0.80 from ocr-expected.json). Exit 1 otherwise.

Offline: set MINIMAX_VLM_MOCK to point at a JSON file containing a single
`{"base_resp":{"status_code":0},"content":"..."}` payload. Every image call
returns that same content, so the test script can seed a content string that
passes the gate deterministically.

Env:
  MINIMAX_VLM_MOCK  optional. See above.
  MINIMAX_API_KEY   required if MINIMAX_VLM_MOCK is not set (real VLM call).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FIXDIR = REPO / "tests/washing-machine/fixtures/images"
EXPECTED_PATH = FIXDIR / "ocr-expected.json"
VLM_SCRIPT = REPO / "scripts/minimax-vlm.sh"

OCR_PROMPT = (
    "Transcribe every field on this business card. Return plain text, "
    "one field per line as Key: Value. Include both English and Chinese "
    "text if present. Be literal: no interpretation, no commentary."
)


def run_vlm(image_path: Path, job_name: str) -> str:
    result = subprocess.run(
        [
            str(VLM_SCRIPT),
            "--image", str(image_path),
            "--prompt", OCR_PROMPT,
            "--job-name", job_name,
            "--fallback", "never",
        ],
        capture_output=True, text=True, timeout=90,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"minimax-vlm.sh exited {result.returncode} for {image_path.name}: "
            f"{result.stderr.strip()}"
        )
    return result.stdout


def score_image(ocr_text: str, expected_fields: dict[str, str]) -> tuple[int, int]:
    matched = 0
    total = len(expected_fields)
    ocr_lower = ocr_text.lower()
    for _key, value in expected_fields.items():
        if str(value).lower() in ocr_lower:
            matched += 1
    return matched, total


def main() -> int:
    if not EXPECTED_PATH.exists():
        print(f"FATAL: {EXPECTED_PATH} missing", file=sys.stderr)
        return 2
    spec = json.loads(EXPECTED_PATH.read_text())
    threshold = float(spec.get("threshold", 0.80))
    images = spec["images"]
    if not images:
        print("FATAL: no images in ocr-expected.json", file=sys.stderr)
        return 2

    per_image = []
    failures = []
    for image_name, entry in images.items():
        path = FIXDIR / image_name
        if not path.exists():
            failures.append(f"{image_name}: missing fixture")
            per_image.append(0.0)
            continue
        try:
            ocr = run_vlm(path, f"ocr-gate-{image_name}")
        except (subprocess.TimeoutExpired, RuntimeError) as err:
            failures.append(f"{image_name}: {err}")
            per_image.append(0.0)
            continue
        matched, total = score_image(ocr, entry["fields"])
        ratio = matched / total if total else 0.0
        per_image.append(ratio)
        print(f"{image_name}: {matched}/{total} = {ratio:.2f}")

    corpus_acc = sum(per_image) / len(per_image) if per_image else 0.0
    print(f"CORPUS_ACCURACY: {corpus_acc:.3f}")
    print(f"THRESHOLD:       {threshold:.3f}")
    if failures:
        print("FAILURES:")
        for f in failures:
            print(f"  {f}")
    return 0 if corpus_acc >= threshold else 1


if __name__ == "__main__":
    sys.exit(main())
