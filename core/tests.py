from allauth.core import context
from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.helpers import complete_social_login
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from io import StringIO
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from .models import AccessLog, AlunoProfile, ProfessorProfile, UnassignedUIDLog

User = get_user_model()

ADMIN = '2020999999@aluno.restinga.ifrs.edu.br'  # e-mail fictício
GOOGLE_FAKE = {
    'google': {
        'APP': {'client_id': 'id-de-teste', 'secret': 'segredo-de-teste', 'key': ''},
        'SCOPE': ['profile', 'email'],
    }
}


def criar_usuario(email, grupo=None, **extra):
    user = User.objects.create_user(username=email.split('@')[0], email=email, **extra)
    if grupo:
        user.groups.add(Group.objects.get_or_create(name=grupo)[0])
    return user


@override_settings(SOCIALACCOUNT_PROVIDERS=GOOGLE_FAKE, ADMIN_EMAILS=[ADMIN])
class LoginGoogleTests(TestCase):
    """Exercita o fluxo real do allauth, simulando apenas a resposta do Google."""

    def login_google(self, email, sub=None, verificado=True):
        request = RequestFactory().get('/accounts/google/login/callback/')
        SessionMiddleware(lambda r: None).process_request(request)
        MessageMiddleware(lambda r: None).process_request(request)
        request.user = AnonymousUser()
        with context.request_context(request):
            provider = get_adapter().get_provider(request, 'google')
            sociallogin = provider.sociallogin_from_response(request, {
                'sub': sub or f'sub-{email}',
                'email': email,
                'email_verified': verificado,
                'given_name': 'Fulano',
                'family_name': 'de Tal',
            })
            return complete_social_login(request, sociallogin)

    def test_gmail_nao_entra_mais(self):
        response = self.login_google('alguem@gmail.com')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.exists())

    def test_dominio_parecido_nao_entra(self):
        self.assertEqual(self.login_google('x@fake-restinga.ifrs.edu.br').status_code, 403)
        self.assertEqual(self.login_google('x@restinga.ifrs.edu.br.evil.com').status_code, 403)
        self.assertFalse(User.objects.exists())

    def test_conta_gmail_antiga_e_barrada_no_login(self):
        antigo = criar_usuario('antigo@gmail.com', 'Professores')
        SocialAccount.objects.create(user=antigo, provider='google', uid='sub-antigo')
        response = self.login_google('antigo@gmail.com', sub='sub-antigo')
        self.assertEqual(response.status_code, 403)

    def test_professor_institucional(self):
        response = self.login_google('prof@restinga.ifrs.edu.br')
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email='prof@restinga.ifrs.edu.br')
        self.assertEqual(list(user.groups.values_list('name', flat=True)), ['Professores'])
        self.assertTrue(ProfessorProfile.objects.filter(user=user).exists())
        self.assertFalse(user.is_superuser or user.is_staff)

    def test_aluno_comum_nao_vira_admin(self):
        self.login_google('2020000001@aluno.restinga.ifrs.edu.br')
        user = User.objects.get(email='2020000001@aluno.restinga.ifrs.edu.br')
        self.assertEqual(list(user.groups.values_list('name', flat=True)), ['Alunos'])
        self.assertFalse(user.is_superuser or user.is_staff)

    def test_email_admin_vira_administrador_no_primeiro_login(self):
        response = self.login_google(ADMIN)
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email=ADMIN)
        self.assertTrue(user.is_superuser and user.is_staff)
        self.assertFalse(user.has_usable_password())

    def test_sem_admin_emails_ninguem_e_promovido(self):
        with override_settings(ADMIN_EMAILS=[]):
            self.login_google(ADMIN)
        self.assertFalse(User.objects.get().is_superuser)

    def test_dois_usuarios_novos_recebem_usernames_diferentes(self):
        self.login_google('fulano@restinga.ifrs.edu.br')
        self.login_google('fulano@aluno.restinga.ifrs.edu.br')
        self.assertEqual(User.objects.values('username').distinct().count(), 2)

    def test_admin_pre_cadastrado_como_aluno_e_vinculado_e_promovido(self):
        # Cenário: o aluno já tinha sido cadastrado manualmente por um professor.
        existente = criar_usuario(ADMIN, 'Alunos')
        AlunoProfile.objects.create(user=existente, curso='ADS', rfid_token='A1 B2 C3 D4')

        response = self.login_google(ADMIN)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.count(), 1)  # não duplicou a conta
        existente.refresh_from_db()
        self.assertTrue(existente.is_superuser and existente.is_staff)
        self.assertTrue(SocialAccount.objects.filter(user=existente, provider='google').exists())
        self.assertEqual(existente.aluno_profile.rfid_token, 'A1 B2 C3 D4')  # cartão preservado

    def test_aluno_pre_cadastrado_consegue_entrar(self):
        existente = criar_usuario('2020000002@aluno.restinga.ifrs.edu.br', 'Alunos')
        response = self.login_google('2020000002@aluno.restinga.ifrs.edu.br')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.count(), 1)
        existente.refresh_from_db()
        self.assertFalse(existente.is_superuser)

    def test_admin_que_ja_tinha_conta_google_e_promovido_no_proximo_login(self):
        with override_settings(ADMIN_EMAILS=[]):
            self.login_google(ADMIN)
        self.assertFalse(User.objects.get(email=ADMIN).is_superuser)

        self.login_google(ADMIN)
        self.assertTrue(User.objects.get(email=ADMIN).is_superuser)

    def test_email_nao_verificado_pelo_google_nao_entra(self):
        response = self.login_google(ADMIN, verificado=False)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.exists())

    def test_trocar_email_no_banco_nao_promove(self):
        # Um professor pode editar o e-mail de um aluno pela tela de edição.
        # Isso NÃO pode transformar o aluno em admin: vale o e-mail do Google.
        aluno_email = '2020000003@aluno.restinga.ifrs.edu.br'
        self.login_google(aluno_email, sub='sub-aluno')
        User.objects.filter(email=aluno_email).update(email=ADMIN)

        self.login_google(aluno_email, sub='sub-aluno')

        self.assertFalse(User.objects.get(socialaccount__uid='sub-aluno').is_superuser)


@override_settings(SOCIALACCOUNT_PROVIDERS=GOOGLE_FAKE, ALLOWED_HOSTS=['acdoor.exemplo.com', 'testserver'])
class AtrasDeProxyHttpsTests(TestCase):
    """O portal publicado em HTTPS por um proxy (Nginx Proxy Manager) na frente."""

    def url_de_retorno(self, **cabecalhos):
        response = self.client.post('/accounts/google/login/', **cabecalhos)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith('https://accounts.google.com/'))
        from urllib.parse import parse_qs, urlparse
        return parse_qs(urlparse(response['Location']).query)['redirect_uri'][0]

    def test_retorno_do_google_usa_https_quando_o_proxy_informa(self):
        retorno = self.url_de_retorno(HTTP_HOST='acdoor.exemplo.com', HTTP_X_FORWARDED_PROTO='https')
        self.assertEqual(retorno, 'https://acdoor.exemplo.com/accounts/google/login/callback/')

    def test_sem_proxy_continua_http(self):
        self.assertEqual(self.url_de_retorno(), 'http://testserver/accounts/google/login/callback/')

    def test_post_vindo_de_https_passa_no_csrf(self):
        from django.test import Client
        cliente = Client(enforce_csrf_checks=True)
        extras = {'HTTP_HOST': 'acdoor.exemplo.com', 'HTTP_X_FORWARDED_PROTO': 'https'}
        pagina = cliente.get('/accounts/google/login/', **extras)
        token = pagina.context['csrf_token']
        response = cliente.post('/accounts/google/login/', {'csrfmiddlewaretoken': token},
                                HTTP_ORIGIN='https://acdoor.exemplo.com', **extras)
        self.assertEqual(response.status_code, 302)


class CadastroLocalFechadoTests(TestCase):
    def test_cadastro_local_nao_existe(self):
        self.assertEqual(self.client.get('/accounts/signup/').status_code, 404)
        self.assertEqual(self.client.post('/accounts/signup/', {
            'email': 'invasor@gmail.com', 'password1': 'Senha-Forte-123', 'password2': 'Senha-Forte-123',
        }).status_code, 404)
        self.assertFalse(User.objects.exists())

    def test_login_por_senha_desligado(self):
        User.objects.create_user('fulano', 'fulano@gmail.com', 'Senha-Forte-123')
        response = self.client.post('/accounts/login/', {'login': 'fulano@gmail.com', 'password': 'Senha-Forte-123'})
        self.assertEqual(response.status_code, 403)

    def test_pagina_de_login_mostra_botao_do_google(self):
        with override_settings(SOCIALACCOUNT_PROVIDERS=GOOGLE_FAKE):
            response = self.client.get('/accounts/login/')
        self.assertContains(response, 'Login com Google')

    def test_reset_de_senha_nao_existe(self):
        self.assertEqual(self.client.get('/accounts/password/reset/').status_code, 404)


class GerenciamentoTests(TestCase):
    def setUp(self):
        self.professor = criar_usuario('prof@restinga.ifrs.edu.br', 'Professores')
        self.outro_professor = criar_usuario('outro@restinga.ifrs.edu.br', 'Professores')
        self.aluno = criar_usuario('2020000001@aluno.restinga.ifrs.edu.br', 'Alunos', first_name='Ana', last_name='Souza')
        self.aluno2 = criar_usuario('2020000002@aluno.restinga.ifrs.edu.br', 'Alunos', first_name='Beto', last_name='Lima')
        # Administrador com e-mail de aluno (superusuário + grupo Alunos)
        self.admin = criar_usuario(ADMIN, 'Alunos', is_superuser=True, is_staff=True)

    def test_anonimo_e_redirecionado_para_login(self):
        for nome in ('gerenciar_bolsistas', 'listar_bolsistas_orientadores', 'access_logs'):
            response = self.client.get(reverse(nome))
            self.assertEqual(response.status_code, 302, nome)
            self.assertIn('/accounts/login/', response['Location'], nome)

    def test_aluno_nao_acessa_gerenciamento(self):
        self.client.force_login(self.aluno)
        respostas = [
            self.client.get(reverse('gerenciar_bolsistas')),
            self.client.get(reverse('listar_bolsistas_orientadores')),
            self.client.post(reverse('delete_aluno', args=[self.aluno2.id])),
            self.client.get(reverse('edit_aluno', args=[self.aluno2.id])),
            self.client.post(reverse('gerenciar_bolsistas'), {'action': 'add', 'student_id': self.aluno2.id}),
        ]
        for response in respostas:
            # barrado: volta para a página inicial
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response['Location'].startswith('/?next='), response['Location'])
        self.assertTrue(User.objects.filter(pk=self.aluno2.pk).exists())
        self.assertNotContains(self.client.get('/'), 'Meus Bolsistas')

    def test_professor_acessa_gerenciamento(self):
        self.client.force_login(self.professor)
        self.assertEqual(self.client.get(reverse('gerenciar_bolsistas')).status_code, 200)
        self.assertEqual(self.client.get(reverse('listar_bolsistas_orientadores')).status_code, 200)
        home = self.client.get('/')
        self.assertContains(home, 'Meus Bolsistas')
        self.assertNotContains(home, '>Admin<')

    def test_admin_acessa_gerenciamento_e_admin_do_django(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('gerenciar_bolsistas')).status_code, 200)
        self.assertEqual(self.client.get(reverse('listar_bolsistas_orientadores')).status_code, 200)
        self.assertEqual(self.client.get(reverse('access_logs')).status_code, 200)
        self.assertEqual(self.client.get('/admin/').status_code, 200)
        home = self.client.get('/')
        self.assertContains(home, 'Meus Bolsistas')
        self.assertContains(home, '>Admin<')

    def test_professor_nao_exclui_quem_nao_e_aluno(self):
        self.client.force_login(self.professor)
        response = self.client.post(reverse('delete_aluno', args=[self.outro_professor.id]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(User.objects.filter(pk=self.outro_professor.pk).exists())

    def test_professor_nao_exclui_administrador(self):
        self.client.force_login(self.professor)
        self.client.post(reverse('delete_aluno', args=[self.admin.id]))
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())

    def test_professor_exclui_aluno(self):
        self.client.force_login(self.professor)
        self.client.post(reverse('delete_aluno', args=[self.aluno.id]))
        self.assertFalse(User.objects.filter(pk=self.aluno.pk).exists())

    def test_get_em_delete_nao_exclui(self):
        self.client.force_login(self.professor)
        self.client.get(reverse('delete_aluno', args=[self.aluno.id]))
        self.assertTrue(User.objects.filter(pk=self.aluno.pk).exists())

    def test_professor_nao_edita_quem_nao_e_aluno(self):
        self.client.force_login(self.professor)
        self.assertEqual(self.client.get(reverse('edit_aluno', args=[self.outro_professor.id])).status_code, 404)
        response = self.client.post(reverse('edit_aluno', args=[self.outro_professor.id]), {
            'first_name': 'X', 'last_name': 'Y', 'email': 'x@y.com', 'curso': 'ADS', 'rfid_token': 'AA BB',
        })
        self.assertEqual(response.status_code, 404)
        self.assertFalse(AlunoProfile.objects.filter(user=self.outro_professor).exists())

    def test_vincular_so_funciona_com_alunos(self):
        self.client.force_login(self.professor)
        response = self.client.post(reverse('gerenciar_bolsistas'), {'action': 'add', 'student_id': self.outro_professor.id})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(AlunoProfile.objects.filter(user=self.outro_professor).exists())

    def test_vincular_e_desvincular_bolsista(self):
        self.client.force_login(self.professor)
        self.client.post(reverse('gerenciar_bolsistas'), {'action': 'add', 'student_id': self.aluno.id})
        self.assertIn(self.aluno, self.professor.professor_profile.bolsistas.all())
        self.assertIsNone(self.aluno.aluno_profile.rfid_token)
        self.client.post(reverse('gerenciar_bolsistas'), {'action': 'remove', 'student_id': self.aluno.id})
        self.assertNotIn(self.aluno, self.professor.professor_profile.bolsistas.all())

    def test_cadastro_manual_de_aluno(self):
        self.client.force_login(self.professor)
        self.client.post(reverse('gerenciar_bolsistas'), {
            'nome_completo': 'Carla Dias Pereira', 'email': '2020000009@ALUNO.restinga.ifrs.edu.br', 'curso': 'ADS',
        })
        novo = User.objects.get(email='2020000009@aluno.restinga.ifrs.edu.br')
        self.assertEqual((novo.first_name, novo.last_name), ('Carla', 'Dias Pereira'))
        self.assertFalse(novo.has_usable_password())
        self.assertTrue(novo.groups.filter(name='Alunos').exists())
        self.assertEqual(novo.aluno_profile.curso, 'ADS')
        self.assertIn(novo, self.professor.professor_profile.bolsistas.all())

    def test_cadastro_manual_recusa_gmail_e_email_repetido(self):
        self.client.force_login(self.professor)
        total = User.objects.count()
        self.client.post(reverse('gerenciar_bolsistas'), {'nome_completo': 'A B', 'email': 'a@gmail.com', 'curso': 'ADS'})
        self.client.post(reverse('gerenciar_bolsistas'), {'nome_completo': 'A B', 'email': self.aluno.email.upper(), 'curso': 'ADS'})
        self.assertEqual(User.objects.count(), total)

    def test_cadastro_manual_com_username_ja_usado(self):
        # Antes isso estourava IntegrityError (erro 500).
        User.objects.create_user(username='2020000010', email='qualquer@restinga.ifrs.edu.br')
        self.client.force_login(self.professor)
        response = self.client.post(reverse('gerenciar_bolsistas'), {
            'nome_completo': 'Davi Rocha', 'email': '2020000010@aluno.restinga.ifrs.edu.br', 'curso': 'ADS',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.get(email='2020000010@aluno.restinga.ifrs.edu.br').username, '20200000102')

    def editar(self, aluno, rfid):
        return self.client.post(reverse('edit_aluno', args=[aluno.id]), {
            'first_name': aluno.first_name or 'Nome', 'last_name': aluno.last_name or 'Sobrenome',
            'email': aluno.email, 'curso': 'ADS', 'rfid_token': rfid,
        })

    def test_dois_alunos_sem_cartao_nao_quebra(self):
        # Antes o segundo aluno salvo sem cartão estourava erro 500 (UID '' duplicado).
        self.client.force_login(self.professor)
        self.assertEqual(self.editar(self.aluno, '').status_code, 302)
        self.assertEqual(self.editar(self.aluno2, '').status_code, 302)
        self.assertIsNone(AlunoProfile.objects.get(user=self.aluno).rfid_token)
        self.assertIsNone(AlunoProfile.objects.get(user=self.aluno2).rfid_token)

    def test_uid_do_cartao_e_normalizado_e_unico(self):
        self.client.force_login(self.professor)
        self.editar(self.aluno, '  a1 b2  c3 d4 ')
        self.assertEqual(AlunoProfile.objects.get(user=self.aluno).rfid_token, 'A1 B2 C3 D4')
        # salvar de novo o mesmo aluno com o mesmo UID funciona
        self.assertEqual(self.editar(self.aluno, 'A1 B2 C3 D4').status_code, 302)
        # outro aluno com o mesmo UID é recusado
        response = self.editar(self.aluno2, 'a1 b2 c3 d4')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'já está atribuído a outro aluno')
        # remover o cartão volta para NULL
        self.editar(self.aluno, '')
        self.assertIsNone(AlunoProfile.objects.get(user=self.aluno).rfid_token)

    def test_admin_pode_cadastrar_o_proprio_cartao(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.editar(self.admin, 'DE AD BE EF').status_code, 302)
        self.assertEqual(AlunoProfile.objects.get(user=self.admin).rfid_token, 'DE AD BE EF')

    def test_painel_geral_lista_alunos_sem_orientador(self):
        self.professor_profile = ProfessorProfile.objects.create(user=self.professor)
        self.professor_profile.bolsistas.add(self.aluno)
        self.client.force_login(self.professor)
        response = self.client.get(reverse('listar_bolsistas_orientadores'))
        self.assertIn(self.aluno2, response.context['alunos_sem_orientador'])
        self.assertNotIn(self.aluno, response.context['alunos_sem_orientador'])


class ApiPortaTests(TestCase):
    URL = '/api/v1/log-access/'

    def setUp(self):
        dispositivo = User.objects.create_user('esp32_device')
        self.api = APIClient()
        self.api.credentials(HTTP_AUTHORIZATION=f'Token {Token.objects.create(user=dispositivo).key}')

        self.professor = criar_usuario('prof@restinga.ifrs.edu.br', 'Professores')
        self.perfil_professor = ProfessorProfile.objects.create(user=self.professor)
        self.aluno = criar_usuario('2020000001@aluno.restinga.ifrs.edu.br', 'Alunos', first_name='Ana', last_name='Souza')
        AlunoProfile.objects.create(user=self.aluno, curso='ADS', rfid_token='A1 B2 C3 D4')

    def test_sem_token_nao_passa(self):
        self.assertEqual(APIClient().post(self.URL, {'uid': 'A1 B2 C3 D4'}, format='json').status_code, 401)
        self.assertEqual(APIClient().get('/api/v1/bolsistas/').status_code, 401)
        self.assertFalse(AccessLog.objects.exists())

    def test_bolsista_com_orientador_abre_a_porta(self):
        self.perfil_professor.bolsistas.add(self.aluno)
        response = self.api.post(self.URL, {'uid': 'A1 B2 C3 D4'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'access granted', 'user': 'Ana Souza'})
        log = AccessLog.objects.get()
        self.assertEqual((log.status, log.user, log.uid), ('GRANTED', self.aluno, 'A1 B2 C3 D4'))

    def test_uid_em_minusculas_tambem_e_reconhecido(self):
        self.perfil_professor.bolsistas.add(self.aluno)
        self.assertEqual(self.api.post(self.URL, {'uid': 'a1 b2 c3 d4'}, format='json').status_code, 200)

    def test_aluno_sem_orientador_e_negado(self):
        response = self.api.post(self.URL, {'uid': 'A1 B2 C3 D4'}, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(AccessLog.objects.get().status, 'DENIED')

    def test_aluno_desativado_e_negado(self):
        self.perfil_professor.bolsistas.add(self.aluno)
        User.objects.filter(pk=self.aluno.pk).update(is_active=False)
        response = self.api.post(self.URL, {'uid': 'A1 B2 C3 D4'}, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(AccessLog.objects.get().status, 'DENIED')

    def test_cartao_desconhecido_e_negado_e_registrado(self):
        response = self.api.post(self.URL, {'uid': 'FF FF FF FF'}, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['user'], 'unknown')
        primeiro = UnassignedUIDLog.objects.get(uid='FF FF FF FF')

        self.api.post(self.URL, {'uid': 'FF FF FF FF'}, format='json')
        segundo = UnassignedUIDLog.objects.get(uid='FF FF FF FF')
        self.assertEqual(UnassignedUIDLog.objects.count(), 1)
        self.assertEqual(segundo.first_seen, primeiro.first_seen)
        self.assertGreater(segundo.last_seen, primeiro.last_seen)
        self.assertEqual(AccessLog.objects.filter(status='DENIED').count(), 2)

    def test_uid_vazio_e_invalido(self):
        self.assertEqual(self.api.post(self.URL, {'uid': ''}, format='json').status_code, 400)
        self.assertEqual(self.api.post(self.URL, {}, format='json').status_code, 400)

    def test_lista_de_bolsistas(self):
        criar_usuario('2020000002@aluno.restinga.ifrs.edu.br', 'Alunos')  # aluno sem perfil/cartão
        response = self.api.get('/api/v1/bolsistas/')
        self.assertEqual(response.status_code, 200)
        por_email = {item['email']: item for item in response.json()}
        self.assertEqual(por_email[self.aluno.email]['rfid_token'], 'A1 B2 C3 D4')
        self.assertNotIn(self.professor.email, por_email)


class RevogarDominioTests(TestCase):
    def setUp(self):
        self.gmail = criar_usuario('antigo@gmail.com', 'Professores')
        self.aluno = criar_usuario('2020000001@aluno.restinga.ifrs.edu.br', 'Alunos')
        ProfessorProfile.objects.create(user=self.gmail).bolsistas.add(self.aluno)
        self.superuser = criar_usuario('dono@gmail.com', is_superuser=True, is_staff=True)
        self.institucional = criar_usuario('prof@restinga.ifrs.edu.br', 'Professores')

    def rodar(self, *args):
        saida = StringIO()
        call_command('revogar_dominio', *args, stdout=saida)
        return saida.getvalue()

    def test_sem_aplicar_so_lista(self):
        saida = self.rodar('gmail.com')
        self.assertIn('antigo@gmail.com', saida)
        self.assertIn('Nada foi alterado', saida)
        self.gmail.refresh_from_db()
        self.assertTrue(self.gmail.is_active)
        self.assertTrue(self.gmail.groups.filter(name='Professores').exists())

    def test_aplicar_desativa_sem_apagar_vinculos(self):
        self.rodar('gmail.com', '--aplicar')
        self.gmail.refresh_from_db()
        self.assertFalse(self.gmail.is_active)
        self.assertFalse(self.gmail.groups.filter(name='Professores').exists())
        # o bolsista continua vinculado (não perde o acesso à porta)
        self.assertTrue(self.aluno.orientadores.exists())
        # superusuário e contas institucionais não são tocados
        self.superuser.refresh_from_db()
        self.institucional.refresh_from_db()
        self.assertTrue(self.superuser.is_active and self.superuser.is_superuser)
        self.assertTrue(self.institucional.is_active)
        self.assertTrue(self.institucional.groups.filter(name='Professores').exists())
