#!/usr/bin/env python3
"""Liga ou desliga os hooks do Jarvis no Claude Code e no Codex, sem mexer nos hooks que já existem.

    python3 hooks.py instalar
    python3 hooks.py remover
"""
import json
import os
import shutil
import sys

HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, "Jarvis")
PYTHON = shutil.which("python3") or "/usr/bin/python3"
CLAUDE = os.path.join(HOME, ".claude", "settings.json")
CODEX = os.path.join(HOME, ".codex", "hooks.json")
MARCA = "/Jarvis/jarvis_"  # identifica os hooks do Jarvis

HOOKS_CLAUDE = {
    # a conversa termina livre; o hook espera a resposta em segundo plano e acorda a conversa
    "Stop": {"type": "command", "command": f"JARVIS_FUNDO=1 {PYTHON} {BASE}/jarvis_hook.py",
             "timeout": 21900, "asyncRewake": True},
    "SessionStart": {"type": "command", "command": f"{PYTHON} {BASE}/jarvis_contexto.py"},
    "UserPromptSubmit": {"type": "command", "command": f"{PYTHON} {BASE}/jarvis_cancelar.py",
                         "timeout": 5},
}
HOOKS_CODEX = {
    "Stop": {"type": "command",
             "command": f"JARVIS_AGENTE=codex {PYTHON} {BASE}/jarvis_hook.py", "timeout": 1800},
    "SessionStart": {"type": "command", "command": f"{PYTHON} {BASE}/jarvis_contexto.py"},
}


def ler(caminho):
    try:
        with open(caminho) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def gravar(caminho, dados):
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    if os.path.exists(caminho):
        shutil.copy(caminho, caminho + ".antes-do-jarvis")
    with open(caminho, "w") as f:
        json.dump(dados, f, indent=2, ensure_ascii=False)


def sem_jarvis(grupos):
    """Tira só os hooks do Jarvis; os outros ficam como estão."""
    novos = []
    for g in grupos:
        hooks = [h for h in g.get("hooks", []) if MARCA not in h.get("command", "")]
        if hooks:
            novos.append({**g, "hooks": hooks})
    return novos


def aplicar(caminho, hooks, instalar):
    dados = ler(caminho)
    todos = dados.setdefault("hooks", {})
    for evento in list(todos):
        todos[evento] = sem_jarvis(todos[evento])
        if not todos[evento]:
            del todos[evento]
    if instalar:
        for evento, hook in hooks.items():
            todos.setdefault(evento, []).append({"hooks": [hook]})
    gravar(caminho, dados)


if __name__ == "__main__":
    acao = sys.argv[1] if len(sys.argv) > 1 else ""
    if acao not in ("instalar", "remover"):
        sys.exit(__doc__)
    aplicar(CLAUDE, HOOKS_CLAUDE, acao == "instalar")
    print(f"Claude Code: hooks {'ligados' if acao == 'instalar' else 'removidos'} em {CLAUDE}")
    if os.path.isdir(os.path.dirname(CODEX)):
        aplicar(CODEX, HOOKS_CODEX, acao == "instalar")
        print(f"Codex: hooks {'ligados' if acao == 'instalar' else 'removidos'} em {CODEX}")
        if acao == "instalar":
            print("  → no Codex, aprove os hooks uma vez: rode `codex` no terminal, digite /hooks e aprove.")
