# Jarvis

Assistente de voz para quem trabalha com **Claude Code** e **Codex** no Mac. Quando um agente
termina uma tarefa ou precisa de uma decisão, um beagle aparece no canto da tela, fala o próximo
passo e escuta a sua resposta — você responde por voz, sem sair do que está fazendo, e a resposta
vai para a conversa certa.

Tudo roda no seu Mac e é gratuito: transcrição local (whisper.cpp), detector local de "Ei, Jarvis"
(openWakeWord). Só a voz neural (edge-tts) usa internet; sem internet ele cai para a voz do macOS.

## Requisitos

- macOS 13 ou mais novo (testado em Mac com chip Apple)
- [Homebrew](https://brew.sh) e as ferramentas de linha de comando (`xcode-select --install`)
- Claude Code (app desktop ou terminal) e, se quiser, o Codex
- ~1 GB livre (modelos de transcrição e voz)

## Instalar

```bash
git clone <url-deste-repositório> ~/Jarvis
```

```bash
cd ~/Jarvis && ./instalar.sh
```

O instalador pergunta como o Jarvis deve te chamar, instala as dependências, baixa os modelos,
compila o app, liga os hooks no Claude Code e no Codex (sem apagar os que você já tem; guarda uma
cópia `.antes-do-jarvis`) e faz o Jarvis abrir no início da sessão.

Depois:
1. Na primeira vez, o macOS pede **permissão de microfone** para o Jarvis — clique em Permitir.
2. **Codex**: aprove os hooks uma vez — rode `codex` no terminal, digite `/hooks` e aprove.
3. Conversas do Claude abertas antes da instalação não usam o Jarvis; abra conversas novas.

## Como usar

| Você diz / faz | O Jarvis |
|---|---|
| "Ei, Jarvis" | aparece e lê o que está pendente (ou diz quais conversas estão trabalhando) |
| responde depois do som curto | leva a resposta para a conversa que perguntou |
| "esconde", "pode ir", "valeu" | some da tela até o próximo aviso |
| "modo conversa" | ao terminar, o agente fala o próximo passo e o microfone abre (padrão) |
| "modo aviso" | só uma frase curta, sem abrir o microfone |
| "modo discreto" | só um sininho e o número de pendências no cachorrinho |
| botão **Agora não** | para de falar e de ouvir; a pergunta fica guardada |
| clique no número | lista de pendências; responda pelo 🎤 ou escrevendo |
| "Ei, Jarvis" ou clique no beagle enquanto ele fala | interrompe e escuta |

O "?" ao lado do cachorrinho mostra esses comandos.

Sons: sino = aviso · *tink* = microfone aberto · *pop* = trecho captado · *bottle* = entendi ·
*basso* = não entendi. O anel verde pulsa com a sua voz enquanto ele escuta.

## Configuração

`config.json` (criado na instalação a partir de `config.exemplo.json`):

```json
{
  "nome": "SeuNome",
  "vozes": {"claude": "pt-BR-ThalitaMultilingualNeural", "codex": "pt-BR-AntonioNeural"},
  "voz_mac": "Luciana"
}
```

Desligar temporariamente: `touch ~/Jarvis/OFF` (e `rm ~/Jarvis/OFF` para religar).
Remover: `./desinstalar.sh`.

## Como funciona

- **`jarvis_hook.py`** — hook `Stop` do Claude Code e do Codex. Pega a última linha `🔊` da
  resposta do agente e deixa um recado no `jarvisd`. No Claude, roda em segundo plano
  (`asyncRewake`): a conversa fica livre e só é acordada quando a sua resposta chega. No Codex,
  espera até 30 minutos.
- **`jarvis_contexto.py`** — hook `SessionStart`: ensina o agente a terminar cada resposta com uma
  linha `🔊` curta, falável, e a confirmar o plano antes de executar um pedido novo ("Ok?").
- **`jarvis_cancelar.py`** — hook `UserPromptSubmit`: quando você digita direto numa conversa, o
  recado dela sai da fila e ela passa a contar como "trabalhando".
- **`jarvisd.py`** — o programa sempre ligado (HTTP local em `127.0.0.1:8765`): fila de recados
  (salva em `fila.json`), voz, microfone com detector de fala (Silero VAD), transcrição, detector
  de "Ei, Jarvis", modos, e a limpeza automática de recados resolvidos (aviso sem pergunta, ou
  conversa que já seguiu).
- **`ui.html`** — o beagle e o balão, desenhados em SVG/CSS.
- **`app/main.swift`** — o app nativo: janela flutuante que não rouba foco nem cliques (só aceita
  clique em cima do beagle e da lista) e que inicia o `jarvisd`.

## Privacidade e cuidados

- O microfone fica aberto só para detectar "Ei, Jarvis" (localmente) e, quando o Jarvis pergunta,
  para gravar a resposta. Nada de áudio sai do Mac.
- Fala com mais de 25 segundos seguidos, ou transcrição longa/repetitiva, é tratada como conversa
  do ambiente e **descartada** — para não mandar conversa alheia como resposta a um agente.
- Ainda assim, o que o Jarvis transcreve vira instrução para um agente. Por isso os agentes são
  orientados a confirmar ("Ok?") antes de executar um pedido novo. Use o botão **Agora não** quando
  estiver com áudio tocando ou conversando com alguém.
- `jarvis.log` registra só o começo de cada resposta e fica fora do Git.
