# core/adapters.py

from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.contrib.auth.models import Group
from django.shortcuts import render

from .models import ProfessorProfile

# Domínios institucionais aceitos no login e o grupo que cada um recebe.
GRUPO_POR_DOMINIO = {
    'restinga.ifrs.edu.br': 'Professores',
    'aluno.restinga.ifrs.edu.br': 'Alunos',
}


def email_verificado(sociallogin):
    """
    Retorna o e-mail confirmado pelo Google para este login.

    Toda decisão de permissão usa este e-mail, e nunca o `user.email` salvo no
    banco, porque o do banco pode ser editado pela tela de edição de aluno.
    """
    for address in sociallogin.email_addresses:
        if address.verified and address.email:
            return address.email.strip().lower()
    return ''


def promover_admin(user):
    """Garante que o usuário seja administrador (superusuário com acesso ao /admin/)."""
    campos = [campo for campo in ('is_staff', 'is_superuser') if not getattr(user, campo)]
    if campos:
        for campo in campos:
            setattr(user, campo, True)
        user.save(update_fields=campos)


class MySocialAccountAdapter(DefaultSocialAccountAdapter):

    def pre_social_login(self, request, sociallogin):
        """
        Intercepta o processo de login social logo após a autenticação
        bem-sucedida para executar validações personalizadas.
        """
        email = email_verificado(sociallogin)
        dominio = email.rpartition('@')[2]

        # Se o domínio do e-mail não estiver na lista de domínios permitidos,
        # interrompe o login e exibe uma página de erro. Vale também para quem
        # já tem cadastro (ex.: contas antigas criadas com outro domínio).
        if dominio not in GRUPO_POR_DOMINIO:
            raise ImmediateHttpResponse(render(request, 'error/domain_not_allowed.html', {
                'email': email or sociallogin.user.email
            }, status=403))

        # Usuário que já existe no sistema: só confere se deve ser administrador.
        # (Usuários novos são tratados em save_user.)
        if sociallogin.is_existing and email in settings.ADMIN_EMAILS:
            promover_admin(sociallogin.user)

    def save_user(self, request, sociallogin, form=None):
        """
        Chamado quando um novo usuário social está sendo salvo.
        É o lugar perfeito para adicionar o usuário a um grupo.
        """
        # Primeiro, salva o usuário usando o método padrão
        user = super().save_user(request, sociallogin, form)

        email = email_verificado(sociallogin)
        group_name = GRUPO_POR_DOMINIO.get(email.rpartition('@')[2])

        if group_name:
            # Pega o objeto do grupo ou cria se não existir
            group, created = Group.objects.get_or_create(name=group_name)
            user.groups.add(group)
            # Professores ganham um perfil para poder vincular bolsistas.
            if group_name == 'Professores':
                ProfessorProfile.objects.get_or_create(user=user)

        if email in settings.ADMIN_EMAILS:
            promover_admin(user)

        return user
