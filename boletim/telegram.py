"""Formatação HTML segura e envio para o Telegram.

Regras:
  * Todo o texto vindo de feeds/IA passa por html.escape -> nunca há tags partidas.
  * Só usamos <b>, <i> e <a href> (suportados pelo parse_mode=HTML do Telegram).
  * Limite por mensagem: 4000 caracteres (o Telegram aceita 4096; fica margem).
  * Até 2 mensagens; se ainda assim não couber, corta os últimos itens e indica quantos.
  * Se o Telegram recusar o HTML, reenvia a mesma mensagem em texto simples.
"""
from __future__ import annotations

import html
import logging
import re
import time
from datetime import datetime

import requests

log = logging.getLogger(__name__)

LIMITE = 4000
MAX_MENSAGENS = 2
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]


def esc(t: str) -> str:
    return html.escape(t or "", quote=False)


def href(url: str) -> str | None:
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        return None
    return html.escape(url, quote=True)


# ------------------------------------------------------------------ blocos
def _bloco_item(it: dict) -> str:
    titulo = esc(it["titulo"])
    h = href(it.get("url", ""))
    linha = f'• <b><a href="{h}">{titulo}</a></b>' if h else f"• <b>{titulo}</b>"
    linha += f" <i>({esc(it.get('fonte', ''))})</i>" if it.get("fonte") else ""
    if it.get("resumo"):
        linha += f"\n{esc(it['resumo'])}"
    if len(linha) > LIMITE - 200:  # item absurdo: corta só o resumo
        linha = linha[: LIMITE - 200].rsplit(" ", 1)[0] + "…"
        linha = re.sub(r"<[^>]*$", "", linha)
    return linha


def construir_blocos(resumo: dict, data_local: datetime, n_fontes_ok: int, n_recolhidos: int) -> list[tuple[str, str]]:
    """Devolve lista de (tipo, html). Tipos: cab, top, item, rod. Facilita a divisão."""
    data_txt = f"{data_local.day} de {MESES[data_local.month - 1]} de {data_local.year}"
    blocos = [("cab", f"🧠 <b>Boletim Claude — {esc(data_txt)}</b>")]
    if resumo.get("destaque"):
        blocos.append(("cab", f"⭐ <b>Destaque:</b> {esc(resumo['destaque'])}"))
    if not resumo.get("topicos"):
        blocos.append(("cab", "😴 Sem novidades relevantes nas últimas 24 horas."))
    for t in resumo.get("topicos", []):
        blocos.append(("top", f"{t['emoji']} <b>{esc(t['topico'])}</b>"))
        for it in t["itens"]:
            blocos.append(("item", _bloco_item(it)))
    modo = "IA" if resumo.get("com_ia") else "sem IA"
    blocos.append(("rod", f"<i>{n_recolhidos} itens analisados · {n_fontes_ok} fontes · resumo {modo}</i>"))
    return blocos


def dividir(blocos: list[tuple[str, str]], limite: int = LIMITE, max_msgs: int = MAX_MENSAGENS) -> list[str]:
    """Junta blocos em mensagens ≤ limite, sem partir blocos; no máx. max_msgs."""
    def juntar(bs):
        return "\n\n".join(h for _, h in bs)

    if len(juntar(blocos)) <= limite:
        return [juntar(blocos)]

    rodape = [b for b in blocos if b[0] == "rod"]
    corpo = [b for b in blocos if b[0] != "rod"]
    msgs, atual = [], []
    omitidos = 0
    for i, b in enumerate(corpo):
        candidato = atual + [b]
        if len(juntar(candidato)) <= limite:
            atual = candidato
            continue
        # não deixar um título de tópico órfão no fim da mensagem
        transportar = []
        if atual and atual[-1][0] == "top":
            transportar = [atual.pop()]
        if len(msgs) + 1 < max_msgs:
            msgs.append(atual)
            atual = transportar + [b]
        else:
            omitidos = sum(1 for x in corpo[i:] if x[0] == "item")
            break
    # rodapé + aviso de omitidos na última mensagem
    def aviso(n):
        if not n:
            return []
        txt = "mais 1 item omitido" if n == 1 else f"mais {n} itens omitidos"
        return [("rod", f"<i>… e {txt} por falta de espaço.</i>")]

    extra = aviso(omitidos) + rodape
    while atual and len(juntar(atual + extra)) > limite:
        removido = atual.pop()
        if removido[0] == "item":
            omitidos += 1
            extra = aviso(omitidos) + rodape
    while atual and atual[-1][0] == "top":
        atual.pop()
    msgs.append(atual + extra)
    return [juntar(m) for m in msgs if m]


# ------------------------------------------------------------------- envio
def _para_texto(html_txt: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", html_txt))


def enviar(token: str, chat_id: str, mensagens: list[str]) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for n, msg in enumerate(mensagens, 1):
        corpo = {"chat_id": chat_id, "text": msg, "parse_mode": "HTML",
                 "link_preview_options": {"is_disabled": True}}
        for tentativa in range(4):
            r = requests.post(url, json=corpo, timeout=30)
            if r.ok:
                log.info("Mensagem %d/%d enviada (%d caracteres).", n, len(mensagens), len(msg))
                break
            dados = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            descricao = dados.get("description", r.text[:200])
            if r.status_code == 429:
                espera = int(dados.get("parameters", {}).get("retry_after", 5))
                time.sleep(espera + 1)
                continue
            if r.status_code == 400 and "parse" in descricao.lower() and "parse_mode" in corpo:
                log.warning("HTML recusado (%s) — a reenviar em texto simples.", descricao)
                corpo.pop("parse_mode")
                corpo["text"] = _para_texto(msg)[:4096]
                continue
            raise RuntimeError(f"Telegram HTTP {r.status_code}: {descricao}")
        else:
            raise RuntimeError("Telegram: tentativas esgotadas")
        time.sleep(1)
