"""Redacao de segredo por VALOR.

Tudo que sai do agente (log, corpo de resultado, evento agent_log) passa por aqui.
A chave do Empire e a da Steam NUNCA podem aparecer em texto nenhum: nem em log
local, nem em nada mandado ao servidor. Falha fechada: se por algum motivo nao der
pra redigir, o texto e substituido inteiro.
"""

from __future__ import annotations

import re
from typing import Any

MASCARA = "[REDIGIDO]"

# Formas conhecidas mesmo sem saber o valor: "Bearer xxxxx", "key=hex", token longo.
_PADROES = [
    re.compile(r"(?i)(bearer)(\s+)[A-Za-z0-9._\-]{8,}"),
    re.compile(
        r"(?i)(api[_-]?key|apikey|token|secret|password|senha)(\s*[:=]\s*)['\"]?[A-Za-z0-9._\-]{8,}"
    ),
]


class Redator:
    """Guarda os segredos conhecidos e apaga cada um deles de qualquer texto."""

    def __init__(self, segredos: list[str] | None = None) -> None:
        self._segredos: list[str] = []
        for s in segredos or []:
            self.adicionar(s)

    def adicionar(self, segredo: str | None) -> None:
        if not segredo:
            return
        segredo = str(segredo)
        if len(segredo) < 4:  # curto demais pra ser segredo; redigir viraria ruido
            return
        if segredo not in self._segredos:
            self._segredos.append(segredo)
            # maiores primeiro, pra um segredo que contem outro nao deixar resto
            self._segredos.sort(key=len, reverse=True)

    @property
    def segredos(self) -> tuple[str, ...]:
        return tuple(self._segredos)

    def texto(self, valor: Any) -> str:
        try:
            s = valor if isinstance(valor, str) else str(valor)
            for seg in self._segredos:
                if seg in s:
                    s = s.replace(seg, MASCARA)
            for pat in _PADROES:
                s = pat.sub(lambda m: _mascarar_padrao(m), s)
            return s
        except Exception:
            return MASCARA

    def estrutura(self, obj: Any) -> Any:
        """Redige recursivamente strings dentro de dict/list/tuple (JSON de saida)."""
        if isinstance(obj, str):
            return self.texto(obj)
        if isinstance(obj, dict):
            return {str(k): self.estrutura(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.estrutura(v) for v in obj]
        return obj

    def contem_segredo(self, valor: Any) -> bool:
        s = valor if isinstance(valor, str) else str(valor)
        return any(seg in s for seg in self._segredos)


def _mascarar_padrao(m: re.Match) -> str:
    grupos = m.groups()
    if grupos and grupos[0] is not None:
        return f"{grupos[0]}{grupos[1]}{MASCARA}"
    return MASCARA


REDATOR = Redator()


def redigir(valor: Any) -> str:
    return REDATOR.texto(valor)
