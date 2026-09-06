# DropHunter Agent 4.0.0a3

O agente roda no seu PC e faz a ponte entre o DropHunter e o Empire/Steam.
As chaves ficam no `agent.toml` local e são enviadas somente à API correspondente.
O servidor DropHunter recebe respostas e eventos; nunca recebe suas chaves.

**Sua chave nunca sai para o servidor DropHunter, e o servidor não consegue sacar.**
A lista branca permite somente consultas, negociação de itens e recusa de ofertas Steam.
`/user/tip`, saques, transferências, pagamentos, hosts externos, redirects e rotas não
listadas são recusados pelo proxy, mesmo se o pedido vier do servidor. O pagamento da
mensalidade tem o fluxo local de consentimento descrito abaixo. O agente não calcula preços,
não escolhe leilões e não decide o que comprar ou vender.

## Instalar e usar

Com Python 3.11+, na pasta do pacote:

```bash
pip install .
drophunter-agent init
drophunter-agent status
drophunter-agent run
```

Os binários Linux/Windows são distribuídos em Releases. A versão antiga `4.0.0a1` não
fala o protocolo atual; a publicação de `4.0.0a3` depende da liberação do projeto.

`init` pede licença e chaves sem eco, cria `~/.drophunter/agent.toml` com permissão 600
(pasta 700) e gera o Basic auth da extensão. Se o terminal não permite leitura sem eco,
a configuração é interrompida. `status` mostra a configuração sem chaves e consulta
somente `/saude`, sem abrir outro canal que derrubaria a instância em execução.
`run` fica em primeiro plano; Ctrl+C encerra.

```toml
server_url = "wss://www.drophunter.com.br/api/agent/ws"
licenca = "lic_..."
empire_api_key = "..."
steam_api_key = ""                    # opcional
steam_id64 = ""                       # opcional, 17 dígitos
log_level = "INFO"
local_api_port = 8765                  # 0 desliga
api_local_usuario = "drophunter"
api_local_senha = "..."                # gerada no init
```

`DROPHUNTER_HOME` muda a pasta; `--config caminho.toml` seleciona outro arquivo.
A porta antiga `api_local_porta` ainda é lida; novas gravações usam `local_api_port`.
URLs antigas `https://host` são convertidas para `wss://host/api/agent/ws`.
Conexões sem TLS são aceitas apenas no loopback para testes. Não coloque licença,
senha ou query na URL. Configurações sobrescritas têm backup também com permissão 600.

## Extensão Chrome

Nas opções da extensão, use `http://127.0.0.1:8765` e o usuário/senha exibidos pelo `init`.
A senha também fica no `agent.toml`; `status` não a exibe.
`--porta-local` e `--sem-senha-local` continuam disponíveis no `init`.

A API local mantém `/api/bot/status` e encaminha `/api/extension/ping`, `/api/sends/*`
e `/api/deliveries/*` como `ext_request`, sem cache ou decisões locais. O servidor
responde como `ext_response`. Sem canal ativo retorna 503; após 25 s sem resposta,
504. A extensão deve reter as ofertas enquanto o servidor estiver indisponível.

## Transporte e limites

- Um WebSocket para o DropHunter, com licença no cabeçalho `Authorization`.
- `hello` informa versão, SO, arquitetura, fingerprint, ID Empire, Steam ID opcional
  e porta local. Só opera após `hello_ok`.
- Socket.io Empire usa `/s/`, namespace `/trade`, autenticação `identify` local e `filters`.
  Todos os eventos de aplicação são repassados crus, incluindo `init`; credenciais são redigidas.
- HTTP: até 8 chamadas simultâneas; timeout padrão 20 s, máximo 30 s, incluindo a fila;
  corpo até 1 MiB e apenas `retry-after`/`content-type` nos cabeçalhos de resposta.
- Steam GET recebe `key` local na query; o POST Steam recebe formulário com `key` local.
- Sem canal, chamadas HTTP param. O socket já aberto permanece ouvindo; reconexão ou
  renovação de metadata espera o canal voltar. Eventos ficam em memória por até 60 s,
  com teto adicional de 20 mil eventos/16 MiB; os mais antigos são descartados primeiro.
- Código 4401 mostra motivo redigido e tenta novamente em 5 min; 4409 avisa que outra
  máquina assumiu e encerra. Outras quedas usam backoff de 1–60 s com jitter.
- `stop` bloqueia HTTP e extensão, fecha o feed e mantém heartbeat. Reconectar libera.
  Chamadas HTTP já iniciadas podem terminar; não é possível desfazer pedidos já enviados.
- `ws_emit` aceita apenas `filters`; eventos não documentados são recusados. `identify`
  é montado localmente e nunca vem do servidor. `ws_reconnect` reabre o feed quando ativo.
- `update_available` avisa; atualização obrigatória encerra. Não há download automático.

O código público não importa o bot nem qualquer módulo do servidor privado.

## Demonstração sem conta e sem API real

Com o pacote instalado, execute:

```bash
python examples/servidor_falso.py
```

O script não lê sua configuração. Ele sobe um WebSocket de loopback, agente com HTTP e
socket.io falsos e API local em porta aleatória. Exibe a URL local; use Basic auth
`demo` / `demo` para testar `/api/sends/pending`. O console mostra `hello`, heartbeat,
eventos, HTTP permitido e `/user/tip` recusado. `--duracao 3` encerra automaticamente.

No checkout privado, rode cada arquivo separadamente:

```bash
.venv/bin/python -m pytest -q tests/test_agent_listabranca_v4.py -p no:cacheprovider
.venv/bin/python -m pytest -q tests/test_agent_proxy_v4.py -p no:cacheprovider
.venv/bin/python -m pytest -q tests/test_agent_v4.py -p no:cacheprovider
.venv/bin/python -m pytest -q tests/test_agent_local_api_v4.py -p no:cacheprovider
```

## Build

```bash
pip install ".[dev]"
python -m PyInstaller build/drophunter-agent.spec --distpath dist --workpath build/_work
```

O spec inclui os clientes asyncio de socketio/engineio e `aiohttp.client_ws`.
Não há dependência de `websockets`: o canal usa `aiohttp`.
O workflow `.github/workflows/build.yml` prepara Linux/Windows; publicar/taguear é uma
etapa separada, autorizada pelo responsável pelo projeto.


## Como o pagamento funciona e por que só você consegue autorizar

O site solicita o pagamento da fatura, mas isso apenas cria um pedido na memória do
agente. Abra `http://127.0.0.1:8765/pagar` **no PC em que o agente está rodando** (ou use
sua `local_api_port`). Entre com o usuário e a senha locais, confira competência, valor,
destino e vencimento, e clique em **Confirmar pagamento**. Você também pode cancelar.
O telefone não autoriza esse pagamento. A API local precisa estar ligada e ter senha;
se `api_local_senha` estiver vazia, configure-a no `agent.toml` e reinicie o agente.

O primeiro `hello_ok` fixa o Steam64 da plataforma na configuração local:

```toml
plataforma_steam_id = "" # preenchido automaticamente na primeira conexão válida
pagamento_teto_coins = 100
```

O destino tem 17 dígitos e começa com `7656`. Se o servidor passar outro destino após a
fixação, todos os pagamentos ficam bloqueados até você conferir e corrigir a configuração
local e reiniciar o processo. O agente nunca troca esse destino sozinho. O teto também é
local: para alterá-lo, edite `pagamento_teto_coins` e reinicie. O comando `status` mostra
ambos. A gravação preserva os demais campos e cria backup privado com permissão 600.

Cada formulário tem um token de uso único, válido por dez minutos; atualizar a página
invalida o formulário anterior. A confirmação verifica novamente destino, teto e prazo.
Sem canal conectado ela responde **503: reconecte o agente**; se o servidor pediu parada,
responde **503: parado**. Pedidos sobrevivem à reconexão enquanto o processo estiver vivo.
O pedido precisa vencer em até 24 horas e o valor ter no máximo duas casas decimais.

Somente esse clique chama o Tip do Empire, com `steam_id` e o valor convertido em centavos
de coin: 6,17 coins → `amount: "617"`. A chave é usada localmente. O proxy continua
recusando `/user/tip`. Dois cliques não geram dois Tips; os pedidos são processados um por
vez, e o diário `~/.drophunter/pagamentos.jsonl` (600) impede repetir os IDs após reinício.
`DROPHUNTER_HOME`, quando definido, muda a pasta da configuração e do diário.

O campo **Código 2FA** é opcional: só preencha se o Empire recusar pedindo 2FA. Após uma
recusa explícita, solicite outro pedido no site e confirme na página local com o código.
Ele vai apenas ao Empire como `code`; não é salvo nem enviado ao servidor DropHunter.
O nome desse campo ainda precisa ser comprovado no primeiro pagamento real.

Se houver timeout, resposta inconclusiva ou queda no meio do pagamento, **confira o
extrato antes de tentar novamente**. A fatura fica reservada no diário para evitar uma
segunda cobrança. Não apague o diário para repetir: confirme pelo extrato e peça a
reconciliação no site. Um resultado obtido durante queda do canal fica na memória e é
reenviado na reconexão; se o processo encerrar antes disso, a reconciliação é necessária.
O diário guarda somente IDs, estado e data, sem chave, código 2FA ou saldo.
