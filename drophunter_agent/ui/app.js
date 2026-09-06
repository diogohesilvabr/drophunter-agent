'use strict';
const token = document.body.dataset.token;
const $ = id => document.getElementById(id);
const CAMPOS = ['licenca', 'empire_api_key', 'steam_api_key', 'steam_id64'];
let ocupado = false, ultimo = null;

const valores = () => Object.fromEntries(CAMPOS.map(k => [k, $(k).value.trim()]));

async function pedir(rota, corpo) {
  const resposta = await fetch('/app/' + rota, {
    method: corpo === undefined ? 'GET' : 'POST',
    headers: corpo === undefined ? {'X-DropHunter-Token': token}
      : {'X-DropHunter-Token': token, 'Content-Type': 'application/json'},
    body: corpo === undefined ? undefined : JSON.stringify(corpo),
    cache: 'no-store', signal: AbortSignal.timeout(30000),
  });
  if (resposta.status === 403) throw new Error('Sessão expirada. Reabra o DropHunter pela bandeja.');
  return await resposta.json();
}

function segundos(valor) {
  if (valor === null || valor === undefined) return '—';
  if (valor < 60) return `há ${valor} s`;
  return `há ${Math.floor(valor / 60)} min`;
}

function pintar(e) {
  ultimo = e;
  $('ponto').dataset.estado = e.estado;
  $('rotulo').textContent = e.rotulo;
  let detalhe = e.motivo || '';
  if (e.estado === 'desconectado' && e.tentativa_s !== null && e.tentativa_s !== undefined) {
    detalhe = (detalhe ? detalhe + ' · ' : '') + `tentando de novo em ${e.tentativa_s} s`;
  }
  if (e.estado === 'conectado' && !detalhe) detalhe = 'O servidor está operando pela sua chave local.';
  $('detalhe').textContent = detalhe;
  $('detalhe').classList.toggle('erro', e.estado === 'recusado');
  $('conta').textContent = e.conta || '—';
  $('heartbeat').textContent = e.estado === 'conectado' ? segundos(e.heartbeat_s) : '—';
  $('pagamentos').textContent = e.pagamentos;
  $('aviso').hidden = e.estado !== 'sem_config';
  $('linha-autostart').hidden = !e.autostart_disponivel;
  if (document.activeElement !== $('autostart')) $('autostart').checked = !!e.autostart;
  CAMPOS.forEach(k => {
    $('atual-' + k).textContent = e.campos[k] ? 'atual: ' + e.campos[k] : 'ainda não configurada';
  });
}

async function atualizar() {
  try { pintar(await pedir('estado')); } catch (erro) { $('detalhe').textContent = erro.message; }
}

function mostrar(tela) {
  $('principal').hidden = tela !== 'principal';
  $('configuracoes').hidden = tela === 'principal';
  $('mensagem').textContent = '';
  if (tela !== 'principal') $('licenca').focus();
}

function recado(texto, erro) {
  $('mensagem').textContent = texto;
  $('mensagem').classList.toggle('erro', !!erro);
}

function travar(estado) {
  ocupado = estado;
  document.querySelectorAll('#configuracoes button').forEach(b => { b.disabled = estado; });
}

async function executar(rota, corpo) {
  if (ocupado) return {ok: false};
  travar(true);
  recado('Aguarde…', false);
  try {
    const r = await pedir(rota, corpo);
    recado(r.mensagem || '', !r.ok);
    return r;
  } catch (erro) {
    recado(erro.message, true);
    return {ok: false};
  } finally {
    travar(false);
  }
}

document.querySelectorAll('[data-abrir]').forEach(b => b.addEventListener('click', () =>
  executar('abrir', {alvo: b.dataset.abrir})));

document.querySelectorAll('[data-testar]').forEach(b => b.addEventListener('click', async () => {
  const enviados = valores();
  const r = await executar('testar/' + b.dataset.testar, enviados);
  if (r.ok && r.steam_id64 && $('empire_api_key').value.trim() === enviados.empire_api_key) {
    $('steam_id64').value = r.steam_id64;
  }
}));

$('formulario').addEventListener('submit', async evento => {
  evento.preventDefault();
  const r = await executar('salvar', valores());
  if (r.ok) {
    CAMPOS.forEach(k => { $(k).value = ''; });
    await atualizar();
  }
});

$('autostart').addEventListener('change', async () => {
  const r = await executar('autostart', {ativo: $('autostart').checked});
  if (r.mensagem === '') recado($('autostart').checked
    ? 'O DropHunter vai abrir junto com o Windows.' : 'O DropHunter não abre mais sozinho.', false);
  if (!r.ok) $('autostart').checked = !$('autostart').checked;
});

$('painel').addEventListener('click', () => pedir('abrir', {alvo: 'painel'}).catch(() => {}));
$('ir-config').addEventListener('click', () => mostrar('configuracoes'));
$('voltar').addEventListener('click', () => mostrar('principal'));
$('minimizar').addEventListener('click', () => pedir('janela', {acao: 'minimizar'}).catch(() => {}));
$('sair').addEventListener('click', () => pedir('janela', {acao: 'sair'}).catch(() => {}));

mostrar(location.hash === '#configuracoes' ? 'configuracoes' : 'principal');
atualizar();
setInterval(() => { if (!ocupado) atualizar(); }, 2000);
