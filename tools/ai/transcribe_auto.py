#!/usr/bin/env python3
"""Nightly job: transcribe any new class recordings in ~/Documents/Athena (runs transcribe.py auto)."""
import os, sys
from pathlib import Path
here = Path(__file__).resolve().parent
os.execv(sys.executable, [sys.executable, str(here / "transcribe.py"), "auto"])
