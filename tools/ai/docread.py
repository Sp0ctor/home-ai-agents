#!/usr/bin/env python3
"""Read a document with Docling (PDF, Word, PowerPoint, Excel, HTML, images) and get clean Markdown,
including tables and scanned pages (OCR). Runs locally; nothing is uploaded.
Run with: ~/.local/aitools/venv/bin/python ~/.hermes/scripts/docread.py FILE [--save]

  docread.py FILE           print the document as Markdown (first 40,000 characters)
  docread.py FILE --save    also save it as FILE.md next to the original
The full text is always cached in ~/.hermes/cache/docread/<name>.md
"""
import os, sys, time
from pathlib import Path

VENV_PY = Path.home() / ".local/aitools/venv/bin/python"
if Path(sys.prefix).resolve() != VENV_PY.parent.parent.resolve() and VENV_PY.exists() and not os.environ.get("AITOOLS_REEXEC"):
    os.environ["AITOOLS_REEXEC"] = "1"                 # run with the venv that has the tools, whoever called us
    os.execv(str(VENV_PY), [str(VENV_PY), __file__] + sys.argv[1:])

CACHE = Path.home() / ".hermes/cache/docread"
LIMIT = 40000


def main(a):
    if not a or a[0] in ("-h", "--help"):
        print(__doc__); return
    src = Path(a[0]).expanduser()
    if not src.exists():
        print(f"FAIL - file not found: {src}"); sys.exit(1)
    t0 = time.time()
    try:
        from docling.document_converter import DocumentConverter
        md = DocumentConverter().convert(str(src)).document.export_to_markdown()
    except Exception as ex:
        print(f"FAIL - Docling couldn't read {src.name}: {type(ex).__name__}: {ex}"); sys.exit(1)
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / (src.stem + ".md")
    cached.write_text(md, encoding="utf-8")
    saved = ""
    if "--save" in a:
        out = src.with_suffix(src.suffix + ".md")
        out.write_text(md, encoding="utf-8")
        saved = f" Saved: {out}"
    print(f"RESULT: OK - {src.name}: {len(md):,} characters in {time.time() - t0:.0f}s. Full text: {cached}.{saved}\n")
    print(md[:LIMIT])
    if len(md) > LIMIT:
        print(f"\n[... {len(md) - LIMIT:,} more characters - read {cached} for the rest]")


if __name__ == "__main__":
    main(sys.argv[1:])
