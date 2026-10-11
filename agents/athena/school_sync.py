#!/usr/bin/env python3
"""Scheduled every 30 min: file anything the owner sent Athena (documents, readings, photos, voice notes)
into ~/Documents/Athena. Silent when there's nothing new."""
import contextlib, io, runpy, sys
from pathlib import Path
buf = io.StringIO()
sys.argv = ["school.py", "sync"]
try:
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        runpy.run_path(str(Path(__file__).resolve().parent / "school.py"), run_name="__main__")
except SystemExit:
    pass
except Exception as ex:
    print(f"ATHENA: filing new school files failed ({type(ex).__name__}: {ex}).")
out = buf.getvalue().strip()
if out and "up to date" not in out:
    print("ATHENA saved new school files to your library:\n" + out)
