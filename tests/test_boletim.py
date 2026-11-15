"""Testes offline: python -m pytest -q"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from boletim.estado import Estado
from boletim.recolha import recolher
from boletim.resumo import _validar, resumo_extrativo
from boletim.telegram import LIMITE, construir_blocos, dividir

RAIZ = Path(__file__).resolve().parent.parent
AGORA = datetime.now(timezone.utc)


def _cfg():
    return yaml.safe_load((RAIZ / "tests/fixtures/feeds.yaml").read_text())


def _recolher(tmp_path, ultimo_envio=None):
    e = Estado(tmp_path / "s.json")
    e.ultimo_envio = ultimo_envio
    itens, sem_data = recolher(_cfg(), e, AGORA, max_itens=100)
    return e, itens, sem_data


def test_filtra_24h_publicidade_relevancia_e_sem_data(tmp_path, monkeypatch):
    monkeypatch.chdir(RAIZ)
    _, itens, sem_data = _recolher(tmp_path)
    titulos = " | ".join(i.titulo for i in itens)
    assert "Anthropic lança Claude" in titulos
    assert "antiga" not in titulos          # > 24 h
    assert "Sponsored" not in titulos       # publicidade
    assert "Gatos" not in titulos           # irrelevante (filtrar: true)
    assert "sem data" not in titulos        # 1.ª execução: sem data é ignorado
    assert "futura" not in titulos          # data futura -> sem data -> ignorado
    assert len(sem_data) == 2


def test_sem_data_entra_uma_vez_depois_do_historico(tmp_path, monkeypatch):
    monkeypatch.chdir(RAIZ)
    e, _, sem_data = _recolher(tmp_path, ultimo_envio=AGORA - timedelta(hours=24))
    # 2.ª execução sem ter marcado: aparecem
    _, itens, _ = _recolher(tmp_path, ultimo_envio=AGORA - timedelta(hours=24))
    assert any("sem data" in i.titulo for i in itens)
    # depois de marcados, não voltam
    e.marcar([i.chave for i in itens] + list(sem_data), AGORA)
    itens2, _ = recolher(_cfg(), e, AGORA, max_itens=100)
    assert itens2 == []


def test_validar_rejeita_ids_inventados(monkeypatch, tmp_path):
    monkeypatch.chdir(RAIZ)
    _, itens, _ = _recolher(tmp_path)
    bruto = {"destaque": "x", "topicos": [
        {"topico": "Inexistente", "itens": [{"id": itens[0].id, "titulo": "A", "resumo": "B"},
                                            {"id": "n999", "titulo": "falso"},
                                            {"id": itens[0].id, "titulo": "dup"}]}]}
    r = _validar(bruto, itens)
    assert len(r["topicos"]) == 1 and len(r["topicos"][0]["itens"]) == 1
    assert r["topicos"][0]["itens"][0]["url"] == itens[0].url


def test_html_seguro_e_divisao(monkeypatch, tmp_path):
    monkeypatch.chdir(RAIZ)
    _, itens, _ = _recolher(tmp_path)
    r = resumo_extrativo(itens, 100)
    msgs = dividir(construir_blocos(r, AGORA, 1, len(itens)))
    assert 1 <= len(msgs) <= 2
    assert all(len(m) <= LIMITE for m in msgs)
    tudo = "".join(msgs)
    assert "<script>" not in tudo and "<p>" not in tudo
    assert "omitidos" in msgs[-1]


def test_curto_fica_numa_so_mensagem():
    r = {"destaque": "a & b", "com_ia": True, "topicos": [
        {"emoji": "🚀", "topico": "Lançamentos e atualizações",
         "itens": [{"titulo": "<x>", "resumo": "r", "url": 'https://a.b/?q="1"', "fonte": "F"}]}]}
    msgs = dividir(construir_blocos(r, AGORA, 1, 1))
    assert len(msgs) == 1
    assert "&lt;x&gt;" in msgs[0] and "a &amp; b" in msgs[0] and "&quot;1&quot;" in msgs[0]


def test_sem_novidades():
    msgs = dividir(construir_blocos({"topicos": []}, AGORA, 0, 0))
    assert len(msgs) == 1 and "Sem novidades" in msgs[0]
