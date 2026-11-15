#!/usr/bin/env python3
"""Ponto de entrada do boletim diário sobre o Claude.

Uso:
  python main.py                 # execução manual: envia sempre, não altera o estado
  python main.py --agendado      # execução do cron: só envia se forem ≥ 18h em Lisboa
                                 # e se ainda não tiver enviado hoje; atualiza o estado
  python main.py --dry-run       # não envia; mostra as mensagens no terminal
  python main.py --guardar       # força a atualização do estado numa execução manual

Variáveis de ambiente:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   (obrigatórias, exceto em --dry-run)
  GEMINI_API_KEY                         (recomendada, gratuita)
  GROQ_API_KEY                           (opcional, gratuita, usada se o Gemini falhar)
  HORA_ENVIO (default 18), FEEDS_FILE (default feeds.yaml), ESTADO_FILE (default state/seen.json)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from boletim.estado import Estado
from boletim.recolha import recolher
from boletim.resumo import resumir
from boletim.telegram import construir_blocos, dividir, enviar

FUSO = ZoneInfo("Europe/Lisbon")
RAIZ = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser(description="Boletim diário Claude -> Telegram")
    ap.add_argument("--agendado", action="store_true", help="modo cron (verifica a hora de Lisboa)")
    ap.add_argument("--dry-run", action="store_true", help="não envia nem grava estado")
    ap.add_argument("--guardar", action="store_true", help="grava estado mesmo em execução manual")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log = logging.getLogger("boletim")

    agora = datetime.now(timezone.utc)
    lisboa = agora.astimezone(FUSO)
    hora_envio = int(os.getenv("HORA_ENVIO", "18"))
    estado = Estado(os.getenv("ESTADO_FILE", RAIZ / "state" / "seen.json"))

    if args.agendado:
        if lisboa.hour < hora_envio:
            log.info("São %s em Lisboa — ainda não é hora (%dh). Nada a fazer.", lisboa.strftime("%H:%M"), hora_envio)
            return 0
        if estado.ultima_data_lisboa == lisboa.date().isoformat():
            log.info("Boletim de hoje (%s) já foi enviado. Nada a fazer.", estado.ultima_data_lisboa)
            return 0

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not args.dry_run and not (token and chat_id):
        log.error("Faltam TELEGRAM_BOT_TOKEN e/ou TELEGRAM_CHAT_ID.")
        return 2

    with open(os.getenv("FEEDS_FILE", RAIZ / "feeds.yaml"), encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    stats: dict = {}
    itens, sem_data = recolher(config, estado, agora, max_itens=int(os.getenv("MAX_ITENS_IA", "60")), stats=stats)
    log.info("Fontes OK: %d/%d · itens brutos: %d · itens novos e relevantes: %d",
             stats.get("fontes_ok", 0), stats.get("fontes_total", 0), stats.get("brutos", 0), len(itens))

    resumo = resumir(itens, max_itens=int(os.getenv("MAX_ITENS_BOLETIM", "25")))
    blocos = construir_blocos(resumo, lisboa, stats.get("fontes_ok", 0), len(itens))
    mensagens = dividir(blocos)

    if args.dry_run:
        for n, m in enumerate(mensagens, 1):
            print(f"\n===== Mensagem {n}/{len(mensagens)} ({len(m)} caracteres) =====\n{m}")
        return 0

    try:
        enviar(token, chat_id, mensagens)
    except Exception as exc:  # noqa: BLE001
        log.error("Falha no envio para o Telegram: %s", exc)
        return 1

    if args.agendado or args.guardar:
        # Marca TODOS os itens analisados (incluindo os que a IA descartou como
        # publicidade/irrelevantes) para não voltarem amanhã.
        estado.marcar([i.chave for i in itens] + list(sem_data), agora)
        estado.ultimo_envio = agora
        if args.agendado:
            estado.ultima_data_lisboa = lisboa.date().isoformat()
        estado.guardar()
        log.info("Estado atualizado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
