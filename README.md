# DropHunter Agent

Agente do [DropHunter](https://www.drophunter.com.br) que roda **no seu PC**. Ele guarda a sua
chave do CSGOEmpire localmente, repassa os eventos dos leiloes para o servidor e executa no
Empire, com a sua chave e do seu IP, exatamente o que o servidor decidir. Ele nao decide nada.

**Sua chave nunca sai da sua maquina.** O protocolo com o servidor nao tem campo para ela e
o codigo esta aqui para quem quiser conferir.

## Instalar

Baixe o binario da sua plataforma em *Releases* (`drophunter-agent` Linux ou
`drophunter-agent.exe` Windows). Ou, com Python 3.11+:

    pip install .

## Usar

    drophunter-agent init      # cria ~/.drophunter/agent.toml (permissao 600)
    drophunter-agent status    # mostra a config sem segredos e testa o servidor
    drophunter-agent run       # roda em primeiro plano; Ctrl+C encerra

Config (`~/.drophunter/agent.toml`):

    server_url = "https://www.drophunter.com.br"
    licenca = "lic_..."            # da sua conta no painel
    empire_api_key = "..."         # csgoempire.com > Settings > API
    steam_api_key = ""             # opcional
    log_level = "INFO"

`DROPHUNTER_HOME` muda a pasta da config. `--config caminho.toml` muda o arquivo.

## Como funciona

1. `hello` no servidor com versao, SO e um fingerprint tolerante da maquina.
2. Conecta no feed (Socket.IO) do Empire com a chave local e repassa os eventos crus
   em lotes de ate 2 s.
3. Faz long-poll de comandos; cada comando e executado no Empire e o desfecho
   (HTTP + corpo redigido) volta para o servidor. Comando vencido nao executa.
4. Heartbeat a cada 30 s com status e saldo (numero, sem segredo).
5. 401 do servidor: para de operar, mostra o motivo e tenta a cada 5 min.

## Build do binario

    pip install ".[dev]"
    pyinstaller build/drophunter-agent.spec --distpath dist --workpath build/_work

O workflow em `.github/workflows/build.yml` gera Linux e Windows e publica nos Releases.
