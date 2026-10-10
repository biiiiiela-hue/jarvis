#!/usr/bin/env python3
"""Hook UserPromptSubmit: o usuário mandou mensagem numa conversa.
Tira o recado dela da fila do Jarvis e marca a conversa como trabalhando."""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def avisar(caminho, dados):
    req = urllib.request.Request("http://127.0.0.1:8765" + caminho, data=json.dumps(dados).encode(),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=2).read()


try:
    import jarvis_core as jc
    dados = json.load(sys.stdin)
    sessao = dados.get("session_id", "")
    if sessao:
        avisar("/encerrar_sessao", {"sessao": sessao})
        avisar("/trabalhando", {"sessao": sessao, "agente": "claude",
                                "assunto": jc.assunto(dados, "claude")})
except Exception:  # noqa: BLE001 — nunca atrapalha o envio da mensagem
    pass
