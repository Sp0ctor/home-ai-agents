#!/usr/bin/env python3
"""Local transcription with MLX Whisper (runs on the Mac's own chip; nothing is uploaded).
Run with: ~/.local/aitools/venv/bin/python ~/.hermes/scripts/transcribe.py ...

  transcribe.py FILE [FILE ...]   transcribe now (a 1-hour lecture takes a few minutes)
  transcribe.py --bg FILE ...     start in the background and return right away (use this from chat)
  transcribe.py auto              transcribe every new recording in ~/Documents/Athena (nightly job; silent if none)
  transcribe.py status            what's running / finished recently

Output: for a file in ~/Documents/Athena/<COURSE>/..., the transcript goes to
~/Documents/Athena/<COURSE>/Notes/Transcripts/<name>.md (+ .srt with timestamps). Otherwise next to the file.
An existing transcript is never redone unless you add --force.
"""
import datetime as dt, os, subprocess, sys, time
from pathlib import Path

VENV_PY = Path.home() / ".local/aitools/venv/bin/python"
if Path(sys.prefix).resolve() != VENV_PY.parent.parent.resolve() and VENV_PY.exists() and not os.environ.get("AITOOLS_REEXEC"):
    os.environ["AITOOLS_REEXEC"] = "1"                 # run with the venv that has the tools, whoever called us
    os.execv(str(VENV_PY), [str(VENV_PY), __file__] + sys.argv[1:])

HOME = Path.home()
ATHENA = HOME / "Documents/Athena"
LOG = HOME / ".hermes/logs/transcribe.log"
MODEL = "mlx-community/whisper-large-v3-turbo"
EXTS = {".mp4", ".m4a", ".mp3", ".wav", ".mov", ".aac", ".flac", ".ogg", ".webm", ".mkv"}
os.environ["PATH"] = str(HOME / ".local/aitools/bin") + os.pathsep + os.environ.get("PATH", "")


def log(line):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M} {line}\n")


def out_paths(src: Path):
    try:
        rel = src.resolve().relative_to(ATHENA.resolve())
        course = rel.parts[0]
        d = ATHENA / course / "Notes" / "Transcripts"
    except (ValueError, IndexError):
        d = src.parent
    d.mkdir(parents=True, exist_ok=True)
    return d / (src.stem + ".md"), d / (src.stem + ".srt")


def ts(sec):
    h, r = divmod(int(sec), 3600)
    m, s = divmod(r, 60)
    return f"{h:02}:{m:02}:{s:02},{int((sec % 1) * 1000):03}"


def transcribe(src: Path, force=False):
    md, srt = out_paths(src)
    if md.exists() and not force:
        return f"SKIP (already transcribed): {md}"
    import mlx_whisper
    t0 = time.time()
    log(f"START {src}")
    r = mlx_whisper.transcribe(str(src), path_or_hf_repo=MODEL, language="en")
    segs = r.get("segments", [])
    paras, cur, start = [], [], 0.0
    for sg in segs:                                   # paragraphs of ~60 s with a [mm:ss] marker
        if not cur:
            start = sg["start"]
        cur.append(sg["text"].strip())
        if sg["end"] - start > 60:
            paras.append(f"[{int(start // 60):02}:{int(start % 60):02}] " + " ".join(cur))
            cur = []
    if cur:
        paras.append(f"[{int(start // 60):02}:{int(start % 60):02}] " + " ".join(cur))
    mins = (segs[-1]["end"] / 60) if segs else 0
    md.write_text(f"# Transcript - {src.stem}\n\nSource: {src}\nTranscribed {dt.date.today()} with local Whisper "
                  f"({mins:.0f} min of audio). Machine transcript - names and technical terms may be misspelled.\n\n"
                  + "\n\n".join(paras) + "\n", encoding="utf-8")
    srt.write_text("\n".join(f"{i}\n{ts(s['start'])} --> {ts(s['end'])}\n{s['text'].strip()}\n"
                             for i, s in enumerate(segs, 1)), encoding="utf-8")
    log(f"DONE {src} -> {md} ({mins:.0f} min audio, {time.time() - t0:.0f}s)")
    return f"RESULT: OK - {mins:.0f} min transcribed in {(time.time() - t0) / 60:.1f} min -> {md}"


def find_new():
    out = []
    for f in sorted(ATHENA.rglob("*")):
        if f.suffix.lower() in EXTS and "Transcripts" not in f.parts and not out_paths(f)[0].exists():
            out.append(f)
    return out


def main(a):
    force = "--force" in a
    a = [x for x in a if x != "--force"]
    if not a:
        print(__doc__); return
    if a[0] == "status":
        print("\n".join(LOG.read_text().splitlines()[-10:]) if LOG.exists() else "Nothing transcribed yet."); return
    if a[0] == "--bg":
        files = a[1:]
        subprocess.Popen([sys.executable, __file__] + (["--force"] if force else []) + files,
                         stdout=open(LOG, "a"), stderr=subprocess.STDOUT, start_new_session=True)
        print(f"RESULT: STARTED in the background ({len(files)} file(s)). Check with: transcribe.py status"); return
    if a[0] == "auto":
        new = find_new()
        done = []
        for f in new:
            try:
                done.append(transcribe(f))
            except Exception as ex:
                log(f"FAILED {f}: {type(ex).__name__}: {ex}")
                done.append(f"FAILED {f.name}: {type(ex).__name__}")
        if done:
            print("ATHENA: transcribed new class recordings overnight:\n" + "\n".join("  " + d for d in done)
                  + "\nAsk Athena to turn any of them into notes and to-dos.")
        return
    for f in a:
        p = Path(f).expanduser()
        if not p.exists():
            print(f"FAIL - file not found: {p}"); continue
        try:
            print(transcribe(p, force))
        except Exception as ex:
            log(f"FAILED {p}: {type(ex).__name__}: {ex}")
            print(f"FAIL - {p.name}: {type(ex).__name__}: {ex}")


if __name__ == "__main__":
    main(sys.argv[1:])
