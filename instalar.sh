#!/bin/bash
# Instala o Jarvis: assistente de voz para Claude Code e Codex no macOS.
# Uso: clone este repositório em ~/Jarvis e rode  ./instalar.sh
set -euo pipefail

BASE="$HOME/Jarvis"
MODELOS="$BASE/models"
WHISPER="ggml-large-v3-turbo-q5_0.bin"
OWW="https://github.com/dscripka/openWakeWord/releases/download/v0.5.1"

passo() { printf '\n\033[1m▸ %s\033[0m\n' "$1"; }
erro() { printf '\033[31m✗ %s\033[0m\n' "$1"; exit 1; }

[ "$(pwd -P)" = "$(cd "$BASE" 2>/dev/null && pwd -P)" ] || erro "Clone o repositório em ~/Jarvis e rode o instalador de lá."
[ "$(uname)" = "Darwin" ] || erro "O Jarvis só funciona no macOS."
command -v brew >/dev/null || erro "Instale o Homebrew primeiro: https://brew.sh"
xcrun --find swiftc >/dev/null 2>&1 || erro "Instale as ferramentas de linha de comando: xcode-select --install"

passo "Dependências (sox, whisper.cpp, ffmpeg, Python 3.11)"
brew install sox whisper-cpp ffmpeg python@3.11
PY311="$(brew --prefix python@3.11)/bin/python3.11"

passo "Ambientes Python"
[ -d venv ] || python3 -m venv venv
./venv/bin/pip install -q --upgrade edge-tts
[ -d venv-ww ] || "$PY311" -m venv venv-ww
./venv-ww/bin/pip install -q --upgrade openwakeword sounddevice numpy

passo "Modelos (transcrição ~550 MB e detector de \"Ei, Jarvis\")"
mkdir -p "$MODELOS"
[ -f "$MODELOS/$WHISPER" ] || curl -L --fail -o "$MODELOS/$WHISPER" \
  "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$WHISPER"
OWW_DIR="$(./venv-ww/bin/python -c 'import openwakeword,os;print(os.path.join(os.path.dirname(openwakeword.__file__),"resources","models"))')"
mkdir -p "$OWW_DIR"
for f in melspectrogram.onnx embedding_model.onnx hey_jarvis_v0.1.onnx silero_vad.onnx; do
  [ -f "$OWW_DIR/$f" ] || curl -sL --fail -o "$OWW_DIR/$f" "$OWW/$f"
done

passo "Configuração pessoal"
if [ ! -f config.json ]; then
  read -r -p "Como o Jarvis deve te chamar? " NOME
  sed "s/\"SeuNome\"/\"${NOME:-você}\"/" config.exemplo.json > config.json
fi
[ -f modo.txt ] || echo conversa > modo.txt

passo "App do Jarvis (janela do beagle)"
mkdir -p app/Jarvis.app/Contents/MacOS
cp app/Info.plist app/Jarvis.app/Contents/
xcrun swiftc -O app/main.swift -o app/Jarvis.app/Contents/MacOS/Jarvis -framework Cocoa -framework WebKit
codesign --force --deep -s - app/Jarvis.app

passo "Hooks no Claude Code e no Codex"
python3 hooks.py instalar

passo "Abrir no início da sessão"
PLIST="$HOME/Library/LaunchAgents/com.jarvis.assistente.plist"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.jarvis.assistente</string>
  <key>ProgramArguments</key>
  <array><string>/usr/bin/open</string><string>$BASE/app/Jarvis.app</string></array>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
EOF
launchctl bootout "gui/$(id -u)/com.jarvis.assistente" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

passo "Pronto"
cat <<'EOF'
O beagle aparece no canto inferior direito. Na primeira vez, o macOS pede permissão de
microfone para o Jarvis: clique em Permitir.

- Diga "Ei, Jarvis" para chamar. O "?" ao lado do cachorrinho lista os comandos.
- Conversas novas do Claude Code já usam o Jarvis (as abertas antes da instalação, não).
- Codex: aprove os hooks uma vez (rode `codex` no terminal, digite /hooks e aprove).
EOF
