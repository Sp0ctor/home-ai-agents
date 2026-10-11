#!/usr/bin/env python3
"""Scheduled (1st of each month, 8 AM): Sterling's monthly finance report for last month."""
import runpy, sys
from pathlib import Path
sys.argv = ["fin_tool.py", "summary"]
runpy.run_path(str(Path.home() / ".hermes/scripts/fin_tool.py"), run_name="__main__")
