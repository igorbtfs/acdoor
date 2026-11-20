# core/adapters.py

from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.contrib.auth.models import Group
from django.shortcuts import render


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


def papel_do_email(email):
    """
    Decide se um e-mail pode entrar e com qual papel. Retorna (pode_entrar, grupo, admin).

    Além dos domínios institucionais, entram os e-mails listados um a um no
    .env: ADMIN_EMAILS (administradores) e PROFESSOR_EMAILS (professores de
    fora do domínio do campus). Um e-mail fora das listas e dos domínios não
    entra, mesmo que seja do mesmo provedor de alguém que está na lista.
    """
    admin = email in settings.ADMIN_EMAILS
    grupo = GRUPO_POR_DOMINIO.get(email.rpartition('@')[2])
    if email in settings.PROFESSOR_EMAILS:
        grupo = 'Professores'
    return bool(email) and (admin or grupo is not None), grupo, admin


def promover_admin(user):
    """Garante que o usuário seja administrador (superusuário com acesso ao /admin/)."""
    campos = [campo for campo in ('is_staff', 'is_superuser') if not getattr(user, campo)]
    if campos:
        for campo in campos:
            setattr(user, campo, True)
        user.save(update_fields=campos)


def aplicar_papel(user, grupo, admin):
    """Põe o usuário no grupo do papel dele e, se for o caso, torna administrador."""
    if grupo:
        # Pega o objeto do grupo ou cria se não existir
        group, created = Group.objects.get_or_create(name=grupo)
        user.groups.add(group)
    if admin:
        promover_admin(user)


class MySocialAccountAdapter(DefaultSocialAccountAdapter):

    def pre_social_login(self, request, sociallogin):
        """
        Intercepta o processo de login social logo após a autenticação
        bem-sucedida para executar validações personalizadas.
        """
        email = email_verificado(sociallogin)
        pode_entrar, grupo, admin = papel_do_email(email)

        # Se o e-mail não for de um domínio permitido nem estiver nas listas,
        # interrompe o login e exibe uma página de erro. Vale também para quem
        # já tem cadastro (ex.: contas antigas criadas com outro domínio).
        if not pode_entrar:
            raise ImmediateHttpResponse(render(request, 'error/domain_not_allowed.html', {
                'email': email or sociallogin.user.email
            }, status=403))

        # Usuário que já existe no sistema: confere o papel a cada login, para
        # que um e-mail acrescentado às listas depois passe a valer.
        # (Usuários novos são tratados em save_user.)
        if sociallogin.is_existing:
            aplicar_papel(sociallogin.user, grupo, admin)

    def save_user(self, request, sociallogin, form=None):
        """
        Chamado quando um novo usuário social está sendo salvo.
        É o lugar perfeito para adicionar o usuário a um grupo.
        """
        # Primeiro, salva o usuário usando o método padrão
        user = super().save_user(request, sociallogin, form)

        _, grupo, admin = papel_do_email(email_verificado(sociallogin))
        aplicar_papel(user, grupo, admin)

        return user
