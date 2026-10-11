#!/usr/bin/env python3
"""Scheduled entry point (every 30 min): trip flight watcher - see trip_tools.py. Silent unless something changes."""
import runpy, sys
from pathlib import Path
sys.argv = ["trip_tools.py", "watch"]
runpy.run_path(str(Path(__file__).resolve().parent / "trip_tools.py"), run_name="__main__")
