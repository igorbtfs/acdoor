# core/permissions.py
from .models import Orientacao, eh_gestor


def pode_gerenciar(user):
    """
    Quem pode usar as telas de gerenciamento:
    professores (grupo 'Professores') e administradores (superusuários).
    """
    return user.is_authenticated and eh_gestor(user)


def eh_admin(user):
    return user.is_authenticated and user.is_active and user.is_superuser


def pode_gerenciar_aluno(user, aluno):
    """
    Um professor só mexe nos próprios bolsistas (alunos que ele orienta, mesmo
    com a orientação vencida, para poder renovar). O administrador mexe em todos.
    """
    if eh_admin(user):
        return True
    return pode_gerenciar(user) and Orientacao.objects.filter(orientador=user, aluno=aluno).exists()
