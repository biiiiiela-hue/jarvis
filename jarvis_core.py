#!/usr/bin/env python3
"""Jarvis — peças comuns: voz, microfone, transcrição e leitura da mensagem do agente."""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata

# Homebrew fica em /opt/homebrew (chip Apple) ou /usr/local (Intel)
os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:" + os.environ.get("PATH", "")
HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, "Jarvis")
MODEL = os.path.join(BASE, "models", "ggml-large-v3-turbo-q5_0.bin")
LOG = os.path.join(BASE, "jarvis.log")
LOCK = os.path.join(BASE, ".lock")


def _config():
    """Preferências pessoais em config.json (copiado de config.exemplo.json na instalação)."""
    try:
        with open(os.path.join(BASE, "config.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


CONFIG = _config()
NOME = CONFIG.get("nome", "você")                    # como o Jarvis chama o usuário
VOZES = {"claude": "pt-BR-ThalitaMultilingualNeural", "codex": "pt-BR-AntonioNeural"}
VOZES.update(CONFIG.get("vozes", {}))
VOZ = os.environ.get("JARVIS_VOZ", CONFIG.get("voz_mac", "Luciana"))  # sem internet
VELOCIDADE = "195"

ESPERA_INICIO_FALA = 15   # segundos esperando o usuário começar a falar
DURACAO_MAX_FALA = 45     # segundos máximos de uma resposta
CONTINUACAO = 2.5         # segundos esperando o usuário retomar depois de uma pausa
SILENCIO_FIM = "2.5"      # segundos de silêncio que encerram a resposta
LIMIAR = "1%"             # volume mínimo para contar como fala (sobe se o ambiente for barulhento)

CANCELAR = ("depois", "agora não", "agora nao", "deixa", "espera", "pode parar",
            "para aí", "para ai", "não precisa", "nao precisa", "ignora")
# sons soltos que não são resposta — o Jarvis continua escutando
HESITACOES = {"ah", "ahn", "aham", "hum", "hm", "humm", "é", "eh", "uh", "uhm", "an", "e", "o", "ã", "hã", "e ai", "e aí", "ai", "aí"}
# frases que o whisper inventa quando só ouve ruído
ALUCINACOES = ("amara.org", "legendas", "obrigado por assistir", "inscreva-se",
               "música", "musica", "[", "(")


def log(msg):
    with open(LOG, "a") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")


def voz_neural(agente):
    try:
        return open(os.path.join(BASE, f"voz_{agente}.txt")).read().strip()
    except OSError:
        return VOZES.get(agente, VOZES["claude"])


def sintetizar(texto, agente="claude"):
    """Gera o áudio antes de tocar, para a fala sair colada no sino.
    Voz neural da Microsoft (edge-tts); sem internet, cai para a voz do macOS."""
    arq = tempfile.mktemp(suffix=".mp3", dir=BASE)
    try:
        subprocess.run([os.path.join(BASE, "venv", "bin", "edge-tts"),
                        "--voice", voz_neural(agente), "--rate=+8%",
                        "--text", texto, "--write-media", arq],
                       check=True, timeout=8, capture_output=True)
    except (subprocess.SubprocessError, OSError) as e:
        log(f"voz neural falhou ({e!r}), usando a do macOS")
        arq = arq[:-4] + ".aiff"
        subprocess.run(["say", "-v", VOZ, "-r", VELOCIDADE, "-o", arq, texto], check=False)
    return arq


def tocar(arq):
    subprocess.run(["afplay", arq], check=False)
    if os.path.exists(arq):
        os.remove(arq)


def falar(texto, agente="claude"):
    tocar(sintetizar(texto, agente))


def som(nome):
    subprocess.run(["afplay", f"/System/Library/Sounds/{nome}.aiff"], check=False)


def limpar_markdown(t):
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"[*_#>|]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def ultima_mensagem(dados):
    msg = dados.get("last_assistant_message")
    if msg:
        return msg
    caminho = dados.get("transcript_path")
    if not caminho or not os.path.exists(caminho):
        return ""
    ultimo = ""
    with open(caminho) as f:
        for linha in f:
            try:
                ev = json.loads(linha)
            except ValueError:
                continue
            m = ev.get("message") or {}
            if ev.get("type") == "assistant" and m.get("role") == "assistant":
                partes = [c.get("text", "") for c in m.get("content", [])
                          if isinstance(c, dict) and c.get("type") == "text"]
                if any(partes):
                    ultimo = "\n".join(partes)
    return ultimo


def frase_para_falar(msg):
    for linha in reversed(msg.splitlines()):
        m = re.match(r"^\s*🔊\s*(.+)$", linha)
        if m:
            return limpar_markdown(m.group(1))
    texto = limpar_markdown(msg)
    if not texto:
        return "terminei, mas não deixei mensagem."
    perguntas = re.findall(r"[^.!?]*\?", texto)
    if perguntas:
        return perguntas[-1].strip()[:220]
    primeira = re.split(r"(?<=[.!])\s", texto)[0]
    return primeira[:220]


def gravar_resposta(espera=None):
    """Grava até o usuário parar de falar. Devolve o caminho do wav ou None."""
    espera = espera or ESPERA_INICIO_FALA
    wav = tempfile.mktemp(suffix=".wav", dir=BASE)
    proc = subprocess.Popen(
        ["rec", "-q", "-c", "1", wav,
         "silence", "1", "0.25", LIMIAR, "1", SILENCIO_FIM, LIMIAR,
         "rate", "16k", "trim", "0", str(DURACAO_MAX_FALA)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    inicio = time.time()
    while proc.poll() is None:
        tamanho = os.path.getsize(wav) if os.path.exists(wav) else 0
        if tamanho <= 4096 and time.time() - inicio > espera:
            proc.terminate()
            proc.wait()
            break
        if time.time() - inicio > espera + DURACAO_MAX_FALA + 5:
            proc.terminate()
            proc.wait()
            break
        time.sleep(0.2)
    if not os.path.exists(wav) or os.path.getsize(wav) <= 4096:
        if os.path.exists(wav):
            os.remove(wav)
        return None
    return wav


def duracao(wav):
    r = subprocess.run(["soxi", "-D", wav], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def transcrever(wav):
    if duracao(wav) < 0.4:  # estalo, tecla, tosse: o whisper inventaria palavra
        os.replace(wav, os.path.join(BASE, "ultima_resposta.wav"))
        return ""
    r = subprocess.run(
        ["whisper-cli", "-m", MODEL, "-l", "pt", "-f", wav, "-nt", "-np", "-sns"],
        capture_output=True, text=True)
    os.replace(wav, os.path.join(BASE, "ultima_resposta.wav"))  # para conferir depois
    texto = re.sub(r"\s+", " ", r.stdout).strip()
    baixo = texto.lower()
    if len(baixo) < 2 or any(a in baixo for a in ALUCINACOES):
        return ""
    if re.sub(r"[^a-zà-ú ]", "", baixo).strip() in HESITACOES:
        return ""
    return texto


def normalizar(t):
    t = unicodedata.normalize("NFKD", t.lower())
    return "".join(c for c in t if not unicodedata.combining(c))




def assunto(dados, agente="claude"):
    """Título da conversa (o que aparece na barra lateral do app); senão, a pasta."""
    titulo = ""
    caminho = dados.get("transcript_path")
    if caminho and os.path.exists(caminho):
        with open(caminho) as f:
            for linha in f:
                if '"customTitle"' in linha:
                    try:
                        titulo = json.loads(linha).get("customTitle") or titulo
                    except ValueError:
                        pass
    return titulo or os.path.basename(dados.get("cwd") or "") or agente
