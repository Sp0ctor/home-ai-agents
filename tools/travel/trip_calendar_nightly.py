#!/usr/bin/env python3
"""Scheduled entry point (nightly): rebuild the trip calendar file - see trip_tools.py. Silent unless it changed."""
import runpy, sys
from pathlib import Path
sys.argv = ["trip_tools.py", "calendar", "--nightly"]
runpy.run_path(str(Path(__file__).resolve().parent / "trip_tools.py"), run_name="__main__")
