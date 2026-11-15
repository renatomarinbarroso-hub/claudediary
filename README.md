# 🧠 Boletim diário Claude → Telegram

Agregador 100% gratuito e automático: recolhe notícias, lançamentos, MCPs, agentes,
skills, ferramentas e dicas sobre o Claude a partir de feeds RSS, resume em português
de Portugal com IA gratuita (Google Gemini) e envia para o teu Telegram todos os dias
às **18:00 de Lisboa**.

|Peça|Serviço|Custo|
|-|-|-|
|Execução diária|GitHub Actions|Grátis (repo público: ilimitado; privado: 2000 min/mês — usa \~2 min/dia)|
|Resumo com IA|Google Gemini API (plano gratuito, sem cartão)|Grátis|
|IA alternativa (opcional)|Groq API (plano gratuito)|Grátis|
|Envio|Bot do Telegram|Grátis|
|Fontes|RSS/Atom públicos|Grátis|

Sem chave de IA o boletim continua a funcionar, com resumo extrativo (títulos + excertos originais).

\---

## 1\. Fluxo

```
cron 17:00 e 18:00 UTC ──► main.py --agendado
                            │ é ≥18h em Lisboa e ainda não enviou hoje?  não ► sai
                            ▼
  feeds.yaml ──► recolha.py: lê \~20 feeds em paralelo (falhas ignoradas)
                            │ janela 24 h · sem data = só se nunca visto
                            │ filtra publicidade e irrelevantes · deduplica
                            ▼
                 resumo.py: Gemini → (Groq) → extrativo   \[IA devolve só JSON com ids]
                            ▼
                 telegram.py: HTML escapado · ≤4000 car. · 1 msg (2 se necessário)
                            ▼
                 state/seen.json gravado no repositório (commit automático)
```

## 2\. Configuração (≈10 minutos)

### 2.1 Bot do Telegram

1. No Telegram, abre **@BotFather** → `/newbot` → escolhe nome e username.
2. Guarda o **token** (`123456:ABC...`).                       **9228991521:AAEbUhri8pmCDwFjYXE00VOce9pByUNuZp8**
3. Abre a conversa com o teu bot e envia-lhe qualquer mensagem (ex.: `olá`).
4. No browser abre `https://api.telegram.org/bot<TOKEN>/getUpdates` e copia o
valor de `"chat":{"id": ...}` → é o **chat\_id** (num grupo/canal começa por `-100`).   **"id":795785218**

### 2.2 Chave Gemini (gratuita)

1. Vai a [https://aistudio.google.com/apikey](https://aistudio.google.com/apikey) com a tua conta Google.
2. **Create API key** → copia. Não é preciso cartão nem faturação.             **AQ.Ab8RN6IQE44jBKztAPpOh4Jn0BI5n\_vL-Z-4ohRNx0d7wpVNTA**

> Nota: no plano gratuito, a Google pode usar os pedidos para melhorar os seus produtos.
> Aqui só seguem títulos e excertos públicos dos feeds.

### 2.3 (Opcional) Chave Groq

[https://console.groq.com/keys](https://console.groq.com/keys) → cria chave. Só é usada se o Gemini falhar.            **gsk\_zSH0p0zlcDmR4nn9cOrRWGdyb3FYDNsOqVaetJtKEHqqFzHLZpca**

### 2.4 Repositório GitHub

1. Cria um repositório novo (público = minutos ilimitados) e envia estes ficheiros:

```bash
   git init \&\& git add . \&\& git commit -m "Boletim Claude"
   git branch -M main
   git remote add origin https://github.com/<utilizador>/boletim-claude.git
   git push -u origin main
   ```

2. **Settings → Secrets and variables → Actions → New repository secret**:

|Nome|Valor|Obrigatório|
|-|-|-|
|`TELEGRAM\_BOT\_TOKEN`|token do BotFather|sim|
|`TELEGRAM\_CHAT\_ID`|o teu chat\_id|sim|
|`GEMINI\_API\_KEY`|chave do AI Studio|recomendado|
|`GROQ\_API\_KEY`|chave Groq|não|

3. **Settings → Actions → General → Workflow permissions** → *Read and write permissions* → Save
(necessário para gravar `state/seen.json`).

### 2.5 Testar com um clique

**Actions → Boletim diário Claude → Run workflow**

* `dry\_run` ✅ → mostra as mensagens só no log.
* sem opções → envia para o Telegram **sem** marcar itens como enviados
(o boletim das 18:00 continua completo).

## 3\. Horário e fuso

O cron do GitHub só aceita UTC. Lisboa é UTC+0 no inverno e UTC+1 no verão, por isso
há dois agendamentos (17:00 e 18:00 UTC) e o script decide:

|Época|17:00 UTC|18:00 UTC|
|-|-|-|
|Inverno (WET)|17h Lisboa → ignora|18h → **envia**|
|Verão (WEST)|18h Lisboa → **envia**|19h → já enviado, ignora|

⚠️ O GitHub pode atrasar execuções agendadas alguns minutos (por vezes mais em horas de
ponta). O segundo agendamento serve de rede de segurança.

## 4\. Personalizar

* **Fontes**: edita `feeds.yaml` (nome, url, categoria, `filtrar`). Fontes em baixo são ignoradas.
* **Palavras-chave / publicidade**: listas no topo de `feeds.yaml`.
* **Variáveis opcionais** (no passo *Gerar e enviar boletim* do workflow):
`GEMINI\_MODELS` (default `gemini-flash-latest,gemini-2.5-flash,gemini-flash-lite-latest`),
`GROQ\_MODEL`, `MAX\_ITENS\_BOLETIM` (25), `MAX\_ITENS\_IA` (60), `HORA\_ENVIO` (18).

## 5\. Testar localmente

```bash
pip install -r requirements.txt pytest
python -m pytest -q                       # testes offline
export GEMINI\_API\_KEY=...                 # opcional
python main.py --dry-run                  # recolhe e mostra, não envia
export TELEGRAM\_BOT\_TOKEN=... TELEGRAM\_CHAT\_ID=...
python main.py                            # envia (sem alterar o estado)
```

## 6\. Robustez

* **Itens sem data**: nunca entram na 1.ª execução; depois, só se nunca tiverem sido vistos.
* **Datas no futuro** (fusos errados): tratadas como "sem data".
* **Dia falhado / atraso**: a janela estende-se até ao último envio (máx. 48 h); a deduplicação evita repetições.
* **IA**: devolve só ids → os links vêm sempre do feed original (sem URLs inventados).
* **HTML**: todo o texto é escapado; se o Telegram ainda assim recusar, reenvia em texto simples.
* **Inatividade**: em repos públicos o GitHub desativa o cron após 60 dias sem atividade; o commit diário do estado ajuda a evitá-lo (se acontecer, recebes email e basta reativar em Actions).
* Reddit às vezes bloqueia IPs do GitHub Actions (HTTP 403) — essas fontes são simplesmente ignoradas.

