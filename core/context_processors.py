# core/context_processors.py
from .permissions import pode_gerenciar


def permissoes(request):
    """Expõe `pode_gerenciar` para os templates (usado no menu do base.html)."""
    user = getattr(request, 'user', None)
    return {'pode_gerenciar': bool(user) and pode_gerenciar(user)}
