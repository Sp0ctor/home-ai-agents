#!/usr/bin/env python3
"""Scheduled entry point for Donut: runs eraser_job.py daily (see that file)."""
import runpy, sys
from pathlib import Path
sys.argv = ["eraser_job.py", "daily"]
runpy.run_path(str(Path(__file__).resolve().parent / "eraser_job.py"), run_name="__main__")
