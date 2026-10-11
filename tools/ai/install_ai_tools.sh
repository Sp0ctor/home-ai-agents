#!/bin/bash
# Installs 4 open-source tools for the owner's agents on the Mac mini (no Homebrew, no Docker needed):
#   1. mlx-whisper  (Apple's MLX Whisper)     - local transcription of lecture recordings  -> Athena
#   2. Docling      (docling-project/docling) - PDF/Word/slides -> clean text with tables    -> Athena, Sterling
#   3. JobSpy       (speedyapply/JobSpy)      - Indeed/LinkedIn/ZipRecruiter/Google Jobs    -> Reginald's job list
#   4. SearXNG      (searxng/searxng)         - private search engine on this Mac           -> every agent's web search
# Each tool gets its own folder under ~/.local so they can't break Hermes or each other.
# Needs ~6 GB of disk and 15-30 minutes (mostly downloads). Safe to run again.
set -uo pipefail

PY="$HOME/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11"
[ -x "$PY" ] || PY="$(grep '^home' "$HOME/.hermes/hermes-agent/venv/pyvenv.cfg" | cut -d= -f2 | xargs)/python3.11"
[ -x "$PY" ] || { echo "Can't find Python 3.11 (the one Hermes uses). Stopping."; exit 1; }
SEARX_COMMIT=6671d89bede8c9fc108b17bb98916170f5657650   # SearXNG master on 2026-10-07, tested before install
SEARX_PORT=8888
FAILED=()
step() { echo; echo "=== $* ==="; }

# ---------------------------------------------------------------- 1+2. Whisper + Docling (one folder)
step "1/5 mlx-whisper + Docling -> ~/.local/aitools"
A="$HOME/.local/aitools"
mkdir -p "$A/bin"
[ -x "$A/venv/bin/python" ] || "$PY" -m venv "$A/venv"
"$A/venv/bin/pip" install -q --upgrade pip
if "$A/venv/bin/pip" install -q --upgrade mlx-whisper docling imageio-ffmpeg; then
  FF=$("$A/venv/bin/python" -c "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())")
  ln -sf "$FF" "$A/bin/ffmpeg"
  "$A/bin/ffmpeg" -version | head -1
  echo "Downloading the Whisper model (~1.6 GB, once)..."
  "$A/venv/bin/python" -c "from huggingface_hub import snapshot_download; snapshot_download('mlx-community/whisper-large-v3-turbo')" \
    || echo "  (model download will happen on first use instead)"
  echo "Downloading Docling's layout models (once)..."
  "$A/venv/bin/docling-tools" models download >/dev/null 2>&1 || echo "  (Docling models will download on first use instead)"
  echo "OK - mlx-whisper + Docling installed"
else
  FAILED+=("mlx-whisper/Docling pip install")
fi

# ---------------------------------------------------------------- 3. JobSpy
step "2/5 JobSpy -> ~/.local/jobspy"
J="$HOME/.local/jobspy"
[ -x "$J/venv/bin/python" ] || "$PY" -m venv "$J/venv"
"$J/venv/bin/pip" install -q --upgrade pip
if "$J/venv/bin/pip" install -q "python-jobspy==1.2.0"; then
  "$J/venv/bin/python" -c "import jobspy; print('OK - JobSpy', jobspy.__name__)"
else
  FAILED+=("JobSpy pip install")
fi

# ---------------------------------------------------------------- 4. SearXNG
step "3/5 SearXNG -> ~/.local/searxng (runs only on this Mac, port $SEARX_PORT)"
S="$HOME/.local/searxng"
mkdir -p "$S"
if [ ! -f "$S/src/setup.py" ] || [ "$(cat "$S/.commit" 2>/dev/null)" != "$SEARX_COMMIT" ]; then
  TMP=$(mktemp -d)
  curl -fsSL -o "$TMP/sx.tar.gz" "https://github.com/searxng/searxng/archive/$SEARX_COMMIT.tar.gz" \
    && tar xzf "$TMP/sx.tar.gz" -C "$TMP" \
    && rm -rf "$S/src" && mv "$TMP/searxng-$SEARX_COMMIT" "$S/src" && echo "$SEARX_COMMIT" > "$S/.commit"
  rm -rf "$TMP"
fi
[ -x "$S/venv/bin/python" ] || "$PY" -m venv "$S/venv"
"$S/venv/bin/pip" install -q --upgrade pip setuptools wheel
if "$S/venv/bin/pip" install -q -r "$S/src/requirements.txt" && "$S/venv/bin/pip" install -q --no-build-isolation -e "$S/src"; then
  if [ ! -f "$S/settings.yml" ]; then
    SECRET=$("$PY" -c 'import secrets;print(secrets.token_hex(32))')
    cat > "$S/settings.yml" <<EOF
use_default_settings: true
general:
  instance_name: "Donut Search"
server:
  secret_key: "$SECRET"
  bind_address: "127.0.0.1"
  port: $SEARX_PORT
  limiter: false
  public_instance: false
search:
  safe_search: 0
  formats:
    - html
    - json
EOF
    chmod 600 "$S/settings.yml"
  fi
  PLIST="$HOME/Library/LaunchAgents/local.donut.searxng.plist"
  mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
  launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>local.donut.searxng</string>
  <key>ProgramArguments</key><array>
    <string>$S/venv/bin/python</string><string>-m</string><string>searx.webapp</string></array>
  <key>WorkingDirectory</key><string>$S</string>
  <key>EnvironmentVariables</key><dict>
    <key>SEARXNG_SETTINGS_PATH</key><string>$S/settings.yml</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/searxng.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/searxng.log</string>
</dict></plist>
EOF
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  OK=""
  for i in $(seq 1 30); do
    if curl -s "http://127.0.0.1:$SEARX_PORT/search?q=weather+new+york&format=json" | grep -q '"results"'; then OK=1; break; fi
    sleep 1
  done
  if [ -n "$OK" ]; then
    N=$(curl -s "http://127.0.0.1:$SEARX_PORT/search?q=weather+new+york&format=json" | "$PY" -c 'import json,sys;print(len(json.load(sys.stdin)["results"]))')
    echo "OK - SearXNG running at http://127.0.0.1:$SEARX_PORT ($N results for a test search)"
  else
    FAILED+=("SearXNG didn't start - see ~/Library/Logs/searxng.log")
  fi
else
  FAILED+=("SearXNG pip install")
fi

# ---------------------------------------------------------------- 5. point the agents' web search at SearXNG
step "4/5 Switching Donut, Reginald, Athena, Vlad and Sterling to SearXNG search"
if curl -s "http://127.0.0.1:$SEARX_PORT/healthz" >/dev/null 2>&1; then
  for P in "" reginald athena vlad sterling; do
    if [ -z "$P" ]; then H=(hermes); NAME=Donut; else H=(hermes -p "$P"); NAME=$P; fi
    "${H[@]}" config set web.search_backend searxng >/dev/null 2>&1 \
      && "${H[@]}" config set SEARXNG_URL "http://127.0.0.1:$SEARX_PORT" >/dev/null 2>&1 \
      && echo "  $NAME -> SearXNG" || FAILED+=("hermes config set for $NAME")
  done
  echo "  (If SearXNG is ever down, Hermes automatically falls back to its free search providers.)"
else
  echo "  Skipped - SearXNG isn't answering, so the agents keep their current search."
fi

step "5/5 Summary"
if [ ${#FAILED[@]} -eq 0 ]; then
  echo "ALL DONE. Restarting the agents so they pick up the new search..."
  hermes gateway restart >/dev/null 2>&1 && echo "  Donut restarted"
  for P in reginald athena vlad sterling; do hermes -p "$P" gateway restart >/dev/null 2>&1 && echo "  $P restarted"; done
else
  echo "Finished with problems:"; for f in "${FAILED[@]}"; do echo "  - $f"; done
  echo "Send this output to Claude."
fi
