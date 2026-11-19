# core/permissions.py


def pode_gerenciar(user):
    """
    Quem pode usar as telas de gerenciamento de bolsistas:
    professores (grupo 'Professores') e administradores (superusuários).
    """
    if not user.is_authenticated or not user.is_active:
        return False
    return user.is_superuser or user.groups.filter(name='Professores').exists()
