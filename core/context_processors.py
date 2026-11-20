# core/context_processors.py
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import PedidoOrientador, UnassignedUIDLog
from .permissions import eh_admin, pode_gerenciar


def permissoes(request):
    """Expõe para os templates o que o menu precisa saber sobre o usuário."""
    user = getattr(request, 'user', None)
    gestor = bool(user) and pode_gerenciar(user)
    contexto = {
        'pode_gerenciar': gestor,
        'eh_admin': bool(user) and eh_admin(user),
        'cartoes_pendentes_qtd': 0,
        'pedidos_orientador_qtd': 0,
    }
    if contexto['eh_admin']:
        contexto['pedidos_orientador_qtd'] = PedidoOrientador.objects.filter(status='PENDENTE').count()
    if gestor:
        limite = timezone.now() - timedelta(minutes=settings.CARTAO_PENDENTE_MINUTOS)
        contexto['cartoes_pendentes_qtd'] = UnassignedUIDLog.objects.filter(last_seen__gte=limite).count()
    return contexto
