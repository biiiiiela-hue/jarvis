#!/bin/bash
# Remove o Jarvis: tira os hooks, para o app e não abre mais no início da sessão.
# Os arquivos em ~/Jarvis ficam; apague a pasta se quiser.
set -uo pipefail
cd "$HOME/Jarvis" || exit 1

python3 hooks.py remover
launchctl bootout "gui/$(id -u)/com.jarvis.assistente" 2>/dev/null
rm -f "$HOME/Library/LaunchAgents/com.jarvis.assistente.plist"
osascript -e 'quit app "Jarvis"' 2>/dev/null
pkill -f "$HOME/Jarvis/jarvisd.py" 2>/dev/null
echo "Jarvis removido. Para apagar tudo: rm -rf ~/Jarvis"
