#!/usr/bin/env python3
"""Run the supervised knowledge-integration CLI."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from knowledge_integration import main

if __name__ == "__main__":
    raise SystemExit(main())
