"""Resumo com IA gratuita (Google Gemini, plano gratuito) e alternativa sem IA.

Ordem de tentativa:
  1. Gemini (GEMINI_API_KEY)   — modelos em GEMINI_MODELS, um a seguir ao outro
  2. Groq   (GROQ_API_KEY)     — opcional, também gratuito
  3. Resumo extrativo local    — sem IA, nunca falha

A IA devolve APENAS JSON com ids dos itens (n1, n2…). Os URLs e o HTML são
construídos pelo nosso código -> sem links inventados e sem erros de parsing.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

import requests

from .recolha import Item

log = logging.getLogger(__name__)

TOPICOS = [
    ("📢", "Anúncios oficiais"),
    ("🚀", "Lançamentos e atualizações"),
    ("🔌", "MCP"),
    ("🤖", "Agentes e Skills"),
    ("🛠️", "Ferramentas e projetos"),
    ("💡", "Dicas e tutoriais"),
    ("📰", "Imprensa"),
    ("💬", "Comunidade"),
]
EMOJI_TOPICO = {t: e for e, t in TOPICOS}
CATEGORIA_PARA_TOPICO = {
    "oficial": "Anúncios oficiais",
    "lancamentos": "Lançamentos e atualizações",
    "mcp": "MCP",
    "tutoriais": "Dicas e tutoriais",
    "imprensa": "Imprensa",
    "comunidade": "Comunidade",
}

GEMINI_MODELS_DEFAULT = "gemini-flash-latest,gemini-2.5-flash,gemini-flash-lite-latest"
GROQ_MODEL_DEFAULT = "llama-3.3-70b-versatile"

PROMPT = """És editor de um boletim técnico diário sobre o Claude (Anthropic) para um profissional
que usa Claude, Claude Code, MCP, agentes e skills. Escreve em português de Portugal (pt-PT),
nunca em português do Brasil (usa "ecrã", "utilizador", "ficheiro", "telemóvel", "equipa").

Recebes uma lista JSON de itens recolhidos de feeds RSS nas últimas 24 horas.
Tarefas:
1. Ignora publicidade, conteúdo promocional, duplicados (mesma notícia em fontes diferentes:
   fica só com a melhor) e itens sem relação com Claude/Anthropic/MCP/agentes de IA.
2. Agrupa os restantes nestes tópicos (usa exatamente estes nomes; omite tópicos vazios):
   {topicos}
3. Para cada item escreve um título curto em pt-PT (máx. 90 caracteres) e um resumo
   direto ao assunto de 1 frase (máx. 200 caracteres): o que é e porque interessa.
   Não inventes factos que não estejam no texto do item.
4. Escreve um "destaque" de 1–2 frases (máx. 280 caracteres) com o mais importante do dia.
5. No máximo {max_itens} itens no total; dá prioridade a anúncios oficiais, lançamentos,
   novidades práticas (MCP, skills, ferramentas) e dicas úteis.

Responde APENAS com JSON válido, sem markdown, neste formato:
{{"destaque": "…", "topicos": [{{"topico": "<nome do tópico>", "itens": [{{"id": "n3", "titulo": "…", "resumo": "…"}}]}}]}}
O campo "id" tem de ser um dos ids recebidos.

Itens:
{itens}
"""


# ------------------------------------------------------------------- modelos
def _prompt(itens: list[Item], max_itens: int) -> str:
    dados = [
        {"id": i.id, "fonte": i.fonte, "categoria": i.categoria, "titulo": i.titulo, "texto": i.descricao[:400]}
        for i in itens
    ]
    return PROMPT.format(
        topicos=", ".join(t for _, t in TOPICOS),
        max_itens=max_itens,
        itens=json.dumps(dados, ensure_ascii=False),
    )


def _post(url: str, headers: dict, corpo: dict, tentativas: int = 2) -> dict:
    ultimo = None
    for n in range(tentativas):
        r = requests.post(url, headers=headers, json=corpo, timeout=120)
        if r.status_code == 200:
            return r.json()
        ultimo = f"HTTP {r.status_code}: {r.text[:300]}"
        if r.status_code in (429, 500, 502, 503, 504) and n + 1 < tentativas:
            time.sleep(20)  # limite por minuto do plano gratuito / sobrecarga
            continue
        break
    raise RuntimeError(ultimo)


def _gemini(prompt: str, chave: str, modelo: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
    corpo = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 8192, "responseMimeType": "application/json"},
    }
    dados = _post(url, {"x-goog-api-key": chave, "Content-Type": "application/json"}, corpo)
    partes = dados["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in partes if not p.get("thought"))


def _groq(prompt: str, chave: str, modelo: str) -> str:
    corpo = {
        "model": modelo,
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": prompt}],
    }
    dados = _post("https://api.groq.com/openai/v1/chat/completions",
                  {"Authorization": f"Bearer {chave}", "Content-Type": "application/json"}, corpo)
    return dados["choices"][0]["message"]["content"]


# --------------------------------------------------------------- validação
def _extrair_json(texto: str) -> dict:
    texto = texto.strip()
    texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto)
    ini, fim = texto.find("{"), texto.rfind("}")
    if ini < 0 or fim < 0:
        raise ValueError("resposta sem JSON")
    return json.loads(texto[ini: fim + 1])


def _validar(bruto: dict, itens: list[Item]) -> dict:
    """Mantém só ids válidos, sem repetições, com tópicos conhecidos; devolve estrutura final."""
    por_id = {i.id: i for i in itens}
    usados, topicos = set(), {}
    for bloco in bruto.get("topicos", []) or []:
        nome = str(bloco.get("topico", "")).strip()
        if nome not in EMOJI_TOPICO:
            nome = "Ferramentas e projetos"
        for x in bloco.get("itens", []) or []:
            iid = str(x.get("id", "")).strip()
            if iid not in por_id or iid in usados:
                continue
            usados.add(iid)
            orig = por_id[iid]
            topicos.setdefault(nome, []).append({
                "titulo": (str(x.get("titulo") or orig.titulo).strip())[:120],
                "resumo": str(x.get("resumo") or "").strip()[:260],
                "url": orig.url,
                "fonte": orig.fonte,
            })
    if not topicos:
        raise ValueError("JSON sem itens válidos")
    ordem = [t for _, t in TOPICOS if t in topicos]
    return {
        "destaque": str(bruto.get("destaque") or "").strip()[:320],
        "topicos": [{"emoji": EMOJI_TOPICO[t], "topico": t, "itens": topicos[t]} for t in ordem],
        "com_ia": True,
    }


# ----------------------------------------------------------- sem IA (fallback)
def resumo_extrativo(itens: list[Item], max_itens: int) -> dict:
    topicos: dict[str, list] = {}
    for it in itens[:max_itens]:
        nome = CATEGORIA_PARA_TOPICO.get(it.categoria, "Ferramentas e projetos")
        resumo = it.descricao[:200].rsplit(" ", 1)[0] + "…" if len(it.descricao) > 200 else it.descricao
        topicos.setdefault(nome, []).append({"titulo": it.titulo, "resumo": resumo, "url": it.url, "fonte": it.fonte})
    ordem = [t for _, t in TOPICOS if t in topicos]
    return {
        "destaque": "",
        "topicos": [{"emoji": EMOJI_TOPICO[t], "topico": t, "itens": topicos[t]} for t in ordem],
        "com_ia": False,
    }


# --------------------------------------------------------------------- API
def resumir(itens: list[Item], max_itens: int = 25) -> dict:
    if not itens:
        return {"destaque": "", "topicos": [], "com_ia": False}
    prompt = _prompt(itens, max_itens)

    tentativas = []
    if os.getenv("GEMINI_API_KEY"):
        for m in (os.getenv("GEMINI_MODELS") or GEMINI_MODELS_DEFAULT).split(","):
            if m.strip():
                tentativas.append((f"Gemini {m.strip()}", lambda m=m.strip(): _gemini(prompt, os.environ["GEMINI_API_KEY"], m)))
    if os.getenv("GROQ_API_KEY"):
        m = os.getenv("GROQ_MODEL") or GROQ_MODEL_DEFAULT
        tentativas.append((f"Groq {m}", lambda: _groq(prompt, os.environ["GROQ_API_KEY"], m)))

    for nome, chamar in tentativas:
        try:
            resultado = _validar(_extrair_json(chamar()), itens)
            log.info("Resumo gerado com %s", nome)
            return resultado
        except Exception as exc:  # noqa: BLE001
            log.warning("Falhou %s: %s", nome, str(exc)[:300])

    log.warning("Sem IA disponível — a usar resumo extrativo.")
    return resumo_extrativo(itens, max_itens)
