#!/usr/bin/env python3
"""CLI wrapper for scripts/lib/eod_capture.py."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import eod_capture  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(eod_capture.main())
