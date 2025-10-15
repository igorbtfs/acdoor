# core/adapters.py

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.exceptions import ImmediateHttpResponse
from django.contrib.auth.models import Group
from django.shortcuts import render
from .models import ProfessorProfile # Importe o novo modelo

class MySocialAccountAdapter(DefaultSocialAccountAdapter):

    def pre_social_login(self, request, sociallogin):
        """
        Intercepta o processo de login social logo após a autenticação
        bem-sucedida para executar validações personalizadas.
        """
        # Pega os dados do usuário fornecidos pelo Google
        user = sociallogin.user
        email_domain = user.email.split('@')[1]

        allowed_domains = [
            'restinga.ifrs.edu.br',
            'aluno.restinga.ifrs.edu.br',
            'gmail.com'
        ]

        # Se o domínio do e-mail não estiver na lista de domínios permitidos...
        if email_domain not in allowed_domains:
            # Interrompe o processo de login e exibe uma página de erro.
            # A exceção ImmediateHttpResponse permite renderizar uma página customizada.
            raise ImmediateHttpResponse(render(request, 'error/domain_not_allowed.html', {
                'email': user.email
            }))

        # Se o usuário já existe no sistema, não faz mais nada.
        if sociallogin.is_existing:
            return

        # Adiciona dados extras ao sociallogin, que usaremos depois
        #if email_domain == 'restinga.ifrs.edu.br':
        if email_domain == 'restinga.ifrs.edu.br' or email_domain == 'gmail.com':
            sociallogin.state['group'] = 'Professores'
        elif email_domain == 'aluno.restinga.ifrs.edu.br':
            sociallogin.state['group'] = 'Alunos'


    def save_user(self, request, sociallogin, form=None):
        """
        Chamado quando um novo usuário social está sendo salvo.
        É o lugar perfeito para adicionar o usuário a um grupo.
        """
        # Primeiro, salva o usuário usando o método padrão
        user = super().save_user(request, sociallogin, form)

        # Pega o nome do grupo que definimos no pre_social_login
        group_name = sociallogin.state.get('group')

        if group_name:
            # Pega o objeto do grupo ou cria se não existir
            group, created = Group.objects.get_or_create(name=group_name)
            # Adiciona o novo usuário a esse grupo
            user.groups.add(group)
                        # Se o usuário for adicionado ao grupo 'Professores',
            # crie seu perfil se ainda não existir.
            if group_name == 'Professores':
                ProfessorProfile.objects.get_or_create(user=user)

        return user