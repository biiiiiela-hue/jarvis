#!/usr/bin/env python3
"""Jarvis — o programa que fica sempre ligado.

- Recebe as perguntas dos agentes (hooks do Claude Code e do Codex) por HTTP local.
- Fala com o usuário, escuta a resposta e devolve para o agente que perguntou.
- Pergunta não respondida fica na fila; quando o usuário diz "Jarvis", ele repete as pendentes.
- Serve a tela do beagle (ui.html) e o estado dela em /estado.
"""
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jarvis_core as jc  # noqa: E402

PORTA = 8765
NOMES = {"claude": "Claude", "codex": "Codex"}

trava = threading.Lock()
perguntas = {}          # id -> {id, agente, projeto, frase, status, resposta, criada}
fila = queue.Queue()    # ids novos para anunciar, ou "CHAMADO" quando o usuário diz Jarvis
ocupado = threading.Event()  # falando/ouvindo: o detector de "Jarvis" fica surdo
estado = {"modo": "oculto", "texto": "", "agente": "", "ouvido": "", "versao": 0, "nivel": 0.0,
          "dispensado": False,  # o usuário mandou embora: some da tela até o próximo "Ei, Jarvis"
          "boca": None,         # envelope da fala em andamento, para a boca do beagle
          "boot": uuid.uuid4().hex[:8]}  # muda a cada reinício: a tela se recarrega
fala_atual = [None]           # afplay tocando agora (para poder interromper)
interrompido = threading.Event()
adiado = threading.Event()
trabalhando = {}              # sessao -> {assunto, agente, desde}: conversas em andamento
ARQ_FILA = os.path.join(jc.BASE, "fila.json")
SEM_CANAL = 60                # s sem o hook perguntar pela resposta = caminho de volta perdido    # "Agora não": para tudo, descarta o que ouviu, pergunta fica pendente

# frases que mandam o Jarvis embora (comparadas com a fala inteira, sem "Jarvis")
DISPENSAR = {"pode ir", "valeu", "tchau", "tchau tchau", "dispensado", "pode sair", "obrigado",
             "obrigada", "some", "sai", "ate mais", "ate logo", "pode descansar", "valeu obrigado",
             "esconde", "se esconde", "oculta", "ocultar", "esconder", "some daqui"}

# modos: o que o Jarvis faz quando um agente deixa recado
#   conversa — aparece, fala o próximo passo e abre o microfone (padrão)
#   aviso    — aparece e fala uma frase curta, sem abrir o microfone
#   discreto — só o sininho baixinho e o número no cachorrinho
MODOS = ("conversa", "aviso", "discreto")
ARQ_MODO = os.path.join(jc.BASE, "modo.txt")


def modo_atual():
    try:
        m = open(ARQ_MODO).read().strip()
        return m if m in MODOS else "conversa"
    except OSError:
        return "conversa"


class Microfone:
    """Um só fluxo de microfone para tudo: o chamado "Jarvis" e as respostas do usuário.
    Fala é decidida pelo Silero VAD (rede neural), não por volume — funciona com ruído de fundo."""
    TAXA, BLOCO = 16000, 1280          # blocos de 80 ms
    SEG = BLOCO / TAXA

    def __init__(self):
        self.ouvintes = []             # funções que recebem cada bloco (detector de "Jarvis")
        self.gravando = None           # fila ativa enquanto uma resposta está sendo gravada
        self.pronto = threading.Event()

    def rodar(self):
        import sounddevice as sd
        from openwakeword.vad import VAD
        self.vad = VAD()
        with sd.InputStream(samplerate=self.TAXA, channels=1, dtype="int16",
                            blocksize=self.BLOCO) as fluxo:
            self.pronto.set()
            jc.log("microfone ligado")
            while True:
                bloco, _ = fluxo.read(self.BLOCO)
                b = bloco[:, 0].copy()
                q = self.gravando
                if q is not None:
                    q.put(b)
                for f in self.ouvintes:
                    f(b)

    def gravar(self, espera, maximo=25.0, fim=1.8):
        """Espera o usuário começar a falar (até `espera` s) e grava até `fim` s de silêncio.
        Devolve o caminho de um wav 16 kHz, None se ele não falou, ou LONGA se a fala passou de
        `maximo` s — resposta para o Jarvis é curta; fala longa é conversa no ambiente."""
        import numpy as np
        import wave
        self.pronto.wait(10)
        self.vad.reset_states()
        q = queue.Queue()
        self.gravando = q
        antes, audio = [], []
        seguidos = silencio = 0
        comecou = False
        inicio = time.time()
        try:
            while True:
                if adiado.is_set():
                    return None                    # o usuário clicou "Agora não": descarta
                try:
                    b = q.get(timeout=1)
                except queue.Empty:
                    if time.time() - inicio > espera + maximo:
                        break
                    continue
                voz = float(self.vad.predict(b, frame_size=640)) > 0.5
                # nível para o anel verde: só pulsa quando é voz de verdade
                rms = float(np.sqrt(np.mean((b.astype(np.float32) / 32768) ** 2)))
                estado["nivel"] = round(min(1.0, rms * 18), 2) if voz else 0.0
                if not comecou:
                    antes = (antes + [b])[-5:]
                    seguidos = seguidos + 1 if voz else 0
                    if seguidos >= 3:              # 240 ms de voz: começou a falar
                        comecou = True
                        audio = list(antes)
                    elif time.time() - inicio > espera:
                        break
                else:
                    audio.append(b)
                    silencio = 0 if voz else silencio + 1
                    if silencio * self.SEG >= fim:
                        break
                    if len(audio) * self.SEG >= maximo:
                        return LONGA
        finally:
            self.gravando = None
            estado["nivel"] = 0.0
        if not comecou:
            return None
        caminho = os.path.join(jc.BASE, f"resp_{uuid.uuid4().hex[:6]}.wav")
        with wave.open(caminho, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.TAXA)
            w.writeframes(np.concatenate(audio).tobytes())
        return caminho


LONGA = "LONGA"
mic = Microfone()
MAX_FALA_TOTAL = 30.0   # segundos somando os trechos de uma resposta
MAX_PALAVRAS = 120


def parece_conversa_alheia(texto):
    """Transcrição longa demais ou com frase repetida em loop (alucinação do whisper)."""
    if len(texto.split()) > MAX_PALAVRAS:
        return True
    frases = [f.strip().lower() for f in texto.replace("!", ".").replace("?", ".").split(".") if f.strip()]
    return any(frases.count(f) >= 3 for f in set(frases) if len(f.split()) >= 3)


def mostrar(modo, texto=None, agente=None, ouvido=None):
    with trava:
        estado["modo"] = modo
        if texto is not None:
            estado["texto"] = texto
        if agente is not None:
            estado["agente"] = agente
        if ouvido is not None:
            estado["ouvido"] = ouvido
        estado["versao"] += 1


def envelope(arq, passo_ms=40):
    """Volume da fala a cada 40 ms (0 a 1): a boca do beagle abre junto com o áudio."""
    import numpy as np
    r = subprocess.run(["ffmpeg", "-v", "quiet", "-i", arq, "-f", "s16le", "-ac", "1",
                        "-ar", "8000", "-"], capture_output=True)
    a = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768
    n = 8 * passo_ms
    env = [float(np.sqrt(np.mean(a[i:i + n] ** 2))) for i in range(0, len(a), n)]
    topo = max(env) if env else 0
    return [round(min(1.0, e / topo * 1.4), 2) for e in env] if topo else []


def tocar(arq):
    """Toca a fala com a boca sincronizada. Pode ser interrompida (voz ou clique)."""
    try:
        env = envelope(arq)
        with trava:
            estado["boca"] = {"t0": int(time.time() * 1000), "passo": 40, "env": env}
            estado["versao"] += 1
        if not interrompido.is_set():
            fala_atual[0] = subprocess.Popen(["afplay", arq])
            fala_atual[0].wait()
    finally:
        fala_atual[0] = None
        with trava:
            estado["boca"] = None
            estado["versao"] += 1
        if os.path.exists(arq):
            os.remove(arq)


def falar(texto, agente="claude"):
    tocar(jc.sintetizar(texto, agente))


def parar_fala():
    interrompido.set()
    p = fala_atual[0]
    if p and p.poll() is None:
        p.terminate()
        jc.log("fala interrompida")


def eh_dispensa(texto):
    t = jc.normalizar(texto).replace("jarvis", "")
    t = " ".join("".join(c if c.isalnum() or c == " " else " " for c in t).split())
    return t in DISPENSAR


def tratar_comando(texto):
    """Comandos de voz que não são resposta para agente. Devolve True se era comando."""
    if eh_dispensa(texto):
        dispensar()
        return True
    t = jc.normalizar(texto)
    for m in MODOS:
        if f"modo {m}" in t and len(t.split()) <= 6:
            with open(ARQ_MODO, "w") as f:
                f.write(m)
            jc.log(f"modo: {m}")
            mostrar("falando", texto=f"Modo {m}.", agente="Jarvis")
            falar(f"Modo {m} ligado.")
            return True
    return False


def dispensar():
    with trava:
        estado["dispensado"] = True
        estado["versao"] += 1
    jc.log("dispensado pelo usuário")


def salvar_fila():
    """Guarda os recados em arquivo: um reinício do Jarvis ou do Mac não apaga a fila."""
    with trava:
        itens = [p for p in perguntas.values() if p["status"] == "aguardando"]
        dados = json.dumps({"recados": itens, "trabalhando": trabalhando}, ensure_ascii=False)
        estado["versao"] += 1
    with open(ARQ_FILA + ".tmp", "w") as f:
        f.write(dados)
    os.replace(ARQ_FILA + ".tmp", ARQ_FILA)


def carregar_fila():
    try:
        salvo = json.load(open(ARQ_FILA))
    except (OSError, ValueError):
        return
    itens = salvo.get("recados", []) if isinstance(salvo, dict) else salvo
    with trava:
        trabalhando.update(salvo.get("trabalhando", {}) if isinstance(salvo, dict) else {})
        for p in itens:
            p["contato"] = 0          # o hook que esperava pode ter morrido; se vivo, se registra de novo
            if not eh_pergunta(p.get("frase", "")):
                p["status"] = "informada"   # aviso que já tocou: não precisa ficar na fila
            perguntas[p["id"]] = p
    jc.log(f"fila recuperada: {len(itens)} recado(s)")


def tem_canal(p):
    """O hook da conversa ainda está esperando? (senão, a resposta não tem como chegar)"""
    return time.time() - p.get("contato", 0) < SEM_CANAL


def eh_pergunta(frase):
    """Só fica pendente o que pede algo do usuário: pergunta ou decisão."""
    return "?" in frase


def ultima_atividade(p):
    """Hora (epoch) da última atividade da conversa no arquivo dela, ou 0."""
    import datetime
    import glob
    caminho = p.get("transcript") or ""
    if not caminho or not os.path.exists(caminho):
        s = p.get("sessao") or ""
        achados = (glob.glob(os.path.join(jc.HOME, ".claude/projects/*", f"{s}.jsonl")) +
                   glob.glob(os.path.join(jc.HOME, ".codex/sessions/*/*/*", f"*{s}.jsonl"))) if s else []
        if not achados:
            return 0
        caminho = p["transcript"] = achados[0]
    try:
        with open(caminho, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 65536))
            linhas = f.read().decode("utf-8", "ignore").splitlines()
    except OSError:
        return 0
    ultimo = 0.0
    for linha in linhas:
        try:
            e = json.loads(linha)
        except ValueError:
            continue
        tipo = e.get("type")
        claude = tipo in ("user", "assistant")
        codex = tipo == "event_msg" and (e.get("payload") or {}).get("type") == "task_started"
        if (claude or codex) and e.get("timestamp"):
            try:
                t = datetime.datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            ultimo = max(ultimo, t)
    return ultimo


def resolver_andadas():
    """Recado de conversa que já seguiu (o usuário digitou nela, ou ela continuou) sai sozinho."""
    while True:
        time.sleep(10)
        for p in pendentes():
            if ultima_atividade(p) > p["criada"] + 5:
                with trava:
                    if p["status"] == "aguardando":
                        p["status"] = "resolvida"
                jc.log(f"[{p['agente']}:{p['projeto']}] resolvido: a conversa já seguiu")
                salvar_fila()


def em_andamento():
    with trava:
        agora = time.time()
        for k in [k for k, t in trabalhando.items() if agora - t["desde"] > 4 * 3600]:
            del trabalhando[k]
        return [t["assunto"] for t in trabalhando.values()]


def pendentes():
    with trava:
        return sorted((p for p in perguntas.values() if p["status"] == "aguardando"),
                      key=lambda p: p["criada"])


def ouvir(tentativas=2, espera=None):
    """Abre o microfone e devolve o que o usuário disse ('' se nada)."""
    interrompido.clear()
    for tentativa in range(tentativas):
        if adiado.is_set():
            return ""
        mostrar("ouvindo", ouvido="")
        jc.som("Tink")  # microfone aberto: pode falar
        gravacoes, total, longa = [], 0.0, False
        wav = mic.gravar(espera=espera or jc.ESPERA_INICIO_FALA)
        while wav:
            if wav == LONGA:
                longa = True
                break
            jc.som("Pop")  # captei esse trecho
            gravacoes.append(wav)
            total += jc.duracao(wav)
            if total > MAX_FALA_TOTAL:
                longa = True
                break
            wav = mic.gravar(espera=jc.CONTINUACAO)
        if longa:
            # fala comprida = conversa no ambiente, não resposta para o Jarvis: descarta
            for w in gravacoes:
                os.remove(w)
            jc.log("fala longa demais, descartada (conversa no ambiente?)")
            jc.som("Basso")
            mostrar("ouvindo", ouvido="")
            continue
        if gravacoes:
            mostrar("pensando")
            resposta = " ".join(t for t in (jc.transcrever(w) for w in gravacoes) if t)
            if adiado.is_set():                     # clicou "Agora não" durante a transcrição
                return ""
            if resposta and parece_conversa_alheia(resposta):
                jc.log("transcrição descartada (longa ou repetitiva)")
                resposta = ""
            if resposta:
                jc.som("Bottle")  # entendi
                mostrar("pensando", ouvido=resposta)
                return resposta
            jc.som("Basso")  # ouvi som, mas não entendi
        if tentativa < tentativas - 1 and not adiado.is_set():
            mostrar("falando")
            falar(f"{jc.NOME}?")
    return ""


def registrar_resposta(p, resposta):
    jc.log(f"[{p['agente']}:{p['projeto']}] {jc.NOME}: {resposta[:160]}")
    with trava:
        if p["status"] == "aguardando":
            p["resposta"] = resposta
            p["status"] = "respondida"
            if p.get("sessao"):
                trabalhando[p["sessao"]] = {"assunto": p["projeto"], "agente": p["agente"],
                                            "desde": time.time()}
    salvar_fila()


def conversar(p, com_sino=True):
    """Fala a pergunta p e tenta obter a resposta. True se respondida."""
    agente = p["agente"]
    nome = NOMES.get(agente, agente)
    texto = f"{jc.NOME}, {'o Codex aqui. ' if agente == 'codex' else ''}{p['frase']}"
    if not tem_canal(p):
        # o caminho de volta morreu (app do Claude ou Mac reiniciou): só avisa
        interrompido.clear()
        audio = jc.sintetizar(f"{jc.NOME}, recado de antes do reinício, {nome} no {p['projeto']}: "
                              f"{p['frase']} Essa você responde direto na conversa.", agente)
        mostrar("falando", texto=p["frase"] + " (responda direto na conversa)",
                agente=f"{nome} · {p['projeto']}", ouvido="")
        tocar(audio)
        with trava:
            p["status"] = "lida_sem_canal"
        salvar_fila()
        jc.log(f"[{agente}:{p['projeto']}] lido sem caminho de volta")
        return True
    interrompido.clear()                  # cada pergunta é lida, mesmo se a anterior foi cortada
    audio = jc.sintetizar(texto, agente)  # gera antes do sino: a fala sai colada nele
    mostrar("falando", texto=p["frase"], agente=f"{nome} · {p['projeto']}", ouvido="")
    if com_sino:
        jc.som("Glass")
    jc.log(f"[{agente}:{p['projeto']}] falando: {p['frase']}")
    tocar(audio)
    resposta = "" if adiado.is_set() else ouvir()
    if adiado.is_set():
        jc.log(f"[{agente}:{p['projeto']}] adiado pelo usuário (fica pendente)")
        return False
    if not resposta:
        jc.log(f"[{agente}:{p['projeto']}] sem resposta (fica pendente)")
        return False
    if tratar_comando(resposta):
        return "dispensa" if estado["dispensado"] else "comando"
    registrar_resposta(p, resposta)
    return True


def atender_chamado():
    """O o usuário disse 'Jarvis': repete o que está pendente, um por um."""
    with trava:
        estado["dispensado"] = False   # chamou: volta para a tela
    interrompido.clear()
    lista = pendentes()
    if not lista:
        ativos = em_andamento()
        if ativos:
            nomes = ", ".join(ativos[:-1]) + (" e " if len(ativos) > 1 else "") + ativos[-1]
            fala = (f"Oi, {jc.NOME}. Nada esperando você. "
                    f"{'Está' if len(ativos) == 1 else 'Estão'} trabalhando: {nomes}.")
        else:
            fala = f"Oi, {jc.NOME}. Nenhum agente está esperando por você agora."
        mostrar("falando", texto=fala.split(". ", 1)[1], agente="Jarvis")
        falar(fala)
        # escuta um instante: "pode ir" / "valeu" manda o Jarvis embora
        resposta = ouvir(tentativas=1, espera=6)
        if resposta:
            tratar_comando(resposta)
        return
    if len(lista) > 1:
        mostrar("falando", texto=f"{len(lista)} pendentes", agente="Jarvis")
        falar(f"{jc.NOME}, tem {len(lista)} coisas esperando você.")
    for p in lista:
        if adiado.is_set():
            break
        if p["status"] == "aguardando" and conversar(p, com_sino=False) == "dispensa":
            break


def responder_por_voz(pid):
    """O o usuário clicou no microfone de uma pendência na lista: só escuta (ele já leu)."""
    with trava:
        p = perguntas.get(pid)
    if not p or p["status"] != "aguardando":
        return
    nome = NOMES.get(p["agente"], p["agente"])
    mostrar("ouvindo", texto=p["frase"], agente=f"{nome} · {p['projeto']}", ouvido="")
    resposta = ouvir(tentativas=1)
    if resposta and not tratar_comando(resposta):
        registrar_resposta(p, resposta)


def anunciar(p):
    """Um agente deixou recado: age conforme o modo escolhido pelo o usuário."""
    modo = modo_atual()
    jc.log(f"[{p['agente']}:{p['projeto']}] pendente ({modo}): {p['frase']}")
    try:
        _anunciar(p, modo)
    finally:
        if not eh_pergunta(p["frase"]):
            # só um aviso ("terminei", "fechado"): não precisa de resposta, sai da fila
            with trava:
                if p["status"] == "aguardando":
                    p["status"] = "informada"
            salvar_fila()


def _anunciar(p, modo):
    if modo == "discreto":
        subprocess.run(["afplay", "-v", "0.35", "/System/Library/Sounds/Glass.aiff"], check=False)
        return
    with trava:
        estado["dispensado"] = False   # aviso faz o cachorrinho voltar para a tela
    subprocess.run(["caffeinate", "-u", "-t", "3"], check=False)  # acorda a tela
    if modo == "conversa":
        conversar(p)
        return
    # aviso: só uma frase, sem microfone
    nome = NOMES.get(p["agente"], p["agente"])
    interrompido.clear()
    audio = jc.sintetizar(f"{jc.NOME}, {nome} no {p['projeto']}: {p['frase']}", p["agente"])
    mostrar("falando", texto=p["frase"], agente=f"{nome} · {p['projeto']}", ouvido="")
    jc.som("Glass")
    tocar(audio)
    time.sleep(4)  # deixa o balão na tela um instante


def trabalhador():
    while True:
        item = fila.get()
        ocupado.set()
        adiado.clear()
        try:
            if item == "CHAMADO":
                atender_chamado()
            elif isinstance(item, tuple) and item[0] == "VOZ":
                responder_por_voz(item[1])
            else:
                # modo discreto: só um sininho baixinho; a pergunta fica guardada até o
                # o usuário dizer "Ei, Jarvis"
                with trava:
                    p = perguntas.get(item)
                if p and p["status"] == "aguardando":
                    anunciar(p)
        except Exception as e:  # noqa: BLE001 — o Jarvis não pode morrer
            jc.log(f"erro no trabalhador: {e!r}")
        finally:
            mostrar("oculto")
            time.sleep(0.8)  # não deixa o fim da própria fala disparar o "Jarvis"
            ocupado.clear()


def chamado_por_voz():
    if ocupado.is_set():
        return
    jc.log("chamado: Jarvis")
    ocupado.set()
    mostrar("pensando", texto=f"Oi, {jc.NOME}!", agente="Jarvis", ouvido="")  # aparece na hora
    fila.put("CHAMADO")


def detector_palavra():
    """Escuta "Jarvis" o tempo todo (openWakeWord, local). Fica surdo enquanto o Jarvis fala."""
    try:
        import numpy as np
        from openwakeword.model import Model
    except ImportError as e:
        jc.log(f"detector de 'Jarvis' indisponível: {e!r}")
        return
    modelo = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    limiar = float(os.environ.get("JARVIS_LIMIAR_PALAVRA", "0.3"))
    ultimo = [0.0]

    def ouvir_bloco(b):
        falando = fala_atual[0] is not None
        if ocupado.is_set() and not falando:
            modelo.reset()          # ouvindo a resposta: a palavra não conta
            return
        nota = float(max(modelo.predict(np.asarray(b)).values()))
        if nota >= 0.2:
            jc.log(f"palavra: nota {nota:.2f}")
        # durante a fala a própria voz sai na caixa: pede nota mais alta para interromper
        if nota >= (max(limiar, 0.5) if falando else limiar) and time.time() - ultimo[0] > 3:
            ultimo[0] = time.time()
            modelo.reset()
            if falando:
                parar_fala()
            else:
                chamado_por_voz()

    mic.ouvintes.append(ouvir_bloco)
    jc.log("detector de 'Jarvis' ligado")


class Servidor(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, codigo=200):
        corpo = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path in ("/", "/ui.html"):
            corpo = open(os.path.join(jc.BASE, "ui.html"), "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)
        elif url.path == "/estado":
            with trava:
                e = dict(estado)
            e["modo_jarvis"] = modo_atual()
            e["pendentes"] = [{"id": p["id"], "agente": NOMES.get(p["agente"], p["agente"]),
                               "projeto": p["projeto"], "frase": p["frase"],
                               "canal": tem_canal(p)}
                              for p in pendentes()]
            e["trabalhando"] = em_andamento()
            self._json(e)
        elif url.path == "/resposta":
            # long-poll: o hook fica aqui esperando a resposta do usuário
            pid = q.get("id", [""])[0]
            limite = time.time() + min(float(q.get("espera", ["25"])[0]), 55)
            while time.time() < limite:
                with trava:
                    p = perguntas.get(pid)
                    if not p:
                        return self._json({"status": "desconhecida"})
                    p["contato"] = time.time()
                    if p["status"] != "aguardando":
                        return self._json({"status": p["status"], "resposta": p["resposta"]})
                time.sleep(0.3)
            self._json({"status": "aguardando"})
        elif url.path == "/nivel":
            # diagnóstico: grava 2 s pelo mesmo caminho das respostas e mede o volume
            wav = os.path.join(jc.BASE, "nivel.wav")
            subprocess.run(["rec", "-q", "-c", "1", wav, "trim", "0", "2"], check=False)
            r = subprocess.run(["sox", wav, "-n", "stat"], capture_output=True, text=True)
            self._json({"stat": r.stderr})
        elif url.path == "/vivo":
            self._json({"ok": True})
        else:
            self._json({"erro": "não encontrado"}, 404)

    def do_POST(self):
        url = urlparse(self.path)
        tamanho = int(self.headers.get("Content-Length") or 0)
        dados = json.loads(self.rfile.read(tamanho) or b"{}")
        if url.path == "/pergunta":
            pid = uuid.uuid4().hex[:10]
            sessao = dados.get("sessao", "")
            with trava:
                # recado novo da mesma conversa substitui o anterior
                for p in perguntas.values():
                    if sessao and p.get("sessao") == sessao and p["status"] == "aguardando":
                        p["status"] = "expirada"
                perguntas[pid] = {"id": pid, "agente": dados.get("agente", "claude"),
                                  "projeto": dados.get("projeto", ""), "sessao": sessao,
                                  "frase": dados.get("frase", ""), "status": "aguardando",
                                  "resposta": "", "criada": time.time(), "contato": time.time(),
                                  "transcript": dados.get("transcript", "")}
                trabalhando.pop(sessao, None)   # terminou de trabalhar e deixou recado
            salvar_fila()
            if not dados.get("reenvio"):   # reenvio após reinício: volta para a fila sem anunciar
                fila.put(pid)
            self._json({"id": pid})
        elif url.path == "/encerrar":
            # o agente desistiu de esperar (timeout do hook) ou o usuário dispensou na tela
            with trava:
                p = perguntas.get(dados.get("id", ""))
                if p and p["status"] == "aguardando":
                    p["status"] = "expirada"
            salvar_fila()
            self._json({"ok": True})
        elif url.path == "/encerrar_sessao":
            # o usuário foi direto na conversa e digitou: o recado dela sai da fila
            sessao = dados.get("sessao", "")
            with trava:
                for p in perguntas.values():
                    if sessao and p.get("sessao") == sessao and p["status"] == "aguardando":
                        p["status"] = "expirada"
            salvar_fila()
            self._json({"ok": True})
        elif url.path == "/trabalhando":
            # o usuário mandou mensagem numa conversa: ela está trabalhando
            sessao = dados.get("sessao", "")
            if sessao:
                with trava:
                    trabalhando[sessao] = {"assunto": dados.get("assunto", ""),
                                           "agente": dados.get("agente", "claude"),
                                           "desde": time.time()}
                salvar_fila()
            self._json({"ok": True})
        elif url.path == "/chamar":
            chamado_por_voz()
            self._json({"ok": True})
        elif url.path == "/dispensar":
            dispensar()
            parar_fala()
            self._json({"ok": True})
        elif url.path == "/adiar":
            adiado.set()
            parar_fala()
            jc.log("Agora não: conversa adiada")
            self._json({"ok": True})
        elif url.path == "/interromper":
            parar_fala()
            self._json({"ok": True})
        elif url.path == "/responder":
            # resposta escrita na lista de pendências
            with trava:
                p = perguntas.get(dados.get("id", ""))
            texto = (dados.get("texto") or "").strip()
            if p and texto:
                registrar_resposta(p, texto)
            self._json({"ok": bool(p and texto)})
        elif url.path == "/responder_voz":
            fila.put(("VOZ", dados.get("id", "")))
            self._json({"ok": True})
        else:
            self._json({"erro": "não encontrado"}, 404)


def limpar_antigas():
    while True:
        time.sleep(60)
        with trava:
            for pid in [k for k, p in perguntas.items()
                        if p["status"] != "aguardando" and time.time() - p["criada"] > 3600]:
                del perguntas[pid]


def main():
    # carrega o openWakeWord antes das threads: importar em paralelo dá import circular
    import openwakeword.model  # noqa: F401
    import openwakeword.vad  # noqa: F401
    carregar_fila()
    threading.Thread(target=mic.rodar, daemon=True).start()
    threading.Thread(target=trabalhador, daemon=True).start()
    threading.Thread(target=limpar_antigas, daemon=True).start()
    threading.Thread(target=resolver_andadas, daemon=True).start()
    if not os.environ.get("JARVIS_SEM_PALAVRA"):
        detector_palavra()
    jc.log(f"jarvisd no ar na porta {PORTA}")
    ThreadingHTTPServer(("127.0.0.1", PORTA), Servidor).serve_forever()


if __name__ == "__main__":
    main()
