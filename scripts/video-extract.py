#!/usr/bin/env python3
"""Installed entry point. Repository execution requires installation first."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent/'lib'))
from video_extract import main
if __name__ == '__main__':
    raise SystemExit(main())
