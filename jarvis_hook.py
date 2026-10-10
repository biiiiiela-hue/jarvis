#!/usr/bin/env python3
"""Jarvis — hook Stop do Claude Code e do Codex.

Entrega a pergunta do agente para o jarvisd (que fala, escuta e mostra o beagle) e espera
a resposta do usuário. Pergunta não respondida fica pendente no jarvisd: o usuário pode dizer
"Jarvis" depois e responder. Se o jarvisd estiver desligado, conversa sozinho como antes.

Desligar: crie o arquivo ~/Jarvis/OFF.
"""
import fcntl
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jarvis_core as jc  # noqa: E402

AGENTE = os.environ.get("JARVIS_AGENTE", "claude")  # "codex" quando chamado pelo hook do Codex
# em segundo plano (Claude, asyncRewake): a conversa fica livre e só é acordada quando o usuário
# responde — a resposta volta pelo stderr com código 2
FUNDO = os.environ.get("JARVIS_FUNDO") == "1"
# quanto tempo o agente fica esperando a resposta (precisa ser menor que o timeout do hook)
ESPERA_MAX = float(os.environ.get("JARVIS_ESPERA_MAX", "21600" if FUNDO else "1700"))
URL = "http://127.0.0.1:8765"

CANCELAR = ("depois", "agora não", "agora nao", "deixa", "espera", "pode parar",
            "para aí", "para ai", "não precisa", "nao precisa", "ignora")


def chamar(caminho, dados=None, timeout=60):
    corpo = json.dumps(dados).encode() if dados is not None else None
    req = urllib.request.Request(URL + caminho, data=corpo,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def pelo_jarvisd(frase, projeto, sessao, transcript=""):
    recado = {"agente": AGENTE, "projeto": projeto, "frase": frase, "sessao": sessao,
              "transcript": transcript}
    pid = chamar("/pergunta", recado, 3)["id"]
    limite = time.time() + ESPERA_MAX
    try:
        while time.time() < limite:
            espera = max(1, min(25, limite - time.time()))
            try:
                r = chamar(f"/resposta?id={pid}&espera={espera:.0f}", timeout=espera + 10)
            except OSError:
                time.sleep(3)  # jarvisd reiniciando: tenta de novo
                continue
            if r["status"] == "respondida":
                return r["resposta"]
            if r["status"] == "desconhecida":
                # o jarvisd reiniciou e perdeu a fila: registra o recado de novo, em silêncio
                recado["reenvio"] = True
                pid = chamar("/pergunta", recado, 3)["id"]
                continue
            if r["status"] != "aguardando":
                return ""
        return ""
    finally:
        try:
            chamar("/encerrar", {"id": pid}, 3)
        except OSError:
            pass


def sozinho(frase, projeto):
    """Plano B, sem o jarvisd: fala e escuta direto daqui."""
    with open(os.path.join(jc.BASE, ".lock"), "w") as trava:
        fcntl.flock(trava, fcntl.LOCK_EX)
        quem = "o Codex aqui. " if AGENTE == "codex" else ""
        audio = jc.sintetizar(f"{jc.NOME}, {quem}{frase}", AGENTE)
        jc.som("Glass")
        jc.tocar(audio)
        for tentativa in range(2):
            jc.som("Tink")
            gravacoes = []
            wav = jc.gravar_resposta()
            while wav:
                jc.som("Pop")
                gravacoes.append(wav)
                wav = jc.gravar_resposta(espera=jc.CONTINUACAO)
            if gravacoes:
                resposta = " ".join(t for t in (jc.transcrever(w) for w in gravacoes) if t)
                if resposta:
                    jc.som("Bottle")
                    return resposta
                jc.som("Basso")
            if tentativa == 0:
                jc.falar(f"{jc.NOME}?", AGENTE)
    return ""


def main():
    if os.environ.get("JARVIS_INNER") or os.path.exists(os.path.join(jc.BASE, "OFF")):
        return
    try:
        dados = json.load(sys.stdin)
    except ValueError:
        dados = {}

    frase = jc.frase_para_falar(jc.ultima_mensagem(dados))
    projeto = jc.assunto(dados, AGENTE)

    try:
        chamar("/vivo", timeout=1)
        resposta = pelo_jarvisd(frase, projeto, dados.get("session_id", ""),
                                dados.get("transcript_path", ""))
    except OSError:
        if FUNDO:  # modo interruptor: sem o jarvisd, não fala sozinho
            jc.log("jarvisd desligado, recado não registrado")
            return
        jc.log("jarvisd desligado, conversando direto pelo hook")
        resposta = sozinho(frase, projeto)
        if resposta:
            jc.log(f"[{AGENTE}:{projeto}] {jc.NOME}: {resposta[:160]}")

    if not resposta:
        return
    if any(jc.normalizar(c) in jc.normalizar(resposta) for c in CANCELAR):
        jc.falar("Beleza, fico esperando.", AGENTE)
        return

    motivo = (f"{jc.NOME} respondeu por voz, pelo Jarvis: \"{resposta}\". "
                   "A transcrição pode ter pequenos erros; interprete pelo sentido. "
                   f"REGRA DE {jc.NOME.upper()}: se isso for uma confirmação (ok, sim, pode, bora, "
                   "executa) de um plano que você acabou de propor, execute. Se for "
                   "um pedido novo ou uma correção, NÃO execute ainda: responda só "
                   "com a linha 🔊 dizendo em uma frase o que entendeu e o que vai "
                   "fazer, terminando com \"Ok?\".")
    if FUNDO:
        sys.stderr.write(motivo)
        sys.exit(2)  # acorda a conversa com a resposta
    print(json.dumps({"decision": "block", "reason": motivo}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # o Jarvis nunca pode travar o agente
        jc.log(f"erro no hook: {e!r}")
