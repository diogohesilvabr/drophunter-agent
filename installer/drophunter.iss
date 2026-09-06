; Instalador Windows do DropHunter Agent (Inno Setup 6) — assistente que ja pergunta as chaves.
;
; Fluxo: Bem-vindo -> [Configuracao encontrada] -> Licenca -> Empire -> Steam -> Pasta ->
;        Opcoes (atalho/autostart) -> Pronto -> Instalando -> Concluir (abre o app).
; Ao final o Setup grava %USERPROFILE%\.drophunter\agent.toml. Quando o app abre, a config
; ja existe: ele vai direto pra janela "Conectado" em vez do assistente da frente W1.
;
; Compilar (da raiz de agent/, no Windows, com o Inno Setup instalado):
;     python build/icone/gerar_ico.py
;     python build/icone/gerar_bmp.py
;     iscc /DVersao=$(python build/versao.py) installer\drophunter.iss
;
; A versão NÃO fica escrita aqui: vem de drophunter_agent/__init__.py, lida pelo workflow
; (build/versao.py) e passada em /DVersao. Duplicar o número é como o build mente.
;
; Pré-requisitos na pasta agent/ (o workflow faz nesta ordem):
;   dist\DropHunter Agent\    -> pyinstaller build/drophunter-app.spec   (app, sem console)
;   dist\drophunter-agent.exe -> pyinstaller build/drophunter-agent.spec (linha de comando)
;   build\icone\drophunter.ico + wizard-*.bmp -> python build/icone/gerar_ico.py e gerar_bmp.py
;
; Sem assinatura de código (não temos certificado): o SmartScreen avisa na primeira
; execução. Ver "Aviso do SmartScreen" no README.md.

#ifndef Versao
  #error Passe a versao: iscc /DVersao=<x.y.z> installer\drophunter.iss
#endif

#define NomeApp "DropHunter Agent"
#define Editora "DropHunter"
#define SiteEditora "https://www.drophunter.com.br"
#define ExeApp "DropHunterAgent.exe"
#define ExeCli "drophunter-agent.exe"
; Nome do valor em HKCU\...\Run — casado com bandeja.py (frente W1). Nao mude sozinho.
#define ValorRun "DropHunter"

[Setup]
; AppId fixo: é o que faz a próxima versão ATUALIZAR em vez de instalar de novo ao lado.
AppId={{7F3B9C21-4E8D-4A57-9B0E-2C6A1D5F8E30}
AppName={#NomeApp}
AppVersion={#Versao}
AppVerName={#NomeApp} {#Versao}
AppPublisher={#Editora}
AppPublisherURL={#SiteEditora}
AppSupportURL={#SiteEditora}
AppUpdatesURL={#SiteEditora}
; Por usuário: não pede admin, não mostra UAC, não precisa de senha de administrador.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\DropHunter
DefaultGroupName=DropHunter
DisableProgramGroupPage=yes
; Atualizacao nao repergunta a pasta: so a primeira instalacao mostra essa tela.
DisableDirPage=auto
AllowNoIcons=yes
SourceDir=..
OutputDir=dist
OutputBaseFilename=DropHunter-Setup-{#Versao}
SetupIconFile=build\icone\drophunter.ico
UninstallDisplayIcon={app}\{#ExeApp}
UninstallDisplayName={#NomeApp}
WizardStyle=modern
WizardImageFile=build\icone\wizard-grande.bmp,build\icone\wizard-grande-2x.bmp
WizardSmallImageFile=build\icone\wizard-pequeno.bmp,build\icone\wizard-pequeno-2x.bmp
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64
CloseApplications=yes
RestartApplications=no
SetupMutex=DropHunterAgentSetup
; O Setup manipula licença e chaves: nada disso pode acabar num arquivo de log.
SetupLogging=no

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "autostart"; Description: "Iniciar o DropHunter Agent junto com o Windows (recomendado)"; GroupDescription: "Opções:"
Name: "desktopicon"; Description: "Criar um atalho na área de trabalho"; GroupDescription: "Opções:"; Flags: unchecked

[Files]
; O app com janela (onedir): o .exe e tudo que ele carrega.
Source: "dist\DropHunter Agent\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; A versão de linha de comando, na mesma pasta (para quem roda em servidor/terminal).
Source: "dist\{#ExeCli}"; DestDir: "{app}"; Flags: ignoreversion
Source: "build\icone\drophunter.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "build\icone\logo.png"; DestDir: "{app}"; Flags: ignoreversion
Source: "installer\LEIA-ME.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "installer\LICENCA.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#NomeApp}"; Filename: "{app}\{#ExeApp}"; IconFilename: "{app}\drophunter.ico"; Comment: "Liga o DropHunter à sua conta do CSGOEmpire"
Name: "{group}\Desinstalar o {#NomeApp}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#NomeApp}"; Filename: "{app}\{#ExeApp}"; IconFilename: "{app}\drophunter.ico"; Tasks: desktopicon

[Registry]
; MESMO nome de valor que drophunter_agent/bandeja.py escreve ("DropHunter"): se o
; instalador e o menu da bandeja usarem nomes diferentes, o toggle da bandeja mostra
; "desligado" com o app subindo assim mesmo, e ele sobe duas vezes no boot.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#ValorRun}"; ValueData: """{app}\{#ExeApp}"""; Flags: uninsdeletevalue; Tasks: autostart
; Reinstalar desmarcando a opção tem que APAGAR a chave, senão a escolha não vale nada.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "{#ValorRun}"; Flags: deletevalue; Tasks: not autostart
; Sobra de instalador anterior, que usava outro nome: some sempre, marcada ou não.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "DropHunterAgent"; Flags: deletevalue uninsdeletevalue

[Run]
Filename: "{app}\{#ExeApp}"; Description: "Abrir o {#NomeApp} agora"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Só o que o instalador criou. A pasta de configuração do cliente
; (%USERPROFILE%\.drophunter, com a licença e as chaves DELE) fica onde está.
Type: filesandordirs; Name: "{app}\_internal"
Type: dirifempty; Name: "{app}"

[Messages]
brazilianportuguese.WelcomeLabel1=Bem-vindo ao DropHunter Agent
brazilianportuguese.WelcomeLabel2=Este assistente vai instalar o DropHunter Agent {#Versao} no seu computador.%n%nO agente é a ponte entre o DropHunter e a sua conta do CSGOEmpire: ele fica ligado no seu PC e faz as chamadas por você.%n%nNas próximas telas eu peço a sua licença e as suas chaves — uma por tela, cada uma com um link que abre a página certa para copiar.%n%nSua chave do Empire fica só neste computador. Ela nunca é enviada para o nosso servidor — nem para lugar nenhum.%n%nClique em Avançar para continuar.
brazilianportuguese.FinishedLabel=O DropHunter Agent foi instalado e a sua configuração já está gravada na pasta .drophunter da sua conta de usuário. Abra o app: o ícone fica ao lado do relógio e a janela mostra "Conectado". Para trocar uma chave depois, use Configurações no próprio app.
brazilianportuguese.ConfirmUninstall=Tem certeza de que quer remover o %1?%n%nAs suas configurações (a pasta .drophunter na sua conta de usuário) NÃO serão apagadas.
brazilianportuguese.UninstalledAll=O %1 foi removido do seu computador.%n%nAs suas configurações continuam onde estavam: a pasta .drophunter na sua conta de usuário, com a licença e as suas chaves. A chave é sua — se quiser apagar tudo, apague essa pasta à mão.

[Code]
{ ------------------------------------------------------------------------------------
  O assistente pergunta licenca, chave do Empire, chave da Steam e Steam64 — uma tela
  por assunto, cada uma com link pra pagina que gera aquela chave — e grava tudo em
  %USERPROFILE%\.drophunter\agent.toml no fim da instalacao.

  Contrato do arquivo (frente W1, drophunter_agent/config.py): licenca, empire_api_key,
  steam_api_key, steam_id64, server_url, log_level, local_api_port, api_local_usuario,
  api_local_senha. A senha da API local sai VAZIA de proposito: quem gera e o app.

  Nada aqui vai pra log (SetupLogging=no) e nada e enviado pra lugar nenhum: o Setup so
  escreve um arquivo no perfil do usuario.
  ------------------------------------------------------------------------------------ }

const
  { Laranja da marca (#EF7D2B) no formato BGR que o Windows usa em TColor. }
  COR_LINK = $2B7DEF;
  URL_PAINEL = 'https://www.drophunter.com.br/painel/';
  { Confirmada em docs.csgoempire.com/reference/getting-started-with-your-api (06/09/2026). }
  URL_EMPIRE = 'https://csgoempire.com/trading/apikey';
  URL_STEAM_KEY = 'https://steamcommunity.com/dev/apikey';
  URL_STEAM_ID = 'https://steamid.io/';
  SERVER_URL = 'wss://www.drophunter.com.br/api/agent/ws';
  TAM_LICENCA = 40;
  TAM_STEAM_KEY = 32;
  TAM_STEAM_ID = 17;
  MIN_EMPIRE = 20;

var
  PagManter: TInputOptionWizardPage;
  PagLicenca: TInputQueryWizardPage;
  PagEmpire: TInputQueryWizardPage;
  PagSteam: TInputQueryWizardPage;
  LinkPainel, LinkEmpire, LinkSteamKey, LinkSteamID: TNewStaticText;
  ConfigJaExiste: Boolean;

function CaminhoConfig(): String;
begin
  Result := ExpandConstant('{userprofile}\.drophunter\agent.toml');
end;

function ManterOQueJaTem(): Boolean;
begin
  Result := ConfigJaExiste and (PagManter <> nil) and (PagManter.SelectedValueIndex = 0);
end;

{ ------------------------------------------------------------------ links clicaveis }
procedure Abrir(const URL: String);
var
  Codigo: Integer;
begin
  ShellExec('open', URL, '', '', SW_SHOWNORMAL, ewNoWait, Codigo);
end;

{ Um manipulador por link (em vez de um so olhando o Sender): e o que o Pascal Script
  do Inno aceita sem malabarismo de tipo. }
procedure AbrirPainel(Sender: TObject);
begin
  Abrir(URL_PAINEL);
end;

procedure AbrirEmpire(Sender: TObject);
begin
  Abrir(URL_EMPIRE);
end;

procedure AbrirSteamKey(Sender: TObject);
begin
  Abrir(URL_STEAM_KEY);
end;

procedure AbrirSteamID(Sender: TObject);
begin
  Abrir(URL_STEAM_ID);
end;

function CriarLink(Pagina: TInputQueryWizardPage; const Texto: String; Campo: Integer): TNewStaticText;
begin
  Result := TNewStaticText.Create(Pagina);
  Result.Parent := Pagina.Surface;
  Result.Caption := Texto;
  Result.AutoSize := True;
  Result.Cursor := crHand;
  Result.Font.Color := COR_LINK;
  Result.Font.Style := [fsUnderline];
  { logo abaixo do campo a que ele pertence: link e campo se leem como um bloco so }
  Result.Left := Pagina.Edits[Campo].Left;
  Result.Top := Pagina.Edits[Campo].Top + Pagina.Edits[Campo].Height + ScaleY(6);
end;

{ O TInputQueryWizardPage empilha rotulo+campo sozinho, sem saber do link que eu enfio
  entre eles: quem vem depois do primeiro link precisa descer, senao os dois se sobrepoem. }
procedure DescerCampo(Pagina: TInputQueryWizardPage; Campo, Delta: Integer);
begin
  Pagina.PromptLabels[Campo].Top := Pagina.PromptLabels[Campo].Top + Delta;
  Pagina.Edits[Campo].Top := Pagina.Edits[Campo].Top + Delta;
end;

function CriarPaginaChave(Anterior: Integer; const Titulo, Descricao, Rodape: String): TInputQueryWizardPage;
begin
  Result := CreateInputQueryPage(Anterior, Titulo, Descricao, Rodape);
end;

{ ------------------------------------------------------------------------ validacao }
function EhDigito(C: Char): Boolean;
begin
  Result := (C >= '0') and (C <= '9');
end;

function EhHex(C: Char): Boolean;
begin
  Result := EhDigito(C) or ((C >= 'a') and (C <= 'f')) or ((C >= 'A') and (C <= 'F'));
end;

function EhCaractereDeLicenca(C: Char): Boolean;
begin
  { mesma classe do regex [A-Za-z0-9_-] que o servidor emite }
  Result := EhDigito(C) or ((C >= 'a') and (C <= 'z')) or ((C >= 'A') and (C <= 'Z'))
            or (C = '_') or (C = '-');
end;

function LicencaValida(const S: String): Boolean;
var
  i: Integer;
begin
  Result := False;
  if Copy(S, 1, 4) <> 'lic_' then Exit;
  if Length(S) <> 4 + TAM_LICENCA then Exit;
  for i := 5 to Length(S) do
    if not EhCaractereDeLicenca(S[i]) then Exit;
  Result := True;
end;

function SteamKeyValida(const S: String): Boolean;
var
  i: Integer;
begin
  Result := False;
  if Length(S) <> TAM_STEAM_KEY then Exit;
  for i := 1 to Length(S) do
    if not EhHex(S[i]) then Exit;
  Result := True;
end;

function Steam64Valido(const S: String): Boolean;
var
  i: Integer;
begin
  Result := False;
  if Length(S) <> TAM_STEAM_ID then Exit;
  if Copy(S, 1, 4) <> '7656' then Exit;
  for i := 1 to Length(S) do
    if not EhDigito(S[i]) then Exit;
  Result := True;
end;

{ ------------------------------------------------------------------ gravar a config }
function ComoTexto(const S: String): String;
var
  i: Integer;
  C: Char;
begin
  { string TOML basica: a barra invertida sai primeiro, senao escaparia a propria aspa }
  Result := '"';
  for i := 1 to Length(S) do begin
    C := S[i];
    if C = '\' then Result := Result + '\\'
    else if C = '"' then Result := Result + '\"'
    else Result := Result + C;
  end;
  Result := Result + '"';
end;

function ChaveDaLinha(const Linha: String): String;
var
  S: String;
  p: Integer;
begin
  Result := '';
  S := Trim(Linha);
  if (S = '') or (Copy(S, 1, 1) = '#') then Exit;
  p := Pos('=', S);
  if p = 0 then Exit;
  Result := Trim(Copy(S, 1, p - 1));
end;

procedure DefinirCampo(var Linhas: TArrayOfString; const Chave, Valor: String; SoSeFaltar: Boolean);
var
  i, n: Integer;
begin
  for i := 0 to GetArrayLength(Linhas) - 1 do
    if ChaveDaLinha(Linhas[i]) = Chave then begin
      if not SoSeFaltar then Linhas[i] := Chave + ' = ' + Valor;
      Exit;
    end;
  n := GetArrayLength(Linhas);
  SetArrayLength(Linhas, n + 1);
  Linhas[n] := Chave + ' = ' + Valor;
end;

procedure GravarConfig();
var
  Arquivo, Pasta: String;
  Linhas: TArrayOfString;
  Aproveitou: Boolean;
begin
  if ManterOQueJaTem() then Exit;
  Arquivo := CaminhoConfig();
  Pasta := ExtractFileDir(Arquivo);
  if not DirExists(Pasta) then
    if not ForceDirectories(Pasta) then
      RaiseException('Não consegui criar a pasta ' + Pasta);
  { Reinstalacao com chaves novas reescreve SO os campos perguntados: o que o app pos ali
    depois (senha da API local, teto de pagamento, Steam64 da plataforma) fica de pe. }
  Aproveitou := False;
  if FileExists(Arquivo) then
    try
      Aproveitou := LoadStringsFromUTF8File(Arquivo, Linhas);
    except
      Aproveitou := False;  { arquivo ilegivel nao pode derrubar a instalacao }
    end;
  if not Aproveitou then begin
    SetArrayLength(Linhas, 1);
    Linhas[0] := '# DropHunter Agent — config privada; não compartilhe.';
  end;
  DefinirCampo(Linhas, 'licenca', ComoTexto(Trim(PagLicenca.Values[0])), False);
  DefinirCampo(Linhas, 'empire_api_key', ComoTexto(Trim(PagEmpire.Values[0])), False);
  DefinirCampo(Linhas, 'steam_api_key', ComoTexto(Trim(PagSteam.Values[0])), False);
  DefinirCampo(Linhas, 'steam_id64', ComoTexto(Trim(PagSteam.Values[1])), False);
  DefinirCampo(Linhas, 'server_url', ComoTexto(SERVER_URL), False);
  DefinirCampo(Linhas, 'log_level', ComoTexto('INFO'), True);
  DefinirCampo(Linhas, 'local_api_port', '8765', True);
  DefinirCampo(Linhas, 'api_local_usuario', ComoTexto('drophunter'), True);
  { vazio de proposito: quem sorteia a senha da API local e o app, na primeira subida }
  DefinirCampo(Linhas, 'api_local_senha', ComoTexto(''), True);
  if not SaveStringsToUTF8FileWithoutBOM(Arquivo, Linhas, False) then
    RaiseException('Não consegui gravar ' + Arquivo);
end;

{ ------------------------------------------------------------------ ganchos do Setup }
procedure InitializeWizard();
var
  Anterior: Integer;
begin
  ConfigJaExiste := FileExists(CaminhoConfig());
  if ConfigJaExiste then begin
    PagManter := CreateInputOptionPage(wpWelcome,
      'Configuração encontrada',
      'Este computador já tem uma configuração do DropHunter.',
      'A licença e as chaves de antes continuam gravadas em ' + CaminhoConfig() + '.',
      True, False);
    PagManter.Add('Manter a configuração atual (recomendado)');
    PagManter.Add('Informar novas chaves');
    PagManter.SelectedValueIndex := 0;
  end;

  if PagManter <> nil then Anterior := PagManter.ID else Anterior := wpWelcome;

  PagLicenca := CriarPaginaChave(Anterior,
    'Licença do DropHunter',
    'Cole aqui a licença da sua conta.',
    'Ela aparece no painel do site, na aba Fatura. É o que liga este computador à sua conta.');
  PagLicenca.Add('Licença (começa com lic_):', True);
  LinkPainel := CriarLink(PagLicenca, 'Abrir a página para pegar a licença (painel, aba Fatura)', 0);
  LinkPainel.OnClick := @AbrirPainel;

  PagEmpire := CriarPaginaChave(PagLicenca.ID,
    'Chave da API do CSGOEmpire',
    'É com ela que o agente negocia por você.',
    'A chave fica gravada só neste computador e nunca é enviada ao servidor do DropHunter.');
  PagEmpire.Add('Chave da API do Empire:', True);
  LinkEmpire := CriarLink(PagEmpire, 'Abrir a página para pegar a chave no CSGOEmpire', 0);
  LinkEmpire.OnClick := @AbrirEmpire;

  PagSteam := CriarPaginaChave(PagEmpire.ID,
    'Steam',
    'Faltam a chave da Steam Web API e o seu Steam ID.',
    'São eles que deixam o agente acompanhar e recusar as trocas em seu nome.');
  PagSteam.Add('Chave da Steam Web API (32 caracteres):', True);
  PagSteam.Add('Steam ID 64 (17 dígitos, começa com 7656):', False);
  LinkSteamKey := CriarLink(PagSteam, 'Abrir a página da chave na Steam (nome de domínio: drophunter)', 0);
  LinkSteamKey.OnClick := @AbrirSteamKey;
  DescerCampo(PagSteam, 1, LinkSteamKey.Height + ScaleY(10));
  LinkSteamID := CriarLink(PagSteam, 'Não sabe o seu número? Abrir o steamid.io', 1);
  LinkSteamID.OnClick := @AbrirSteamID;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := ManterOQueJaTem()
            and ((PageID = PagLicenca.ID) or (PageID = PagEmpire.ID) or (PageID = PagSteam.ID));
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Valor: String;
begin
  Result := True;
  if CurPageID = PagLicenca.ID then begin
    Valor := Trim(PagLicenca.Values[0]);
    if not LicencaValida(Valor) then begin
      MsgBox('A licença não está no formato certo.' + #13#10#13#10
               + 'Ela começa com "lic_" e tem mais 40 caracteres (letras, números, _ ou -).'
               + #13#10 + 'Copie do painel, na aba Fatura, com o botão de copiar.', mbError, MB_OK);
      Result := False;
    end;
  end else if CurPageID = PagEmpire.ID then begin
    Valor := Trim(PagEmpire.Values[0]);
    if Length(Valor) < MIN_EMPIRE then begin
      MsgBox('Informe a chave da API do CSGOEmpire.' + #13#10#13#10
               + 'Ela é bem mais longa do que isso — copie a chave inteira da página do Empire.', mbError, MB_OK);
      Result := False;
    end;
  end else if CurPageID = PagSteam.ID then begin
    Valor := Trim(PagSteam.Values[0]);
    if not SteamKeyValida(Valor) then begin
      MsgBox('A chave da Steam Web API tem 32 caracteres (números e letras de A a F).'
               + #13#10#13#10 + 'Se ainda não tem uma, use o link da tela: no campo'
               + ' "Nome de domínio" pode escrever drophunter.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
    Valor := Trim(PagSteam.Values[1]);
    if not Steam64Valido(Valor) then begin
      MsgBox('O Steam ID 64 tem 17 dígitos e começa com 7656.' + #13#10#13#10
               + 'Se não souber o seu, abra o steamid.io pelo link da tela e cole o campo'
               + ' steamID64.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then GravarConfig();
end;
