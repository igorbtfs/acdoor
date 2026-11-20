# core/acesso.py
"""
Regra única de acesso ao laboratório, usada pela porta (API) e pelas telas.

O cartão de um aluno abre a porta quando:
  - a conta do aluno está ativa;
  - ele tem um cartão cadastrado;
  - existe pelo menos uma orientação vigente (veja Orientacao.vigente).
"""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from django.conf import settings
from django.db.models import Prefetch
from django.utils import timezone

from .models import Orientacao, RegistroAuditoria


def validade_padrao():
    return timezone.localdate() + timedelta(days=settings.ACESSO_VALIDADE_PADRAO_DIAS)


def validade_maxima():
    return timezone.localdate() + timedelta(days=settings.ACESSO_VALIDADE_MAXIMA_DIAS)


def com_orientacoes(alunos):
    """
    Carrega de uma vez o perfil, as orientações, os orientadores e os grupos
    deles. Sem isso, cada linha de uma lista faria várias consultas ao banco.
    """
    return alunos.select_related('aluno_profile').prefetch_related(
        Prefetch(
            'orientacoes_recebidas',
            queryset=Orientacao.objects.select_related('orientador').prefetch_related('orientador__groups'),
        )
    )


def cartao_de(aluno):
    perfil = getattr(aluno, 'aluno_profile', None)
    return perfil.rfid_token if perfil else None


@dataclass
class Situacao:
    liberado: bool
    motivo: str
    valido_ate: Optional[date] = None


def situacao_do_aluno(aluno):
    """Diz se o cartão do aluno abre a porta hoje e, se não, por quê."""
    if not aluno.is_active:
        return Situacao(False, "Conta desativada")

    orientacoes = list(aluno.orientacoes_recebidas.all())
    vigentes = [o for o in orientacoes if o.vigente]

    if not vigentes:
        if not orientacoes:
            return Situacao(False, "Sem orientador")
        vencidas = [o for o in orientacoes if o.vencida]
        if len(vencidas) == len(orientacoes):
            ultima = max(o.valido_ate for o in vencidas)
            return Situacao(False, f"Orientação vencida em {ultima:%d/%m/%Y}")
        return Situacao(False, "Orientador sem permissão ou com a conta desativada")

    valido_ate = max(o.valido_ate for o in vigentes)
    if not cartao_de(aluno):
        return Situacao(False, "Sem cartão cadastrado", valido_ate)
    return Situacao(True, f"Acesso liberado até {valido_ate:%d/%m/%Y}", valido_ate)


def nome_de(user):
    return user.get_full_name() or user.username


def registrar(autor, acao, aluno, detalhe=''):
    """Grava na trilha de auditoria quem fez o quê com o acesso de qual aluno."""
    return RegistroAuditoria.objects.create(
        acao=acao,
        autor=autor,
        autor_nome=nome_de(autor),
        aluno=aluno,
        aluno_nome=nome_de(aluno),
        detalhe=detalhe[:255],
    )
