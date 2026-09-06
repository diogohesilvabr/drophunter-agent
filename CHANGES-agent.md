# Diario do agente (CODER B) — DropHunter 4.0

> Para o Diogo fundir no `CHANGES.md` da raiz. Tudo aqui foi decidido sozinho (nao havia
> com quem falar); cada decisao esta marcada pra ser revista se o servidor (CODER A)
> tiver feito diferente.

## [Claude/VM] - Fase 3 adiantada: agente `drophunter_agent` em `agent/` (06/09)

Nasceu `agent/`: pacote Python **`drophunter_agent`** com pyproject proprio, pronto pra
virar o repo publico `drophunter-agent`. **Nao importa nada** de `src/csgoempire_arb`
nem de `server/` (ha teste que varre o codigo e reprova). Deps: `httpx` e
`python-socketio[asyncio_client]` (que traz `aiohttp` pro WebSocket — mesma pilha que o
bot atual usa contra o Empire, validada ao vivo em 28/06). TOML e lido com `tomllib`
(stdlib, por isso `requires-python >= 3.11`; sem `tomli`).

Venv proprio em `agent/.venv` (gitignorado; python 3.13 do sistema + pip). O `.venv`
do repo nao foi tocado. Nada do bot atual, `~/csgoempire-arb`, `~/drophunter-angel`,
servico ou tunel foi mexido. Sem commit.

### Estrutura

    agent/
      pyproject.toml            drophunter-agent 4.0.0a1, script `drophunter-agent`
      README.md                 texto do repo publico (instalar, usar, como funciona)
      .gitignore                .venv, dist, build/_work, agent.toml, *.bak-*
      drophunter_agent/
        __init__.py             __version__
        __main__.py             python -m drophunter_agent
        cli.py                  init | run | status | --version (pt-BR)
        config.py               ~/.drophunter/agent.toml (0600, dir 0700), backup ao sobrescrever
        redact.py               Redator: apaga segredo por VALOR e por forma (Bearer, key=)
        logs.py                 log legivel; o formatador redige TODA linha, tracebacks inclusive
        fingerprint.py          sha256(machine-id|hostname|SO|arch|usuario)[:24] — tolerante
        server.py               ServidorClient: as 5 rotas do PROTOCOLO.md
        empire.py               EmpireRest (bid/list/reprice/cancel/dispute/received/metadata)
                                + EmpireFeed (Socket.IO /s/ ns /trade, identify, filters)
        commands.py             Executor: expira, dedup, mapeia comando -> REST do Empire
        agent.py                Agente: loop asyncio (hello, heartbeat, lote, long-poll)
      build/
        drophunter-agent.spec   PyInstaller, binario unico, hiddenimports de engineio/socketio
        entry.py                ponto de entrada do binario
        build.sh                PY=.venv/bin/python build/build.sh -> dist/drophunter-agent
      .github/workflows/build.yml   ubuntu-latest + windows-latest, tag v* -> Release (so GITHUB_TOKEN)
      dist/drophunter-agent     binario Linux x86_64 gerado aqui (15 MB, gitignorado)
    tests/test_agent_v4.py      26 testes com servidor FAKE (httpx.MockTransport) e Empire FAKE

**`.gitignore` da raiz ganhou 2 linhas** (`!agent/build/` e `agent/build/_work/`): o
`build/` global escondia a spec do PyInstaller do git. Unica mudanca fora de `agent/`
e `tests/test_agent_v4.py`.

### Zero custodia — como esta garantido no codigo

1. Nao existe campo pra chave em nenhum payload (`hello` = version/os/fingerprint/ts;
   `heartbeat` = version/status/balance/ts; `events`; `results`).
2. A chave so entra no cabecalho `Authorization` do `httpx.AsyncClient` do **Empire**.
3. `ServidorClient._post` redige recursivamente todo JSON de saida por valor e, como
   ultima barreira, **recusa enviar** se ainda houver segredo no corpo (`RuntimeError`).
4. O formatador de log redige toda linha (o agente registra os segredos no redator
   global ao subir). `Config.__repr__` nunca mostra a chave; `status` mostra so
   "definida".
5. Teste `test_chave_do_empire_nunca_vai_ao_servidor_nem_ao_log`: o Empire fake devolve
   a chave no corpo de um 400, o feed traz a chave num evento e um log com a chave e
   emitido de proposito — o teste **falha** se a chave aparecer em qualquer requisicao
   ao servidor (URL, cabecalho ou corpo) ou em qualquer linha de log.

### Decisoes tomadas sozinho (rever contra o servidor do CODER A)

- **Lote de eventos**: `POST /api/agent/events` com corpo `{"events": [ {id, ts, type,
  data}, ... ]}` (o PROTOCOLO mostra um evento; "lote" pedia lista — envelopei em
  `events`). Ate 300 por lote, flush em <= 2 s.
- **Resultado**: um `POST /api/agent/results` **por comando**, corpo exatamente o
  objeto do PROTOCOLO (`command_id, status, http, body, ts`). `http` e `null` quando
  nao chegou a falar com o Empire (expirado, payload invalido, rede).
- **Comandos**: aceito `{"commands": [...]}` **ou** lista crua na resposta do long-poll.
- **Mapa feed -> evento**: `new_item` -> `auction_new` (um evento por item, payload
  cru), `updated_item` -> `auction_update` (idem), `deleted_item` -> `auction_end`
  com `data = {"id": <id>}`, **qualquer outro** evento do Socket.IO (trade_status,
  notificacoes...) -> `trade_update` com `data = {"event": "<nome>", "payload": <cru>}`.
  `updated_seller_online_status` e descartado na origem (96% do volume, ninguem le —
  licao do bot atual, 22/07). `agent_log` = `{"text": "<redigido>"}`.
- **`set_config`** (payload livre, mesclado em `config_servidor`) entende duas chaves
  do agente: `watch_auction_ids` (lista de ids -> so esses `auction_update` sobem;
  `null` = todos) e `active` (`true` religa o feed depois de um `stop`).
- **`stop`** = **pausa**: fecha o feed, status `idle`, continua heartbeat e long-poll
  (pra poder ser religado pelo painel). Com `payload.exit = true` encerra o processo.
- **`update_available`**: imprime versao + URL em destaque; `hard=true` reporta `ok` e
  encerra (codigo de saida 3). `min_version` em hello/heartbeat so avisa.
- **Expiracao usa o relogio do servidor**: cada resposta com `server_time` atualiza o
  desvio; `expires_at` e comparado com `time.time() + desvio`. PC com relogio errado
  nao executa lance atrasado nem descarta lance bom.
- **Repetido**: `id` de comando ja visto (ate 5000 lembrados) e ignorado sem resultado.
- **Metadata do Empire**: o brief cita `/trading/user/metadata`; o bot atual usa
  `GET /api/v2/metadata/socket` (validado 28/06). Uso o validado e caio no outro em 404.
  Cache de 20 s (feed e heartbeat pedem quase juntos e o Empire da 429 nessa rota —
  visto na fumaca). Saldo = `user.balance / 100`, buscado sem retry pra nunca atrasar o
  heartbeat.
- **Rate limit do Empire**: token bucket 30/min (igual ao bot). 429/5xx: ate 3
  tentativas com `Retry-After` (teto 30 s) + jitter. 4xx **nao levanta**: volta como
  `status=erro` com o corpo, que e o que o servidor precisa ("higher amount" etc.).
- **Long-poll sem long-poll**: se o servidor responder vazio na hora, o agente espera
  1 s antes de perguntar de novo (evita busy-loop; achado no teste).
- **401**: para tudo (feed, heartbeat, lote, long-poll), loga o motivo do corpo
  (`motivo`/`reason`/`detail`/`error`/`message`) e tenta `hello` a cada 5 min. Outro
  4xx (ex.: 409 "outro agente ativo"): espera 60 s. Rede/5xx: backoff 1 s -> 60 s com
  jitter. Feed do Empire tem supervisor proprio (reconecta se cair ou ficar mudo > 75 s).
- **`status` nao chama `hello`** de proposito (derrubaria um agente ja rodando); testa
  o servidor com `GET /saude` (rota que o front da Fase 1 ja tem).
- **`DROPHUNTER_HOME`** muda a pasta da config (usado nos testes e na fumaca);
  `--config caminho` muda o arquivo. `init` cria o arquivo com `os.open(..., 0o600)`
  antes de escrever o primeiro byte; no Windows o chmod e no-op (testado).
- Nomes de excecao em portugues sem sufixo `Error` (ruff N818 ignorado no pyproject
  do agente).

### Empacotamento

- `build/drophunter-agent.spec`: binario unico, console, sem UPX, `strip` fora do
  Windows, `hiddenimports` = todos os submodulos de `engineio`/`socketio` + `aiohttp`
  (o driver websocket e carregado por nome em runtime).
- Gerado aqui: `agent/dist/drophunter-agent`, **15 MB**, `--version` ok, `status` sem
  config sai com 2 e mensagem amigavel, `strings` nao encontra `csgoempire_arb`.
- **Fumaca real do binario** (servidor fake em 127.0.0.1:8097 + chave falsa contra o
  Empire de verdade): hello aceito, comando vencido -> `expirado` sem tocar no Empire,
  `set_config` -> `ok`, Empire respondeu 429 no metadata (tratado com espera), Ctrl+C
  encerrou limpo. Nenhuma ocorrencia da chave no que o servidor fake recebeu nem no log.
- `.github/workflows/build.yml`: `ubuntu-latest` + `windows-latest`, Python 3.12,
  `pip install .[dev]`, PyInstaller, fumaca (`--version` e `status` sem config == 2),
  artefatos `drophunter-agent-linux-x86_64` / `drophunter-agent-windows-x86_64.exe`,
  `SHA256SUMS.txt`, Release via `softprops/action-gh-release` com o `GITHUB_TOKEN`
  automatico. **Nenhum segredo.** Dispara em tag `v*` ou manual.

### Testes (rodar SO este arquivo)

    agent/.venv/bin/python -m pytest tests/test_agent_v4.py -q      # 26 passed

Cobre: config (600/700, backup, ausente, incompleta, CLI `init` e `status`), redator,
`centavos`, hello/heartbeat/events/commands/results no formato do contrato, feed
(URL, path, namespace, User-Agent "<uid> API Bot", identify, filters), mapa de eventos
e payload cru, lote <= 2 s, bid -> `POST /trading/deposit/123/bid {"bid_value":1040}`,
expirado nao executa, erro do Empire volta com corpo, list/reprice/cancel/dispute/
mark_received, repetido ignorado, payload invalido, 401 para e retoma quando a
licenca volta, update_available (URL no log; hard encerra), stop/set_config, backoff
em 503, **chave nunca sai** (servidor + log), barreira do ServidorClient, nenhum import
do privado, relogio do servidor na expiracao, chmod no Windows.

`pytest tests/` inteira continua travando por causa do teste antigo — rodar por arquivo.
`pytest-timeout` foi instalado no venv do agente so pra diagnosticar (nao e dep).

### Pendencias (pro Diogo)

- Alinhar com o servidor do CODER A os 3 pontos de forma: envelope `{"events": [...]}`,
  um POST por resultado, e `{"commands": [...]}` no long-poll.
- LICENSE do repo publico (nao escolhi; o pyproject diz "Proprietary").
- Icone/nome do `.exe` e assinatura de codigo (SmartScreen vai reclamar de binario nao
  assinado no Windows).
- Docker (PROJETO.md cita): nao feito; o binario Linux cobre a VM por enquanto.
