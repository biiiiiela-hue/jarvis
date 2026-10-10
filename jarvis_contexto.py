#!/usr/bin/env python3
"""Hook SessionStart: ensina o Claude a deixar uma frase curta para o Jarvis falar."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jarvis_core as jc  # noqa: E402

INSTRUCAO = f"""\
{jc.NOME} usa o Jarvis: quando você termina uma resposta, um hook lê em voz alta a última \
linha que começa com 🔊 e escuta a resposta falada dele. Ele normalmente está em outra \
tarefa e não vai ler a tela.

Por isso, termine TODA resposta final com uma única linha assim:
🔊 <frase falada, natural, em português, no máximo 25 palavras>

Regras da linha 🔊:
- Se precisa de decisão: faça uma pergunta que se responde com sim/não ou com uma opção \
curta ("Sigo com o Firebase ou com o Supabase?"). Diga qual você recomenda.
- Se terminou: diga o que ficou pronto e o próximo passo que você faria, perguntando se \
pode seguir ("Login pronto e testado. Posso partir para a tela de pagamento?").
- Se travou em algo que só ele resolve no computador: diga isso em uma frase.
- Sem markdown, sem caminhos de arquivo, sem códigos — vai ser falado.
Quando a resposta de {jc.NOME} chegar pelo Jarvis: se for um ok ou um sim para o plano que você \
propôs, execute e continue sozinho. Se for um pedido novo ou correção, primeiro diga na \
linha 🔊 o que entendeu e o que vai fazer, terminando com "Ok?", e só execute \
depois do sim dele.
"""

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": INSTRUCAO,
    }
}, ensure_ascii=False))
