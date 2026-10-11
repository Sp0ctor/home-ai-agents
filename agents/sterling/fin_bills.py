#!/usr/bin/env python3
"""Scheduled (Mondays 9 AM): add text reminders for bills due in the next 5 weeks. Silent unless new ones were added."""
import runpy, sys
from pathlib import Path
sys.argv = ["fin_tool.py", "bills-remind"]
runpy.run_path(str(Path.home() / ".hermes/scripts/fin_tool.py"), run_name="__main__")
