# DropHunter Agent

O agente roda no seu PC e faz a ponte entre o DropHunter e o Empire/Steam.
As chaves ficam no `agent.toml` local e são enviadas somente à API correspondente.
O servidor DropHunter recebe respostas e eventos; nunca recebe suas chaves.

**Sua chave nunca sai para o servidor DropHunter, e o servidor não consegue sacar.**
A lista branca permite somente consultas, negociação de itens e recusa de ofertas Steam.
`/user/tip`, saques, transferências, pagamentos, hosts externos, redirects e rotas não
listadas são recusados pelo proxy, mesmo se o pedido vier do servidor. O pagamento da
mensalidade tem o fluxo local de consentimento descrito abaixo. O agente não calcula preços,
não escolhe leilões e não decide o que comprar ou vender.

## Instalação no Windows

Baixe o **`DropHunter-Setup-<versão>.exe`** no Release (ou o botão "Baixar instalador
(Windows)" na aba Fatura do painel) e dê dois cliques. O instalador é um assistente em
português e **não pede senha de administrador**: instala só para o seu usuário, em
`%LOCALAPPDATA%\Programs\DropHunter`.

As telas, em ordem:

1. **Bem-vindo** — o que é o agente e a promessa: a sua chave fica neste computador.
2. **Licença** — cole a licença (`lic_…`) da aba Fatura do painel. A tela tem um link que
   abre o painel.
3. **Chave da API do CSGOEmpire** — link para `csgoempire.com/trading/apikey`.
4. **Steam** — a chave da Steam Web API (link para `steamcommunity.com/dev/apikey`; no
   campo "Nome de domínio" pode escrever `drophunter`) e o seu Steam ID 64 (link para
   `steamid.io`).
5. **Pasta** de instalação (só na primeira vez) e **Opções**: atalho na área de trabalho
   (desmarcado) e "Iniciar junto com o Windows" (marcado).
6. **Pronto para instalar** → **Instalando** → **Concluir**, com a opção de abrir o app.

Nenhum campo é opcional e cada um é conferido antes de avançar (licença `lic_` + 40
caracteres, chave da Steam com 32, Steam ID com 17 dígitos começando em `7656`). Ao final o
instalador grava `%USERPROFILE%\.drophunter\agent.toml` — é por isso que o app já abre
**Conectado**, sem repetir as perguntas. Para trocar uma chave depois: **Configurações**, no
próprio app.

Reinstalando por cima (atualização), a primeira tela pergunta se você quer **manter a
configuração atual** (recomendado) ou informar chaves novas; mantendo, ele pula as três
telas de chave. Informando chaves novas, só os campos perguntados são reescritos — o resto
do `agent.toml` (senha da API local, teto de pagamento) fica de pé.

O instalador ainda:

- põe na mesma pasta o `drophunter-agent.exe` de linha de comando (mesmo agente, sem janela);
- cria o atalho no menu Iniciar;
- escreve `DropHunter` em `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` quando a
  opção "Iniciar junto com o Windows" fica marcada — a **mesma** chave que o menu da bandeja
  liga e desliga (nomes diferentes fariam o app subir duas vezes);
- roda com `SetupLogging=no`: licença e chaves nunca vão parar num log do Setup.

Desinstalar: "Aplicativos instalados" do Windows, ou o atalho no menu Iniciar. **A sua
configuração não é apagada**: `%USERPROFILE%\.drophunter` (licença e chaves) continua onde
está — a chave é sua. Apague a pasta à mão se quiser remover tudo.

Quem roda em servidor ou por script pode usar direto o
`drophunter-agent-windows-x86_64.exe` do Release, sem instalador.

## Aviso do SmartScreen

O instalador **não é assinado com certificado de código** (não temos um). Na primeira
execução o Windows mostra a tela azul *"O Windows protegeu o seu computador"*: clique em
**Mais informações** e depois em **Executar assim mesmo**. Não é vírus — é o Windows
dizendo que não conhece o editor ainda.

Confira o download pelo `SHA256SUMS.txt` publicado no mesmo Release:

```powershell
Get-FileHash .\DropHunter-Setup-<versão>.exe -Algorithm SHA256
```

O que resolve de vez é um certificado de assinatura de código **OV** (some o aviso depois
de reputação acumulada) ou **EV** (some na hora, exige token físico). Custo aproximado:
OV US$ 200–400/ano, EV US$ 300–600/ano (Sectigo, DigiCert, SSL.com). Decisão do dono do
produto — enquanto não houver, o aviso é esperado e está documentado aqui e na aba
"Fatura" do painel.

## Instalar e usar

Com Python 3.11+, na pasta do pacote:

```bash
pip install .
drophunter-agent init
drophunter-agent status
drophunter-agent run
```

Os binários Linux/Windows são distribuídos em Releases. A versão antiga `4.0.0a1` não
fala o protocolo atual; publicado como `4.0.0a4`.

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
api_local_senha = "..."                # nasce sozinha na primeira subida
```

`DROPHUNTER_HOME` muda a pasta; `--config caminho.toml` seleciona outro arquivo.
A porta antiga `api_local_porta` ainda é lida; novas gravações usam `local_api_port`.
URLs antigas `https://host` são convertidas para `wss://host/api/agent/ws`.
Conexões sem TLS são aceitas apenas no loopback para testes. Não coloque licença,
senha ou query na URL. Configurações sobrescritas têm backup também com permissão 600.

## Extensão Chrome

Abra o app, vá em **Configurações** e procure o bloco **Extensão do Chrome**: ele mostra o
endereço (`http://127.0.0.1:8765`), o usuário (`drophunter`) e a senha mascarada, com os
botões **Mostrar**, **Copiar** e **Gerar nova senha**. É de lá que você copia os três valores
para as opções da extensão — você não precisa abrir o `agent.toml` para nada.

A senha da API local **nasce sozinha** na primeira subida do agente (o instalador grava
`api_local_senha = ""` de propósito: instalador não inventa segredo). Gerar uma senha nova
invalida a antiga na hora: a extensão fica com 401 até você colar a nova nela.
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

Linha de comando (Linux e Windows):

```bash
pip install ".[dev]"
python -m PyInstaller build/drophunter-agent.spec --distpath dist --workpath build/_work
```

App com janela + instalador (só Windows):

```bash
pip install ".[dev]" pywebview pystray Pillow
python build/versao.py                 # a versão sai de drophunter_agent/__init__.py
python build/icone/gerar_ico.py        # build/icone/drophunter.ico (derivado, fora do git)
python build/icone/gerar_bmp.py        # imagens do assistente (wizard-*.bmp, idem)
python -m PyInstaller build/drophunter-app.spec --distpath dist --workpath build/_work
python -m PyInstaller build/drophunter-agent.spec --distpath dist --workpath build/_work
iscc /DVersao=<versão> installer\drophunter.iss    # sai em dist/DropHunter-Setup-<versão>.exe
```

O `drophunter-app.spec` é **onedir** e `console=False`: a pasta `dist/DropHunter Agent/` abre
rápido e não dispara o falso-positivo de antivírus que o onefile dispara (ele se desempacota
em `%TEMP%` a cada execução). Ele para com "aguardando a frente W1" enquanto
`drophunter_agent/app.py` não existir.

Os specs incluem os clientes asyncio de socketio/engineio e `aiohttp.client_ws`.
Não há dependência de `websockets`: o canal usa `aiohttp`.
O workflow `.github/workflows/build.yml` faz tudo isso em tag `v*` e publica
`DropHunter-Setup-<versão>.exe`, `DropHunter-Setup.exe` (cópia sem versão, para o link
`releases/latest/download/`), `drophunter-agent-windows-x86_64.exe`,
`drophunter-agent-linux-x86_64` e `SHA256SUMS.txt`. Rodar o workflow **manualmente**
(`workflow_dispatch`, sem tag) compila tudo e sobe como artefato, sem criar Release —
é assim que se testa o instalador sem queimar uma tag. Publicar/taguear é uma etapa
separada, autorizada pelo responsável pelo projeto.


## Como o pagamento funciona e por que só você consegue autorizar

O site solicita o pagamento da fatura, mas isso apenas cria um pedido na memória do
agente. **Abra a página pelo app**: no PC em que o agente está rodando, o cartão
"Pagamentos pendentes" fica laranja e o botão **Ver e confirmar (1)** abre o navegador já
autenticado (o mesmo item existe no menu da bandeja). Confira competência, valor, destino e
vencimento e clique em **Confirmar pagamento**; dá para cancelar também.

O link que o app abre carrega um token de acesso de uso único, válido por dez minutos, que o
navegador troca por um cookie de sessão — por isso o navegador **não** pede usuário e senha.
Quem abrir `http://127.0.0.1:8765/pagar` na mão vê a página "abra pelo DropHunter" (401):
o consentimento é sempre um clique seu, nesta máquina. O telefone não autoriza esse
pagamento. A extensão do Chrome continua entrando por Basic auth, nas rotas `/api/*`.

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
responde **503: parado**. O pedido precisa vencer em até 24 horas e o valor ter no máximo
duas casas decimais.

**Um pedido pendente sobrevive a reinício do agente** (4.0.0b5): ele fica em
`~/.drophunter/pagamentos_pendentes.json` (600, só id, fatura, competência, valor, destino e
vencimento — nenhum segredo) e volta na subida, sem os vencidos. Na conexão o agente manda a
lista de ids no `hello` (`pagamentos_pendentes`) e o servidor reenvia só o que faltar; pedido
com id repetido é ignorado, então o consentimento continua sendo pedido uma vez só. Se esse
arquivo não puder ser lido ou gravado, o agente sobe do mesmo jeito — o pior caso é o
servidor reenviar o pedido.

Somente esse clique chama o Tip do Empire, com `steam_id` e o valor convertido em centavos
de coin: 6,17 coins → `amount: "617"`. A chave é usada localmente. O proxy continua
recusando `/user/tip`. Dois cliques não geram dois Tips; os pedidos são processados um por
vez, e o diário `~/.drophunter/pagamentos.jsonl` (600) impede repetir os IDs após reinício.
`DROPHUNTER_HOME`, quando definido, muda a pasta da configuração e do diário.

**Não há campo de 2FA** (desde a 4.0.0b5). A API do Empire não exige código: provado em
07/09/2026 com dois pagamentos reais, o segundo com o campo deliberadamente vazio (o *site*
do Empire pede MFA num Tip manual; a API com chave, não). O corpo do Tip leva só destino e
valor. Se um formulário antigo ainda postar `codigo_2fa`, o campo é ignorado.

Se houver timeout, resposta inconclusiva ou queda no meio do pagamento, **confira o
extrato antes de tentar novamente**. A fatura fica reservada no diário para evitar uma
segunda cobrança. Não apague o diário para repetir: confirme pelo extrato e peça a
reconciliação no site. Um resultado obtido durante queda do canal fica na memória e é
reenviado na reconexão; se o processo encerrar antes disso, a reconciliação é necessária.
O diário guarda somente IDs, estado e data, sem chave nem saldo.
