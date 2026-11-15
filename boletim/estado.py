"""Estado persistente (state/seen.json), guardado no próprio repositório.

Serve para:
  * não repetir itens já enviados (deduplicação entre dias);
  * aceitar com segurança itens SEM data (só se nunca vistos antes);
  * saber quando foi o último envio (evita janelas com buracos e envios duplicados).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

DIAS_RETENCAO = 10


class Estado:
    def __init__(self, caminho: str | os.PathLike):
        self.caminho = Path(caminho)
        self.vistos: dict[str, str] = {}      # hash -> ISO da última vez visto
        self.ultimo_envio: datetime | None = None
        self.ultima_data_lisboa: str | None = None  # "AAAA-MM-DD" do último envio agendado
        self._carregar()

    # ------------------------------------------------------------------ io
    def _carregar(self) -> None:
        if not self.caminho.exists():
            return
        try:
            dados = json.loads(self.caminho.read_text(encoding="utf-8") or "{}")
        except (json.JSONDecodeError, OSError):
            dados = {}  # ficheiro corrompido -> recomeça sem rebentar
        self.vistos = dict(dados.get("vistos", {}))
        if dados.get("ultimo_envio"):
            try:
                self.ultimo_envio = datetime.fromisoformat(dados["ultimo_envio"])
            except ValueError:
                self.ultimo_envio = None
        self.ultima_data_lisboa = dados.get("ultima_data_lisboa")

    def guardar(self) -> None:
        self._podar()
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        dados = {
            "ultimo_envio": self.ultimo_envio.isoformat() if self.ultimo_envio else None,
            "ultima_data_lisboa": self.ultima_data_lisboa,
            "vistos": dict(sorted(self.vistos.items())),
        }
        self.caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    # --------------------------------------------------------------- lógica
    @property
    def primeira_execucao(self) -> bool:
        return self.ultimo_envio is None

    def ja_visto(self, chave: str) -> bool:
        return chave in self.vistos

    def marcar(self, chaves, agora: datetime) -> None:
        iso = agora.astimezone(timezone.utc).isoformat(timespec="seconds")
        for c in chaves:
            self.vistos[c] = iso  # "visto pela última vez" -> itens perenes não reaparecem

    def _podar(self) -> None:
        limite = datetime.now(timezone.utc) - timedelta(days=DIAS_RETENCAO)
        novos = {}
        for k, v in self.vistos.items():
            try:
                if datetime.fromisoformat(v) >= limite:
                    novos[k] = v
            except ValueError:
                continue
        self.vistos = novos
