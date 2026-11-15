"""Recolha de feeds RSS/Atom, normalização, filtragem por data, relevância e publicidade."""
from __future__ import annotations

import calendar
import hashlib
import html
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import requests

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; BoletimClaude/1.0; +https://github.com)"
TIMEOUT = 20
TOLERANCIA_FUTURO = timedelta(hours=2)  # datas ligeiramente no futuro (fusos mal declarados)
MAX_DESC = 500


@dataclass
class Item:
    titulo: str
    url: str
    fonte: str
    categoria: str
    descricao: str = ""
    data: datetime | None = None
    id: str = field(default="")  # id curto atribuído antes do resumo (n1, n2...)

    @property
    def chave(self) -> str:
        """Identificador estável para deduplicação (URL canónico, senão título)."""
        base = url_canonico(self.url) or normalizar_titulo(self.titulo)
        return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


# ----------------------------------------------------------------- utilitários
_TAGS = re.compile(r"<[^>]+>")
_ESPACOS = re.compile(r"\s+")
_PARAMS_LIXO = re.compile(r"^(utm_|fbclid|gclid|ref$|ref_src|mc_)")


def limpar_texto(texto: str, maximo: int | None = None) -> str:
    texto = html.unescape(_TAGS.sub(" ", texto or ""))
    texto = _ESPACOS.sub(" ", texto).strip()
    if maximo and len(texto) > maximo:
        texto = texto[: maximo - 1].rsplit(" ", 1)[0] + "…"
    return texto


def url_canonico(url: str) -> str:
    if not url:
        return ""
    try:
        p = urlsplit(url.strip())
        q = [(k, v) for k, v in parse_qsl(p.query) if not _PARAMS_LIXO.match(k)]
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), urlencode(q), ""))
    except ValueError:
        return url.strip()


def normalizar_titulo(t: str) -> str:
    t = re.sub(r"\s[-–|]\s[^-–|]{2,40}$", "", t or "")  # remove " - Fonte" (Google News)
    return re.sub(r"[^\w]+", " ", t.lower()).strip()


def _data_entrada(e) -> datetime | None:
    for campo in ("published_parsed", "updated_parsed", "created_parsed"):
        st = e.get(campo)
        if st:
            try:
                return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)
            except (OverflowError, ValueError, TypeError):
                continue
    return None


# ---------------------------------------------------------------------- feeds
def _obter(url: str) -> bytes:
    if not url.startswith(("http://", "https://")):
        return Path(url).read_bytes()  # permite testes locais
    r = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.content


def ler_feed(cfg: dict) -> list[Item]:
    nome = cfg.get("nome") or cfg["url"]
    try:
        bruto = _obter(cfg["url"])
    except Exception as exc:  # noqa: BLE001 — uma fonte nunca deve parar o boletim
        log.warning("Fonte ignorada (%s): %s", nome, exc)
        return []
    d = feedparser.parse(bruto)
    if d.bozo and not d.entries:
        log.warning("Feed inválido (%s): %s", nome, d.get("bozo_exception"))
        return []
    itens = []
    for e in d.entries:
        titulo = limpar_texto(e.get("title", ""), 220)
        url = e.get("link") or ""
        if not titulo or not url:
            continue
        desc = e.get("summary") or ""
        if not desc and e.get("content"):
            desc = e["content"][0].get("value", "")
        itens.append(Item(
            titulo=titulo,
            url=url,
            fonte=nome,
            categoria=cfg.get("categoria", "geral"),
            descricao=limpar_texto(desc, MAX_DESC),
            data=_data_entrada(e),
        ))
    log.info("%-28s %3d itens", nome, len(itens))
    return itens


def _contem(texto: str, palavras: list[str]) -> bool:
    t = texto.lower()
    return any(re.search(r"(?<!\w)" + re.escape(p.lower()) + r"(?!\w)", t) for p in palavras)


def recolher(config: dict, estado, agora: datetime, max_itens: int = 60,
             stats: dict | None = None) -> tuple[list[Item], set[str]]:
    """Lê todos os feeds em paralelo.

    Devolve (itens a resumir, chaves sem data observadas) — estas últimas são
    marcadas como vistas para que itens perenes sem data nunca sejam enviados.
    """
    feeds = config.get("feeds", [])
    kw = config.get("palavras_chave", [])
    pub = config.get("palavras_publicidade", [])

    with ThreadPoolExecutor(max_workers=8) as ex:
        por_feed = list(ex.map(ler_feed, feeds))
    if stats is not None:
        stats["fontes_ok"] = sum(1 for x in por_feed if x)
        stats["fontes_total"] = len(feeds)
        stats["brutos"] = sum(len(x) for x in por_feed)

    # Janela: últimas 24 h; se o último envio foi há mais tempo (atraso do GitHub,
    # falha num dia), alarga até ao último envio, com teto de 48 h. A deduplicação
    # garante que nada se repete.
    inicio = agora - timedelta(hours=24)
    if estado.ultimo_envio and estado.ultimo_envio < inicio:
        inicio = max(estado.ultimo_envio, agora - timedelta(hours=48))

    resultado, chaves, titulos = [], set(), set()
    sem_data_ignorados = 0
    sem_data_observados: set[str] = set()
    for cfg, itens in zip(feeds, por_feed):
        for it in itens:
            texto = f"{it.titulo} {it.descricao}"
            if cfg.get("filtrar") and not _contem(texto, kw):
                continue
            if _contem(texto, pub):
                continue
            if it.data and it.data > agora + TOLERANCIA_FUTURO:
                it.data = None  # data absurda -> tratar como sem data
            if it.data:
                if it.data < inicio:
                    continue
            else:
                sem_data_observados.add(it.chave)
                # Sem data: só entra se nunca foi visto e se já houver histórico
                # (na 1.ª execução não sabemos o que é "novo").
                if estado.primeira_execucao or estado.ja_visto(it.chave):
                    sem_data_ignorados += 1
                    continue
            nt = normalizar_titulo(it.titulo)
            if it.chave in chaves or nt in titulos or estado.ja_visto(it.chave):
                continue
            chaves.add(it.chave)
            titulos.add(nt)
            resultado.append(it)

    if sem_data_ignorados:
        log.info("Itens sem data ignorados por segurança: %d", sem_data_ignorados)

    # Oficiais e lançamentos primeiro, depois mais recentes.
    prioridade = {"oficial": 0, "lancamentos": 1, "mcp": 2, "tutoriais": 3, "comunidade": 4, "imprensa": 5}
    resultado.sort(key=lambda i: (prioridade.get(i.categoria, 9), -(i.data.timestamp() if i.data else 0)))
    resultado = resultado[:max_itens]
    for n, it in enumerate(resultado, 1):
        it.id = f"n{n}"
    return resultado, sem_data_observados
