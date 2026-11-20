from datetime import timedelta
from io import StringIO
from urllib.parse import parse_qs, urlparse

from allauth.core import context
from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.helpers import complete_social_login
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from .acesso import com_orientacoes, situacao_do_aluno
from .models import AccessLog, AlunoProfile, Orientacao, PedidoOrientador, RegistroAuditoria, UnassignedUIDLog
from .orientadores import retirar_papel
from .templatetags.acdoor import ha_quanto, uid_para

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


def dia(deslocamento):
    """Data de hoje mais (ou menos) alguns dias."""
    return timezone.localdate() + timedelta(days=deslocamento)


def orientar(orientador, aluno, dias=30):
    return Orientacao.objects.create(orientador=orientador, aluno=aluno, valido_ate=dia(dias))


def dar_cartao(aluno, uid):
    AlunoProfile.objects.update_or_create(user=aluno, defaults={'curso': 'ADS', 'rfid_token': uid})


def situacao(aluno):
    """A mesma leitura que a porta e as telas fazem."""
    return situacao_do_aluno(com_orientacoes(User.objects.filter(pk=aluno.pk)).get())


def cartao_pendente(uid, minutos_atras=0):
    cartao = UnassignedUIDLog.objects.create(uid=uid)
    UnassignedUIDLog.objects.filter(pk=cartao.pk).update(last_seen=timezone.now() - timedelta(minutes=minutos_atras))
    return cartao


def acoes_auditadas():
    return list(RegistroAuditoria.objects.order_by('id').values_list('acao', flat=True))


class Cenario(TestCase):
    """Dois professores, dois alunos e um administrador com e-mail de aluno."""

    def setUp(self):
        self.professor = criar_usuario('maria@restinga.ifrs.edu.br', 'Professores', first_name='Maria', last_name='Silva')
        self.outro_professor = criar_usuario('joao@restinga.ifrs.edu.br', 'Professores', first_name='João', last_name='Pereira')
        self.aluno = criar_usuario('2020000001@aluno.restinga.ifrs.edu.br', 'Alunos', first_name='Ana', last_name='Souza')
        self.aluno2 = criar_usuario('2020000002@aluno.restinga.ifrs.edu.br', 'Alunos', first_name='Beto', last_name='Lima')
        self.admin = criar_usuario(ADMIN, 'Alunos', first_name='Igor', last_name='Admin', is_superuser=True, is_staff=True)


# --- Login pelo Google --------------------------------------------------------

PROFESSOR_DE_FORA = 'fulano@outrocampus.ifrs.edu.br'
ADMIN_PESSOAL = 'dono@gmail.com'


@override_settings(SOCIALACCOUNT_PROVIDERS=GOOGLE_FAKE, ADMIN_EMAILS=[ADMIN], PROFESSOR_EMAILS=[])
class LoginGoogleTests(TestCase):
    """Exercita o fluxo real do allauth, simulando apenas a resposta do Google."""

    def login_google(self, email, sub=None, verificado=True):
        request = RequestFactory().get('/accounts/google/login/callback/')
        SessionMiddleware(lambda r: None).process_request(request)
        MessageMiddleware(lambda r: None).process_request(request)
        request.user = AnonymousUser()
        self.ultimo_request = request
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

    def grupos_de(self, email):
        return sorted(User.objects.get(email=email).groups.values_list('name', flat=True))

    def pedido(self, email, status):
        return PedidoOrientador.objects.create(email=email, origem='ADMIN', pedido_por_nome='Admin', status=status)

    def test_servidor_do_campus_entra_sem_papel_de_orientador(self):
        # O domínio dos servidores vale para todo o campus, não só para quem orienta.
        response = self.login_google('prof@restinga.ifrs.edu.br')
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email='prof@restinga.ifrs.edu.br')
        self.assertEqual(self.grupos_de(user.email), ['Servidores'])
        self.assertFalse(user.is_superuser or user.is_staff)

    def test_servidor_com_pedido_aprovado_vira_orientador_no_login(self):
        pedido = self.pedido('prof@restinga.ifrs.edu.br', 'APROVADO')  # aprovado antes de a pessoa ter conta
        self.login_google('PROF@restinga.ifrs.edu.br')
        self.assertEqual(self.grupos_de('prof@restinga.ifrs.edu.br'), ['Professores', 'Servidores'])
        pedido.refresh_from_db()
        self.assertEqual(pedido.usuario, User.objects.get())

    def test_pedido_pendente_recusado_ou_revogado_nao_da_o_papel(self):
        for status in ('PENDENTE', 'RECUSADO', 'REVOGADO'):
            email = f'{status.lower()}@restinga.ifrs.edu.br'
            self.pedido(email, status)
            self.login_google(email)
            self.assertEqual(self.grupos_de(email), ['Servidores'], status)

    def test_aprovacao_so_vale_para_o_dominio_dos_servidores(self):
        self.pedido('alguem@gmail.com', 'APROVADO')
        self.pedido('2020000005@aluno.restinga.ifrs.edu.br', 'APROVADO')
        self.assertEqual(self.login_google('alguem@gmail.com').status_code, 403)
        self.login_google('2020000005@aluno.restinga.ifrs.edu.br')
        self.assertEqual(self.grupos_de('2020000005@aluno.restinga.ifrs.edu.br'), ['Alunos'])

    def test_quem_ja_era_orientador_continua_sendo(self):
        # Contas criadas quando o domínio dava o papel direto não perdem o papel sozinhas.
        antigo = criar_usuario('antiga@restinga.ifrs.edu.br', 'Professores')
        SocialAccount.objects.create(user=antigo, provider='google', uid='sub-antiga')
        self.login_google('antiga@restinga.ifrs.edu.br', sub='sub-antiga')
        self.assertEqual(self.grupos_de('antiga@restinga.ifrs.edu.br'), ['Professores', 'Servidores'])

    def test_papel_retirado_nao_volta_no_proximo_login(self):
        email = 'prof@restinga.ifrs.edu.br'
        self.pedido(email, 'APROVADO')
        self.login_google(email)
        admin = criar_usuario(ADMIN, is_superuser=True)

        retirar_papel(User.objects.get(email=email), admin)
        self.login_google(email)

        self.assertEqual(self.grupos_de(email), ['Servidores'])
        self.assertEqual(PedidoOrientador.objects.get().status, 'REVOGADO')

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

    @override_settings(ADMIN_EMAILS=[ADMIN, ADMIN_PESSOAL])
    def test_email_pessoal_na_lista_entra_como_administrador(self):
        self.assertEqual(self.login_google(ADMIN_PESSOAL.upper()).status_code, 302)
        user = User.objects.get(email__iexact=ADMIN_PESSOAL)
        self.assertTrue(user.is_superuser and user.is_staff)
        self.assertEqual(user.groups.count(), 0)  # não vira aluno nem professor por causa do domínio
        # o resto do provedor continua barrado: vale o e-mail exato, não o domínio
        self.assertEqual(self.login_google('outro@gmail.com').status_code, 403)
        self.assertEqual(self.login_google('dono@gmail.com.br').status_code, 403)
        self.assertEqual(User.objects.count(), 1)

    @override_settings(ADMIN_EMAILS=[ADMIN, ADMIN_PESSOAL])
    def test_email_da_lista_nao_verificado_pelo_google_nao_entra(self):
        self.assertEqual(self.login_google(ADMIN_PESSOAL, verificado=False).status_code, 403)
        self.assertFalse(User.objects.exists())

    @override_settings(PROFESSOR_EMAILS=[PROFESSOR_DE_FORA])
    def test_email_de_outro_campus_na_lista_entra_como_professor(self):
        self.assertEqual(self.login_google(PROFESSOR_DE_FORA).status_code, 302)
        user = User.objects.get(email=PROFESSOR_DE_FORA)
        self.assertEqual(list(user.groups.values_list('name', flat=True)), ['Professores'])
        self.assertFalse(user.is_superuser or user.is_staff)
        # colegas do mesmo domínio que não estão na lista não entram
        self.assertEqual(self.login_google('beltrano@outrocampus.ifrs.edu.br').status_code, 403)
        self.assertEqual(User.objects.count(), 1)

    def test_sem_as_listas_esses_emails_nao_entram(self):
        self.assertEqual(self.login_google(PROFESSOR_DE_FORA).status_code, 403)
        self.assertEqual(self.login_google(ADMIN_PESSOAL).status_code, 403)
        self.assertFalse(User.objects.exists())

    def test_quem_ja_tinha_conta_ganha_o_papel_ao_entrar_na_lista(self):
        aluno_email = '2020000004@aluno.restinga.ifrs.edu.br'
        self.login_google(aluno_email)
        self.assertFalse(User.objects.get().groups.filter(name='Professores').exists())

        with override_settings(PROFESSOR_EMAILS=[aluno_email]):
            self.login_google(aluno_email)

        self.assertEqual(sorted(User.objects.get().groups.values_list('name', flat=True)), ['Alunos', 'Professores'])

    @override_settings(PROFESSOR_EMAILS=[PROFESSOR_DE_FORA])
    def test_sair_da_lista_barra_o_proximo_login(self):
        self.login_google(PROFESSOR_DE_FORA)
        with override_settings(PROFESSOR_EMAILS=[]):
            self.assertEqual(self.login_google(PROFESSOR_DE_FORA).status_code, 403)

    def test_login_nao_deixa_mensagem_pendurada(self):
        # Antes, "Conectado com sucesso como ..." aparecia mais tarde numa tela qualquer.
        from django.contrib.messages import get_messages
        self.assertEqual(self.login_google('prof@restinga.ifrs.edu.br').status_code, 302)
        self.assertEqual([str(m) for m in get_messages(self.ultimo_request)], [])

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
        dar_cartao(existente, 'A1 B2 C3 D4')

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
        # O e-mail do cadastro pode ser editado no portal. Isso NÃO pode
        # transformar o aluno em admin: vale o e-mail confirmado pelo Google.
        aluno_email = '2020000003@aluno.restinga.ifrs.edu.br'
        self.login_google(aluno_email, sub='sub-aluno')
        User.objects.filter(email=aluno_email).update(email=ADMIN)

        self.login_google(aluno_email, sub='sub-aluno')

        self.assertFalse(User.objects.get(socialaccount__uid='sub-aluno').is_superuser)


@override_settings(SOCIALACCOUNT_PROVIDERS=GOOGLE_FAKE, ALLOWED_HOSTS=['acdoor.exemplo.com', 'testserver'])
class CaminhoAteOGoogleTests(TestCase):
    """Do clique em "Entrar" até o redirecionamento para o Google."""

    def url_de_retorno(self, **cabecalhos):
        response = self.client.post('/accounts/google/login/', **cabecalhos)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith('https://accounts.google.com/'))
        return parse_qs(urlparse(response['Location']).query)['redirect_uri'][0]

    def test_retorno_do_google_usa_https_quando_o_proxy_informa(self):
        retorno = self.url_de_retorno(HTTP_HOST='acdoor.exemplo.com', HTTP_X_FORWARDED_PROTO='https')
        self.assertEqual(retorno, 'https://acdoor.exemplo.com/accounts/google/login/callback/')

    def test_sem_proxy_continua_http(self):
        self.assertEqual(self.url_de_retorno(), 'http://testserver/accounts/google/login/callback/')

    def test_post_vindo_de_https_passa_no_csrf(self):
        cliente = Client(enforce_csrf_checks=True)
        extras = {'HTTP_HOST': 'acdoor.exemplo.com', 'HTTP_X_FORWARDED_PROTO': 'https'}
        pagina = cliente.get('/accounts/login/', **extras)
        token = pagina.context['csrf_token']
        response = cliente.post('/accounts/google/login/', {'csrfmiddlewaretoken': token},
                                HTTP_ORIGIN='https://acdoor.exemplo.com', **extras)
        self.assertEqual(response.status_code, 302)

    def test_botao_de_login_vai_direto_para_o_google(self):
        # Antes eram duas telas: a de login e uma intermediária "Continuar".
        # O botão agora envia um POST direto para o provedor.
        for pagina in ('/accounts/login/', '/'):
            response = self.client.get(pagina)
            self.assertContains(response, '<form method="post" action="/accounts/google/login/', msg_prefix=pagina)
            self.assertNotContains(response, 'href="/accounts/google/login/', msg_prefix=pagina)

    def test_botao_de_login_preserva_o_destino(self):
        response = self.client.get('/accounts/login/?next=/alunos/')
        self.assertContains(response, 'action="/accounts/google/login/?next=%2Falunos%2F"')
        destino = self.client.post('/accounts/google/login/?next=%2Falunos%2F')
        self.assertTrue(destino['Location'].startswith('https://accounts.google.com/'))


@override_settings(SOCIALACCOUNT_PROVIDERS=GOOGLE_FAKE)
class InterfaceTests(Cenario):
    """Tela de entrada, sair com um clique, títulos e páginas de erro."""

    def test_tela_de_entrada_tem_o_botao_do_google(self):
        for pagina in ('/', '/accounts/login/'):
            response = self.client.get(pagina)
            self.assertContains(response, 'Fazer login com o Google', msg_prefix=pagina)
            self.assertContains(response, 'class="btn-google"', msg_prefix=pagina)
            self.assertContains(response, '<svg', msg_prefix=pagina)       # o "G" do Google
            self.assertContains(response, 'container estreito', msg_prefix=pagina)
            self.assertNotContains(response, '{#', msg_prefix=pagina)   # comentário de template vazando
            self.assertNotContains(response, '{%', msg_prefix=pagina)

    def test_marca_do_campus_aparece_uma_vez_por_pagina(self):
        logo = 'core/img/ifrs-campus-restinga-horizontal.png'
        alt = 'alt="Instituto Federal do Rio Grande do Sul, Campus Restinga"'
        # na entrada, dentro do cartão (sem rodapé, para não repetir)
        for pagina in ('/', '/accounts/login/'):
            response = self.client.get(pagina)
            self.assertContains(response, logo, count=1, msg_prefix=pagina)
            self.assertContains(response, alt, msg_prefix=pagina)
            self.assertContains(response, 'logo-entrada', msg_prefix=pagina)
            self.assertNotContains(response, '<footer', msg_prefix=pagina)
        # nas demais, no rodapé
        self.client.force_login(self.professor)
        for nome in ('home', 'alunos', 'access_logs'):
            response = self.client.get(reverse(nome))
            self.assertContains(response, logo, count=1, msg_prefix=nome)
            self.assertContains(response, '<footer class="rodape">', msg_prefix=nome)

    def test_arquivo_da_marca_existe_e_e_o_original(self):
        from django.contrib.staticfiles import finders
        caminho = finders.find('core/img/ifrs-campus-restinga-horizontal.png')
        self.assertIsNotNone(caminho)
        with open(caminho, 'rb') as arquivo:
            dados = arquivo.read()
        self.assertEqual(dados[:8], b'\x89PNG\r\n\x1a\n')
        # proporção original (660x183), a mesma declarada no HTML: a marca não pode ser distorcida
        self.assertEqual((int.from_bytes(dados[16:20], 'big'), int.from_bytes(dados[20:24], 'big')), (660, 183))

    def test_sair_com_um_clique(self):
        self.client.force_login(self.professor)
        pagina = self.client.get('/')
        self.assertContains(pagina, '<form method="post" action="/accounts/logout/"')

        response = self.client.post('/accounts/logout/')
        self.assertRedirects(response, '/', fetch_redirect_response=False)
        self.assertIn('/accounts/login/', self.client.get(reverse('alunos'))['Location'])

    def test_cada_pagina_tem_o_proprio_titulo(self):
        self.assertContains(self.client.get('/accounts/login/'), '<title>Entrar · AC-Door IFRS</title>')
        self.client.force_login(self.admin)
        for nome, titulo in (('home', 'Início'), ('alunos', 'Alunos'), ('cartoes_pendentes', 'Cartões pendentes'),
                             ('gerenciar_bolsistas', 'Meus bolsistas'), ('access_logs', 'Logs de acesso'),
                             ('listar_bolsistas_orientadores', 'Orientadores'), ('auditoria', 'Auditoria')):
            self.assertContains(self.client.get(reverse(nome)), f'<title>{titulo} · AC-Door IFRS</title>')

    def test_atalho_de_teclado_e_pagina_atual_no_menu(self):
        self.client.force_login(self.professor)
        pagina = self.client.get(reverse('alunos'))
        self.assertContains(pagina, '<a class="pular" href="#conteudo">')
        self.assertContains(pagina, 'id="conteudo"')
        self.assertContains(pagina, 'class="ativo" aria-current="page">Alunos</a>')
        self.assertContains(pagina, '<th scope="col">Aluno</th>')

    def test_pagina_nao_encontrada(self):
        response = self.client.get('/isso-nao-existe/')
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'Página não encontrada', status_code=404)
        self.assertContains(response, 'Voltar para o início', status_code=404)

    def test_envio_recusado_por_seguranca_explica_o_que_houve(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.professor)
        response = cliente.post(reverse('reivindicar_aluno', args=[self.aluno.id]), {'valido_ate': dia(10)})
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, 'Nada foi alterado', status_code=403)
        self.assertFalse(Orientacao.objects.exists())


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

    def test_reset_de_senha_nao_existe(self):
        self.assertEqual(self.client.get('/accounts/password/reset/').status_code, 404)


# --- Regra de acesso ----------------------------------------------------------

class RegraDeAcessoTests(Cenario):
    """A regra que a porta e as telas compartilham (core/acesso.py)."""

    def test_sem_orientador(self):
        dar_cartao(self.aluno, 'A1 B2 C3 D4')
        s = situacao(self.aluno)
        self.assertFalse(s.liberado)
        self.assertEqual(s.motivo, 'Sem orientador')

    def test_com_orientador_mas_sem_cartao(self):
        orientar(self.professor, self.aluno)
        s = situacao(self.aluno)
        self.assertFalse(s.liberado)
        self.assertEqual(s.motivo, 'Sem cartão cadastrado')

    def test_com_cartao_e_orientacao_em_dia(self):
        dar_cartao(self.aluno, 'A1 B2 C3 D4')
        orientar(self.professor, self.aluno, dias=10)
        s = situacao(self.aluno)
        self.assertTrue(s.liberado)
        self.assertEqual(s.valido_ate, dia(10))

    def test_vale_ate_o_proprio_dia_da_validade(self):
        dar_cartao(self.aluno, 'A1 B2 C3 D4')
        orientar(self.professor, self.aluno, dias=0)
        self.assertTrue(situacao(self.aluno).liberado)

    def test_orientacao_vencida(self):
        dar_cartao(self.aluno, 'A1 B2 C3 D4')
        orientar(self.professor, self.aluno, dias=-1)
        s = situacao(self.aluno)
        self.assertFalse(s.liberado)
        self.assertIn('vencida', s.motivo)

    def test_basta_uma_orientacao_em_dia(self):
        dar_cartao(self.aluno, 'A1 B2 C3 D4')
        orientar(self.professor, self.aluno, dias=-5)
        orientar(self.outro_professor, self.aluno, dias=5)
        self.assertTrue(situacao(self.aluno).liberado)

    def test_orientador_desativado_nao_vale(self):
        dar_cartao(self.aluno, 'A1 B2 C3 D4')
        orientar(self.professor, self.aluno)
        User.objects.filter(pk=self.professor.pk).update(is_active=False)
        self.assertFalse(situacao(self.aluno).liberado)

    def test_orientador_que_deixou_de_ser_professor_nao_vale(self):
        dar_cartao(self.aluno, 'A1 B2 C3 D4')
        orientar(self.professor, self.aluno)
        self.professor.groups.clear()
        self.assertFalse(situacao(self.aluno).liberado)

    def test_ninguem_libera_o_proprio_acesso(self):
        dar_cartao(self.admin, 'DE AD BE EF')
        orientar(self.admin, self.admin)
        self.assertFalse(situacao(self.admin).liberado)

    def test_administrador_vale_como_orientador_de_outro_aluno(self):
        dar_cartao(self.aluno, 'A1 B2 C3 D4')
        orientar(self.admin, self.aluno)
        self.assertTrue(situacao(self.aluno).liberado)

    def test_aluno_desativado(self):
        dar_cartao(self.aluno, 'A1 B2 C3 D4')
        orientar(self.professor, self.aluno)
        User.objects.filter(pk=self.aluno.pk).update(is_active=False)
        self.assertFalse(situacao(self.aluno).liberado)

    def test_uid_so_aparece_inteiro_para_o_administrador(self):
        self.assertEqual(uid_para('A1 B2 C3 D4', True), 'A1 B2 C3 D4')
        self.assertEqual(uid_para('A1 B2 C3 D4', False), '•• •• C3 D4')
        self.assertEqual(uid_para('04 9F 22 1A 7C 10 3E', False), '•• •• •• •• •• 10 3E')
        self.assertEqual(uid_para(None, False), '')

    def test_tempo_desde_a_leitura_do_cartao(self):
        agora = timezone.now()
        self.assertEqual(ha_quanto(agora - timedelta(seconds=40)), 'há menos de 1 minuto')
        self.assertEqual(ha_quanto(agora - timedelta(seconds=70)), 'há 1 minuto')
        self.assertEqual(ha_quanto(agora - timedelta(minutes=7, seconds=5)), 'há 7 minutos')


# --- Menus e permissões por perfil --------------------------------------------

PAGINAS_DE_GESTOR = ('gerenciar_bolsistas', 'alunos', 'cartoes_pendentes', 'access_logs')
PAGINAS_DE_ADMIN = ('listar_bolsistas_orientadores', 'auditoria')


class PerfisTests(Cenario):
    def assertBarrado(self, response):
        """Quem não tem permissão volta para a página inicial."""
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith('/?next='), response['Location'])

    def test_anonimo_e_redirecionado_para_login(self):
        for nome in PAGINAS_DE_GESTOR + PAGINAS_DE_ADMIN:
            response = self.client.get(reverse(nome))
            self.assertEqual(response.status_code, 302, nome)
            self.assertIn('/accounts/login/', response['Location'], nome)

    def test_aluno_so_tem_a_pagina_inicial(self):
        self.client.force_login(self.aluno)
        for nome in PAGINAS_DE_GESTOR + PAGINAS_DE_ADMIN:
            self.assertBarrado(self.client.get(reverse(nome)))
        self.assertBarrado(self.client.post(reverse('delete_aluno', args=[self.aluno2.id])))
        self.assertBarrado(self.client.get(reverse('edit_aluno', args=[self.aluno2.id])))
        self.assertBarrado(self.client.post(reverse('reivindicar_aluno', args=[self.aluno2.id]), {'valido_ate': dia(10)}))
        self.assertBarrado(self.client.post(reverse('vincular_cartao'), {}))
        self.assertTrue(User.objects.filter(pk=self.aluno2.pk).exists())
        self.assertFalse(Orientacao.objects.exists())

        home = self.client.get('/')
        self.assertContains(home, 'Meu acesso')
        for texto in ('Meus bolsistas', 'Cartões pendentes', 'Auditoria', '>Admin<'):
            self.assertNotContains(home, texto)

    def test_professor_tem_as_telas_de_gestao_mas_nao_as_de_admin(self):
        self.client.force_login(self.professor)
        for nome in PAGINAS_DE_GESTOR:
            self.assertEqual(self.client.get(reverse(nome)).status_code, 200, nome)
        for nome in PAGINAS_DE_ADMIN:
            self.assertBarrado(self.client.get(reverse(nome)))

        home = self.client.get('/')
        for texto in ('Meus bolsistas', 'Alunos', 'Cartões pendentes', 'Logs'):
            self.assertContains(home, texto)
        for texto in ('Orientadores', 'Auditoria', '>Admin<', 'Meu acesso'):
            self.assertNotContains(home, texto)

    def test_admin_tem_tudo(self):
        self.client.force_login(self.admin)
        for nome in PAGINAS_DE_GESTOR + PAGINAS_DE_ADMIN:
            self.assertEqual(self.client.get(reverse(nome)).status_code, 200, nome)
        self.assertEqual(self.client.get('/admin/').status_code, 200)

        home = self.client.get('/')
        for texto in ('Meus bolsistas', 'Orientadores', 'Auditoria', '>Admin<', 'Meu acesso'):
            self.assertContains(home, texto)

    def test_mensagem_de_login_do_allauth_foi_desligada(self):
        from django.template.loader import render_to_string
        self.assertEqual(render_to_string('account/messages/logged_in.txt').strip(), '')
        self.assertEqual(render_to_string('account/messages/logged_out.txt').strip(), '')


class PaginaInicialDoAlunoTests(Cenario):
    def test_mostra_a_propria_situacao(self):
        self.client.force_login(self.aluno)
        self.assertContains(self.client.get('/'), 'Seu cartão ainda não abre a porta')

        dar_cartao(self.aluno, 'A1 B2 C3 D4')
        orientar(self.professor, self.aluno, dias=20)
        home = self.client.get('/')
        self.assertContains(home, 'Seu cartão abre a porta')
        self.assertContains(home, 'Maria Silva')
        self.assertContains(home, dia(20).strftime('%d/%m/%Y'))

    def test_so_ve_os_proprios_acessos(self):
        dar_cartao(self.aluno, 'A1 B2 C3 D4')
        dar_cartao(self.aluno2, '11 22 33 44')
        AccessLog.objects.create(uid='A1 B2 C3 D4', status='GRANTED', user=self.aluno)
        AccessLog.objects.create(uid='11 22 33 44', status='GRANTED', user=self.aluno2)
        AccessLog.objects.create(uid='FF FF FF FF', status='DENIED', user=None)

        self.client.force_login(self.aluno)
        home = self.client.get('/')

        self.assertEqual(len(home.context['meus_acessos']), 1)
        self.assertNotContains(home, 'Beto')
        self.assertNotContains(home, '33 44')
        self.assertNotContains(home, 'FF FF')
        # nem o próprio UID aparece inteiro na tela
        self.assertNotContains(home, 'A1 B2 C3 D4')
        self.assertContains(home, '•• •• C3 D4')


# --- Meus bolsistas -----------------------------------------------------------

class MeusBolsistasTests(Cenario):
    def setUp(self):
        super().setUp()
        self.minha = orientar(self.professor, self.aluno, dias=10)
        self.dele = orientar(self.outro_professor, self.aluno2, dias=10)
        self.client.force_login(self.professor)

    def test_lista_so_os_meus(self):
        response = self.client.get(reverse('gerenciar_bolsistas'))
        self.assertEqual([linha['aluno'] for linha in response.context['linhas']], [self.aluno])
        self.assertNotContains(response, 'Beto')

    def test_renovar_a_minha(self):
        self.client.post(reverse('renovar_orientacao', args=[self.minha.id]), {'valido_ate': dia(90)})
        self.minha.refresh_from_db()
        self.assertEqual(self.minha.valido_ate, dia(90))
        self.assertEqual(acoes_auditadas(), ['ORIENTACAO_RENOVADA'])

    def test_validade_no_passado_ou_longa_demais_e_recusada(self):
        for data in (dia(-1), dia(367)):
            self.client.post(reverse('renovar_orientacao', args=[self.minha.id]), {'valido_ate': data})
            self.minha.refresh_from_db()
            self.assertEqual(self.minha.valido_ate, dia(10))
        self.assertEqual(acoes_auditadas(), [])

    def test_nao_alcanco_a_orientacao_de_outro_professor(self):
        self.assertEqual(self.client.post(reverse('renovar_orientacao', args=[self.dele.id]), {'valido_ate': dia(90)}).status_code, 404)
        self.assertEqual(self.client.post(reverse('encerrar_orientacao', args=[self.dele.id])).status_code, 404)
        self.dele.refresh_from_db()
        self.assertEqual(self.dele.valido_ate, dia(10))

    def test_retirar_o_acesso(self):
        self.client.post(reverse('encerrar_orientacao', args=[self.minha.id]))
        self.assertFalse(Orientacao.objects.filter(pk=self.minha.pk).exists())
        self.assertEqual(acoes_auditadas(), ['ORIENTACAO_ENCERRADA'])

    def test_acoes_so_por_post(self):
        self.assertEqual(self.client.get(reverse('encerrar_orientacao', args=[self.minha.id])).status_code, 405)
        self.assertTrue(Orientacao.objects.filter(pk=self.minha.pk).exists())

    def test_admin_alcanca_qualquer_orientacao(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('renovar_orientacao', args=[self.dele.id]), {'valido_ate': dia(60)})
        self.dele.refresh_from_db()
        self.assertEqual(self.dele.valido_ate, dia(60))
        self.client.post(reverse('encerrar_orientacao', args=[self.minha.id]))
        self.assertFalse(Orientacao.objects.filter(pk=self.minha.pk).exists())


# --- Alunos: buscar, reivindicar, cadastrar, excluir --------------------------

class AlunosTests(Cenario):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.professor)

    def test_reivindicar_cria_a_orientacao_e_registra(self):
        self.client.post(reverse('reivindicar_aluno', args=[self.aluno.id]), {'valido_ate': dia(45)})
        orientacao = Orientacao.objects.get()
        self.assertEqual((orientacao.orientador, orientacao.aluno, orientacao.valido_ate), (self.professor, self.aluno, dia(45)))
        self.assertEqual(orientacao.concedido_por, self.professor)

        registro = RegistroAuditoria.objects.get()
        self.assertEqual((registro.acao, registro.autor_nome, registro.aluno_nome), ('ORIENTACAO_CRIADA', 'Maria Silva', 'Ana Souza'))

    def test_dois_professores_podem_orientar_o_mesmo_aluno(self):
        self.client.post(reverse('reivindicar_aluno', args=[self.aluno.id]), {'valido_ate': dia(45)})
        self.client.force_login(self.outro_professor)
        self.client.post(reverse('reivindicar_aluno', args=[self.aluno.id]), {'valido_ate': dia(20)})
        self.assertEqual(Orientacao.objects.filter(aluno=self.aluno).count(), 2)

    def test_reivindicar_de_novo_so_ajusta_a_validade(self):
        self.client.post(reverse('reivindicar_aluno', args=[self.aluno.id]), {'valido_ate': dia(45)})
        self.client.post(reverse('reivindicar_aluno', args=[self.aluno.id]), {'valido_ate': dia(60)})
        self.assertEqual(Orientacao.objects.get().valido_ate, dia(60))
        self.assertEqual(acoes_auditadas(), ['ORIENTACAO_CRIADA', 'ORIENTACAO_RENOVADA'])

    def test_ninguem_reivindica_a_si_mesmo(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('reivindicar_aluno', args=[self.admin.id]), {'valido_ate': dia(45)}, follow=True)
        self.assertFalse(Orientacao.objects.exists())
        self.assertContains(response, 'orientador de si mesmo')

    def test_so_alunos_podem_ser_reivindicados(self):
        response = self.client.post(reverse('reivindicar_aluno', args=[self.outro_professor.id]), {'valido_ate': dia(45)})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Orientacao.objects.exists())

    def test_busca(self):
        response = self.client.get(reverse('alunos'), {'q': 'beto'})
        self.assertEqual([linha['aluno'] for linha in response.context['linhas']], [self.aluno2])
        response = self.client.get(reverse('alunos'), {'q': '2020000001'})
        self.assertEqual([linha['aluno'] for linha in response.context['linhas']], [self.aluno])

    def test_cartao_dos_outros_nao_e_mostrado_ao_professor(self):
        dar_cartao(self.aluno, 'A1 B2 C3 D4')
        dar_cartao(self.aluno2, '11 22 33 44')
        orientar(self.professor, self.aluno)

        pagina = self.client.get(reverse('alunos'))
        self.assertContains(pagina, '•• •• C3 D4')      # meu bolsista: só o final
        self.assertNotContains(pagina, 'A1 B2 C3 D4')
        self.assertNotContains(pagina, '33 44')         # de outro: nada do UID

        self.client.force_login(self.admin)
        pagina = self.client.get(reverse('alunos'))
        self.assertContains(pagina, 'A1 B2 C3 D4')
        self.assertContains(pagina, '11 22 33 44')

    def test_cadastro_manual_de_aluno(self):
        self.client.post(reverse('alunos'), {
            'nome_completo': 'Carla Dias Pereira', 'email': '2020000009@ALUNO.restinga.ifrs.edu.br',
            'curso': 'ADS', 'valido_ate': dia(100),
        })
        novo = User.objects.get(email='2020000009@aluno.restinga.ifrs.edu.br')
        self.assertEqual((novo.first_name, novo.last_name), ('Carla', 'Dias Pereira'))
        self.assertFalse(novo.has_usable_password())
        self.assertTrue(novo.groups.filter(name='Alunos').exists())
        self.assertEqual(novo.aluno_profile.curso, 'ADS')
        self.assertIsNone(novo.aluno_profile.rfid_token)
        # quem cadastra vira o orientador
        orientacao = Orientacao.objects.get(aluno=novo)
        self.assertEqual((orientacao.orientador, orientacao.valido_ate), (self.professor, dia(100)))
        self.assertEqual(acoes_auditadas(), ['ALUNO_CADASTRADO', 'ORIENTACAO_CRIADA'])

    def test_cadastro_manual_recusa_gmail_e_email_repetido(self):
        total = User.objects.count()
        base = {'nome_completo': 'A B', 'curso': 'ADS', 'valido_ate': dia(30)}
        self.client.post(reverse('alunos'), {**base, 'email': 'a@gmail.com'})
        self.client.post(reverse('alunos'), {**base, 'email': self.aluno.email.upper()})
        self.assertEqual(User.objects.count(), total)
        self.assertFalse(Orientacao.objects.exists())

    def test_cadastro_manual_com_username_ja_usado(self):
        User.objects.create_user(username='2020000010', email='qualquer@restinga.ifrs.edu.br')
        response = self.client.post(reverse('alunos'), {
            'nome_completo': 'Davi Rocha', 'email': '2020000010@aluno.restinga.ifrs.edu.br',
            'curso': 'ADS', 'valido_ate': dia(30),
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.get(email='2020000010@aluno.restinga.ifrs.edu.br').username, '20200000102')

    def test_professor_nao_exclui_aluno(self):
        orientar(self.professor, self.aluno)
        response = self.client.post(reverse('delete_aluno', args=[self.aluno.id]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(pk=self.aluno.pk).exists())
        self.assertNotContains(self.client.get(reverse('alunos')), 'Excluir</button>')

    def test_admin_exclui_aluno_e_fica_registrado(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('delete_aluno', args=[self.aluno.id]))
        self.assertFalse(User.objects.filter(pk=self.aluno.pk).exists())
        registro = RegistroAuditoria.objects.get()
        self.assertEqual((registro.acao, registro.aluno_nome, registro.aluno), ('ALUNO_EXCLUIDO', 'Ana Souza', None))

    def test_admin_nao_exclui_administrador_nem_quem_nao_e_aluno(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('delete_aluno', args=[self.admin.id]))
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())
        self.assertEqual(self.client.post(reverse('delete_aluno', args=[self.professor.id])).status_code, 404)
        self.assertEqual(self.client.get(reverse('delete_aluno', args=[self.aluno.id])).status_code, 405)
        self.assertTrue(User.objects.filter(pk=self.aluno.pk).exists())


# --- Edição do cadastro -------------------------------------------------------

class EdicaoTests(Cenario):
    def setUp(self):
        super().setUp()
        orientar(self.professor, self.aluno)
        dar_cartao(self.aluno, 'A1 B2 C3 D4')
        self.client.force_login(self.professor)

    def editar(self, aluno, **campos):
        dados = {'first_name': aluno.first_name, 'last_name': aluno.last_name, 'curso': 'ADS', **campos}
        return self.client.post(reverse('edit_aluno', args=[aluno.id]), dados)

    def test_professor_edita_nome_e_curso_do_proprio_bolsista(self):
        self.assertEqual(self.editar(self.aluno, first_name='Ana Clara', curso='ELETRO').status_code, 302)
        self.aluno.refresh_from_db()
        self.assertEqual(self.aluno.first_name, 'Ana Clara')
        self.assertEqual(self.aluno.aluno_profile.curso, 'ELETRO')
        self.assertEqual(self.aluno.aluno_profile.rfid_token, 'A1 B2 C3 D4')  # cartão intacto
        registro = RegistroAuditoria.objects.get()
        self.assertEqual((registro.acao, registro.detalhe), ('ALUNO_EDITADO', 'alterou: nome, curso'))

    def test_professor_nao_troca_email_nem_digita_uid(self):
        # Mesmo forjando os campos no envio, eles são ignorados.
        self.editar(self.aluno, email='outro@aluno.restinga.ifrs.edu.br', rfid_token='99 99 99 99')
        self.aluno.refresh_from_db()
        self.assertEqual(self.aluno.email, '2020000001@aluno.restinga.ifrs.edu.br')
        self.assertEqual(self.aluno.aluno_profile.rfid_token, 'A1 B2 C3 D4')
        pagina = self.client.get(reverse('edit_aluno', args=[self.aluno.id]))
        self.assertNotContains(pagina, 'name="email"')
        self.assertNotContains(pagina, 'name="rfid_token"')

    def test_professor_remove_o_cartao_do_proprio_bolsista(self):
        self.editar(self.aluno, remover_cartao='on')
        self.assertIsNone(AlunoProfile.objects.get(user=self.aluno).rfid_token)
        registro = RegistroAuditoria.objects.get()
        self.assertEqual((registro.acao, registro.detalhe), ('CARTAO_REMOVIDO', 'cartão A1 B2 C3 D4'))

    def test_professor_nao_edita_bolsista_dos_outros(self):
        response = self.editar(self.aluno2, first_name='Hackeado')
        self.assertRedirects(response, reverse('alunos'))
        self.aluno2.refresh_from_db()
        self.assertEqual(self.aluno2.first_name, 'Beto')
        self.assertRedirects(self.client.get(reverse('edit_aluno', args=[self.aluno2.id])), reverse('alunos'))

    def test_ninguem_edita_quem_nao_e_aluno(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('edit_aluno', args=[self.outro_professor.id])).status_code, 404)
        self.assertEqual(self.editar(self.outro_professor, email='x@y.com', rfid_token='AA BB').status_code, 404)
        self.assertFalse(AlunoProfile.objects.filter(user=self.outro_professor).exists())

    def test_admin_troca_email_e_digita_o_uid(self):
        self.client.force_login(self.admin)
        cartao_pendente('11 22 33 44')
        self.editar(self.aluno2, email='novo@aluno.restinga.ifrs.edu.br', rfid_token='  11 22  33 44 ')
        self.aluno2.refresh_from_db()
        self.assertEqual(self.aluno2.email, 'novo@aluno.restinga.ifrs.edu.br')
        self.assertEqual(self.aluno2.aluno_profile.rfid_token, '11 22 33 44')
        self.assertEqual(acoes_auditadas(), ['ALUNO_EDITADO', 'CARTAO_VINCULADO'])
        self.assertFalse(UnassignedUIDLog.objects.exists())  # deixou de ser cartão desconhecido

    def test_admin_troca_e_remove_cartao(self):
        self.client.force_login(self.admin)
        self.editar(self.aluno, email=self.aluno.email, rfid_token='de ad be ef')
        self.assertEqual(AlunoProfile.objects.get(user=self.aluno).rfid_token, 'DE AD BE EF')
        self.editar(self.aluno, email=self.aluno.email, rfid_token='')
        self.assertIsNone(AlunoProfile.objects.get(user=self.aluno).rfid_token)
        self.assertEqual(acoes_auditadas(), ['CARTAO_TROCADO', 'CARTAO_REMOVIDO'])

    def test_uid_repetido_e_recusado(self):
        self.client.force_login(self.admin)
        response = self.editar(self.aluno2, email=self.aluno2.email, rfid_token='a1 b2 c3 d4')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'já está atribuído a outro aluno')

    def test_dois_alunos_sem_cartao_nao_quebra(self):
        # Antes o segundo aluno salvo sem cartão estourava erro 500 (UID '' duplicado).
        self.client.force_login(self.admin)
        self.assertEqual(self.editar(self.aluno, email=self.aluno.email, rfid_token='').status_code, 302)
        self.assertEqual(self.editar(self.aluno2, email=self.aluno2.email, rfid_token='').status_code, 302)
        self.assertIsNone(AlunoProfile.objects.get(user=self.aluno).rfid_token)
        self.assertIsNone(AlunoProfile.objects.get(user=self.aluno2).rfid_token)


# --- Cartões pendentes --------------------------------------------------------

@override_settings(CARTAO_PENDENTE_MINUTOS=10)
class CartoesPendentesTests(Cenario):
    def setUp(self):
        super().setUp()
        orientar(self.professor, self.aluno)
        self.client.force_login(self.professor)

    def vincular(self, cartao, aluno, **extra):
        return self.client.post(reverse('vincular_cartao'), {'cartao_id': cartao.id, 'aluno_id': aluno.id, **extra})

    def test_so_aparecem_os_cartoes_lidos_ha_pouco(self):
        recente = cartao_pendente('AA AA AA AA', minutos_atras=2)
        cartao_pendente('BB BB BB BB', minutos_atras=30)
        pagina = self.client.get(reverse('cartoes_pendentes'))
        self.assertEqual(list(pagina.context['pendentes']), [recente])
        self.assertEqual(pagina.context['cartoes_pendentes_qtd'], 1)
        self.assertContains(pagina, '<span class="contador">1</span>')
        self.assertNotContains(pagina, 'BB BB')

    def test_alunos_sem_cartao_separados_em_meus_e_outros(self):
        cartao_pendente('AA AA AA AA')
        dar_cartao(self.aluno2, '11 22 33 44')  # já tem cartão: não entra na lista
        pagina = self.client.get(reverse('cartoes_pendentes'))
        self.assertEqual(pagina.context['meus'], [self.aluno])
        self.assertEqual(pagina.context['outros'], [self.admin])

    def test_primeiro_passo_so_pede_confirmacao(self):
        cartao = cartao_pendente('AA AA AA AA')
        response = self.vincular(cartao, self.aluno)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Confirmar o cartão')
        self.assertContains(response, 'Ana Souza')
        self.assertFalse(AlunoProfile.objects.filter(rfid_token__isnull=False).exists())
        self.assertTrue(UnassignedUIDLog.objects.exists())

    def test_confirmar_vincula_ao_meu_bolsista(self):
        cartao = cartao_pendente('AA AA AA AA')
        response = self.vincular(cartao, self.aluno, confirmado='1')
        self.assertRedirects(response, reverse('cartoes_pendentes'))
        self.assertEqual(AlunoProfile.objects.get(user=self.aluno).rfid_token, 'AA AA AA AA')
        self.assertFalse(UnassignedUIDLog.objects.exists())
        self.assertEqual(Orientacao.objects.count(), 1)  # não criou outra
        registro = RegistroAuditoria.objects.get()
        self.assertEqual((registro.acao, registro.autor_nome), ('CARTAO_VINCULADO', 'Maria Silva'))
        self.assertIn('AA AA AA AA', registro.detalhe)
        self.assertTrue(situacao(self.aluno).liberado)

    def test_vincular_para_aluno_de_fora_torna_o_professor_orientador(self):
        cartao = cartao_pendente('AA AA AA AA')

        # sem a validade, volta para a confirmação e nada é gravado
        response = self.vincular(cartao, self.aluno2, confirmado='1')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(AlunoProfile.objects.filter(user=self.aluno2).exists())

        self.vincular(cartao, self.aluno2, confirmado='1', valido_ate=dia(40))
        self.assertEqual(AlunoProfile.objects.get(user=self.aluno2).rfid_token, 'AA AA AA AA')
        orientacao = Orientacao.objects.get(aluno=self.aluno2)
        self.assertEqual((orientacao.orientador, orientacao.valido_ate), (self.professor, dia(40)))
        self.assertEqual(acoes_auditadas(), ['CARTAO_VINCULADO', 'ORIENTACAO_CRIADA'])

    def test_cartao_lido_ha_muito_tempo_e_recusado(self):
        cartao = cartao_pendente('AA AA AA AA', minutos_atras=11)
        response = self.vincular(cartao, self.aluno, confirmado='1')
        self.assertRedirects(response, reverse('cartoes_pendentes'))
        self.assertIsNone(getattr(AlunoProfile.objects.filter(user=self.aluno).first(), 'rfid_token', None))

    def test_aluno_que_ja_tem_cartao_e_recusado(self):
        dar_cartao(self.aluno, 'A1 B2 C3 D4')
        cartao = cartao_pendente('AA AA AA AA')
        self.vincular(cartao, self.aluno, confirmado='1')
        self.assertEqual(AlunoProfile.objects.get(user=self.aluno).rfid_token, 'A1 B2 C3 D4')
        self.assertTrue(UnassignedUIDLog.objects.exists())

    def test_so_alunos_recebem_cartao(self):
        cartao = cartao_pendente('AA AA AA AA')
        self.vincular(cartao, self.outro_professor, confirmado='1')
        self.assertFalse(AlunoProfile.objects.filter(user=self.outro_professor).exists())

    def test_admin_vincula_sem_virar_orientador_a_menos_que_peca(self):
        self.client.force_login(self.admin)
        self.vincular(cartao_pendente('AA AA AA AA'), self.aluno2, confirmado='1')
        self.assertEqual(AlunoProfile.objects.get(user=self.aluno2).rfid_token, 'AA AA AA AA')
        self.assertFalse(Orientacao.objects.filter(aluno=self.aluno2).exists())

        AlunoProfile.objects.filter(user=self.aluno2).update(rfid_token=None)
        self.vincular(cartao_pendente('CC CC CC CC'), self.aluno2, confirmado='1', tornar_orientador='on', valido_ate=dia(15))
        self.assertEqual(Orientacao.objects.get(aluno=self.aluno2).orientador, self.admin)

    def test_admin_vincula_o_proprio_cartao_mas_nao_se_autoriza(self):
        self.client.force_login(self.admin)
        self.vincular(cartao_pendente('AA AA AA AA'), self.admin, confirmado='1', tornar_orientador='on', valido_ate=dia(15))
        self.assertEqual(AlunoProfile.objects.get(user=self.admin).rfid_token, 'AA AA AA AA')
        self.assertFalse(Orientacao.objects.filter(aluno=self.admin).exists())
        self.assertFalse(situacao(self.admin).liberado)


# --- Logs, orientadores e auditoria -------------------------------------------

class ConsultasTests(Cenario):
    def setUp(self):
        super().setUp()
        dar_cartao(self.aluno, 'A1 B2 C3 D4')
        AccessLog.objects.create(uid='A1 B2 C3 D4', status='GRANTED', user=self.aluno)
        AccessLog.objects.create(uid='A1 B2 C3 D4', status='DENIED', user=self.aluno)
        AccessLog.objects.create(uid='FF FF FF FF', status='DENIED', user=None)

    def test_logs_com_filtro_e_busca(self):
        self.client.force_login(self.professor)
        self.assertEqual(len(self.client.get(reverse('access_logs')).context['pagina']), 3)
        self.assertEqual(len(self.client.get(reverse('access_logs'), {'status': 'GRANTED'}).context['pagina']), 1)
        self.assertEqual(len(self.client.get(reverse('access_logs'), {'q': 'souza'}).context['pagina']), 2)
        self.assertEqual(len(self.client.get(reverse('access_logs'), {'q': 'ff ff', 'status': 'DENIED'}).context['pagina']), 1)
        self.assertEqual(len(self.client.get(reverse('access_logs'), {'status': 'invalido'}).context['pagina']), 3)

    def test_logs_sao_paginados(self):
        AccessLog.objects.bulk_create(AccessLog(uid='A1 B2 C3 D4', status='GRANTED', user=self.aluno) for _ in range(60))
        self.client.force_login(self.professor)
        primeira = self.client.get(reverse('access_logs'), {'status': 'GRANTED'})
        self.assertEqual(len(primeira.context['pagina']), 50)
        self.assertContains(primeira, '?status=GRANTED&amp;page=2')
        self.assertEqual(len(self.client.get(reverse('access_logs'), {'status': 'GRANTED', 'page': 2}).context['pagina']), 11)

    def test_uid_inteiro_nos_logs_so_para_o_admin(self):
        self.client.force_login(self.professor)
        pagina = self.client.get(reverse('access_logs'))
        self.assertNotContains(pagina, 'A1 B2 C3 D4')
        self.assertContains(pagina, '•• •• C3 D4')
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('access_logs')), 'A1 B2 C3 D4')

    def test_tela_de_orientadores(self):
        orientar(self.professor, self.aluno)
        orientar(self.outro_professor, self.aluno2, dias=-3)
        self.client.force_login(self.admin)
        pagina = self.client.get(reverse('listar_bolsistas_orientadores'))
        for texto in ('Maria Silva', 'Ana Souza', 'Em dia', 'João Pereira', 'Beto Lima', 'Vencida'):
            self.assertContains(pagina, texto)

    def test_auditoria_lista_e_busca(self):
        self.client.force_login(self.professor)
        self.client.post(reverse('reivindicar_aluno', args=[self.aluno.id]), {'valido_ate': dia(45)})
        self.client.post(reverse('reivindicar_aluno', args=[self.aluno2.id]), {'valido_ate': dia(45)})

        self.client.force_login(self.admin)
        pagina = self.client.get(reverse('auditoria'))
        self.assertEqual(len(pagina.context['pagina']), 2)
        self.assertContains(pagina, 'Acesso concedido')
        self.assertContains(pagina, 'Maria Silva')
        self.assertEqual(len(self.client.get(reverse('auditoria'), {'q': 'beto'}).context['pagina']), 1)

    def test_registro_de_auditoria_sobrevive_a_exclusao_das_contas(self):
        self.client.force_login(self.professor)
        self.client.post(reverse('reivindicar_aluno', args=[self.aluno.id]), {'valido_ate': dia(45)})
        self.aluno.delete()
        self.professor.delete()
        registro = RegistroAuditoria.objects.get()
        self.assertEqual((registro.autor, registro.aluno), (None, None))
        self.assertEqual((registro.autor_nome, registro.aluno_nome), ('Maria Silva', 'Ana Souza'))


# --- API usada pela porta (ESP32) ---------------------------------------------

class ApiPortaTests(Cenario):
    URL = '/api/v1/log-access/'

    def setUp(self):
        super().setUp()
        dispositivo = User.objects.create_user('esp32_device')
        self.api = APIClient()
        self.api.credentials(HTTP_AUTHORIZATION=f'Token {Token.objects.create(user=dispositivo).key}')
        dar_cartao(self.aluno, 'A1 B2 C3 D4')

    def passar_cartao(self, uid='A1 B2 C3 D4'):
        return self.api.post(self.URL, {'uid': uid}, format='json')

    def test_sem_token_nao_passa(self):
        self.assertEqual(APIClient().post(self.URL, {'uid': 'A1 B2 C3 D4'}, format='json').status_code, 401)
        self.assertEqual(APIClient().get('/api/v1/bolsistas/').status_code, 401)
        self.assertFalse(AccessLog.objects.exists())

    def test_bolsista_com_orientacao_em_dia_abre_a_porta(self):
        orientar(self.professor, self.aluno)
        response = self.passar_cartao()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'access granted', 'user': 'Ana Souza'})
        log = AccessLog.objects.get()
        self.assertEqual((log.status, log.user, log.uid), ('GRANTED', self.aluno, 'A1 B2 C3 D4'))

    def test_uid_em_minusculas_tambem_e_reconhecido(self):
        orientar(self.professor, self.aluno)
        self.assertEqual(self.passar_cartao('a1 b2  c3 d4').status_code, 200)

    def test_sem_orientador_e_negado(self):
        response = self.passar_cartao()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {'status': 'access denied', 'user': 'Ana Souza'})
        self.assertEqual(AccessLog.objects.get().status, 'DENIED')

    def test_orientacao_vencida_e_negada(self):
        orientar(self.professor, self.aluno, dias=-1)
        self.assertEqual(self.passar_cartao().status_code, 403)

    def test_orientador_desativado_e_negado(self):
        orientar(self.professor, self.aluno)
        User.objects.filter(pk=self.professor.pk).update(is_active=False)
        self.assertEqual(self.passar_cartao().status_code, 403)

    def test_retirar_o_acesso_vale_na_hora(self):
        orientacao = orientar(self.professor, self.aluno)
        self.assertEqual(self.passar_cartao().status_code, 200)
        orientacao.delete()
        self.assertEqual(self.passar_cartao().status_code, 403)

    def test_aluno_desativado_e_negado(self):
        orientar(self.professor, self.aluno)
        User.objects.filter(pk=self.aluno.pk).update(is_active=False)
        self.assertEqual(self.passar_cartao().status_code, 403)
        self.assertEqual(AccessLog.objects.get().status, 'DENIED')

    def test_cartao_desconhecido_e_negado_e_vira_pendente(self):
        response = self.passar_cartao('FF FF FF FF')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['user'], 'unknown')
        primeiro = UnassignedUIDLog.objects.get(uid='FF FF FF FF')

        self.passar_cartao('FF FF FF FF')
        segundo = UnassignedUIDLog.objects.get(uid='FF FF FF FF')
        self.assertEqual(UnassignedUIDLog.objects.count(), 1)
        self.assertEqual(segundo.first_seen, primeiro.first_seen)
        self.assertGreater(segundo.last_seen, primeiro.last_seen)
        self.assertEqual(AccessLog.objects.filter(status='DENIED').count(), 2)

    def test_do_leitor_ate_a_porta_abrir(self):
        """O fluxo inteiro: cartão novo -> pendente -> professor vincula -> porta abre."""
        self.assertEqual(self.passar_cartao('5B 6A 01 F3').status_code, 403)
        cartao = UnassignedUIDLog.objects.get()

        self.client.force_login(self.professor)
        self.client.post(reverse('vincular_cartao'), {
            'cartao_id': cartao.id, 'aluno_id': self.aluno2.id, 'confirmado': '1', 'valido_ate': dia(30),
        })

        self.assertEqual(self.passar_cartao('5B 6A 01 F3').json(), {'status': 'access granted', 'user': 'Beto Lima'})

    def test_uid_vazio_e_invalido(self):
        self.assertEqual(self.passar_cartao('').status_code, 400)
        self.assertEqual(self.api.post(self.URL, {}, format='json').status_code, 400)

    def test_lista_para_o_dispositivo_so_tem_quem_abre_a_porta(self):
        orientar(self.professor, self.aluno, dias=12)
        dar_cartao(self.aluno2, '11 22 33 44')          # tem cartão, mas não tem orientador
        orientar(self.professor, self.admin)            # tem orientador, mas não tem cartão

        response = self.api.get('/api/v1/bolsistas/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [
            {'nome_completo': 'Ana Souza', 'rfid_token': 'A1 B2 C3 D4', 'valido_ate': dia(12).isoformat()},
        ])


# --- Quem pode ser orientador -------------------------------------------------

class OrientadoresTests(Cenario):
    def setUp(self):
        super().setUp()
        self.servidor = criar_usuario('carlos@restinga.ifrs.edu.br', 'Servidores', first_name='Carlos', last_name='Dias')

    def assertBarrado(self, response):
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith('/?next='), response['Location'])

    def eh_orientador(self, user):
        return user.groups.filter(name='Professores').exists()

    def pedir(self, justificativa='Coordeno o projeto de robótica'):
        return self.client.post(reverse('solicitar_orientador'), {'justificativa': justificativa})

    def decidir(self, pedido, decisao):
        return self.client.post(reverse('decidir_pedido_orientador', args=[pedido.id]), {'decisao': decisao})

    # -- o servidor pede --

    def test_servidor_sem_papel_nao_gerencia_nada(self):
        self.client.force_login(self.servidor)
        for nome in PAGINAS_DE_GESTOR + PAGINAS_DE_ADMIN + ('indicar_orientador',):
            self.assertBarrado(self.client.get(reverse(nome)))
        self.assertBarrado(self.client.post(reverse('reivindicar_aluno', args=[self.aluno.id]), {'valido_ate': dia(10)}))
        self.assertFalse(Orientacao.objects.exists())

        home = self.client.get('/')
        self.assertContains(home, 'Solicitar papel de orientador')
        for texto in ('Meus bolsistas', 'Cartões pendentes', 'Meu acesso'):
            self.assertNotContains(home, texto)

    def test_servidor_pede_o_papel(self):
        self.client.force_login(self.servidor)
        self.pedir()

        pedido = PedidoOrientador.objects.get()
        self.assertEqual((pedido.email, pedido.usuario, pedido.origem, pedido.status),
                         ('carlos@restinga.ifrs.edu.br', self.servidor, 'PROPRIO', 'PENDENTE'))
        self.assertEqual(pedido.justificativa, 'Coordeno o projeto de robótica')
        self.assertFalse(self.eh_orientador(self.servidor))  # pedir não dá o papel
        self.assertEqual(acoes_auditadas(), ['ORIENTADOR_SOLICITADO'])

        home = self.client.get('/')
        self.assertContains(home, 'aguardando aprovação')
        self.assertNotContains(home, 'Solicitar papel de orientador</button>')

        self.pedir()  # pedir de novo não duplica
        self.assertEqual(PedidoOrientador.objects.count(), 1)

    def test_pedido_precisa_de_justificativa(self):
        self.client.force_login(self.servidor)
        self.pedir('')
        self.assertFalse(PedidoOrientador.objects.exists())

    def test_so_servidor_sem_papel_pode_pedir(self):
        for user in (self.aluno, self.professor, self.admin):
            self.client.force_login(user)
            self.pedir()
        self.assertFalse(PedidoOrientador.objects.exists())
        self.client.logout()
        self.assertIn('/accounts/login/', self.pedir()['Location'])

    # -- o administrador decide --

    def test_admin_aprova_e_o_servidor_vira_orientador(self):
        self.client.force_login(self.servidor)
        self.pedir()
        pedido = PedidoOrientador.objects.get()

        self.client.force_login(self.admin)
        pagina = self.client.get(reverse('listar_bolsistas_orientadores'))
        self.assertEqual(list(pagina.context['pendentes']), [pedido])
        self.assertContains(pagina, 'Coordeno o projeto de robótica')
        self.assertContains(pagina, '<span class="contador">1</span>')

        self.decidir(pedido, 'aprovar')

        pedido.refresh_from_db()
        self.assertEqual((pedido.status, pedido.decidido_por), ('APROVADO', self.admin))
        self.assertTrue(self.eh_orientador(self.servidor))
        self.assertEqual(acoes_auditadas(), ['ORIENTADOR_SOLICITADO', 'ORIENTADOR_APROVADO'])

        self.client.force_login(self.servidor)
        self.assertEqual(self.client.get(reverse('alunos')).status_code, 200)

    def test_admin_recusa_e_o_servidor_pode_pedir_de_novo(self):
        self.client.force_login(self.servidor)
        self.pedir()
        pedido = PedidoOrientador.objects.get()

        self.client.force_login(self.admin)
        self.decidir(pedido, 'recusar')
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, 'RECUSADO')
        self.assertFalse(self.eh_orientador(self.servidor))

        self.client.force_login(self.servidor)
        self.assertContains(self.client.get('/'), 'foi recusado')
        self.pedir('Agora com o número do edital')
        self.assertEqual(PedidoOrientador.objects.filter(status='PENDENTE').count(), 1)

    def test_so_o_admin_decide(self):
        self.client.force_login(self.servidor)
        self.pedir()
        pedido = PedidoOrientador.objects.get()

        for user in (self.servidor, self.professor, self.aluno):
            self.client.force_login(user)
            self.assertBarrado(self.decidir(pedido, 'aprovar'))
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, 'PENDENTE')
        self.assertFalse(self.eh_orientador(self.servidor))

    def test_pedido_ja_decidido_nao_e_decidido_de_novo(self):
        self.client.force_login(self.servidor)
        self.pedir()
        pedido = PedidoOrientador.objects.get()
        self.client.force_login(self.admin)
        self.decidir(pedido, 'recusar')
        self.assertEqual(self.decidir(pedido, 'aprovar').status_code, 404)
        self.assertFalse(self.eh_orientador(self.servidor))
        self.assertEqual(self.client.get(reverse('decidir_pedido_orientador', args=[pedido.id])).status_code, 405)

    # -- um orientador indica, o administrador autoriza direto --

    def indicar(self, email, justificativa='Vai orientar bolsistas do projeto X'):
        return self.client.post(reverse('indicar_orientador'), {'email': email, 'justificativa': justificativa})

    def test_orientador_indica_colega_mas_quem_decide_e_o_admin(self):
        self.client.force_login(self.professor)
        self.indicar('NOVA@restinga.ifrs.edu.br')

        pedido = PedidoOrientador.objects.get()
        self.assertEqual((pedido.email, pedido.usuario, pedido.origem, pedido.status, pedido.pedido_por),
                         ('nova@restinga.ifrs.edu.br', None, 'INDICACAO', 'PENDENTE', self.professor))
        registro = RegistroAuditoria.objects.get()
        self.assertEqual((registro.acao, registro.aluno, registro.aluno_nome),
                         ('ORIENTADOR_INDICADO', None, 'nova@restinga.ifrs.edu.br'))

        # a indicação de quem já tem conta não dá o papel sozinha
        self.indicar(self.servidor.email)
        self.assertFalse(self.eh_orientador(self.servidor))
        self.assertEqual(PedidoOrientador.objects.get(email=self.servidor.email).usuario, self.servidor)

    def test_indicacao_recusa_emails_invalidos(self):
        self.client.force_login(self.professor)
        self.indicar('alguem@gmail.com')                           # fora do campus
        self.indicar('2020000001@aluno.restinga.ifrs.edu.br')      # aluno
        self.indicar(self.outro_professor.email)                   # já é orientador
        self.assertFalse(PedidoOrientador.objects.exists())

        self.indicar('nova@restinga.ifrs.edu.br')
        self.indicar('nova@restinga.ifrs.edu.br')                  # já tem pedido aguardando
        self.assertEqual(PedidoOrientador.objects.count(), 1)

    def test_admin_autoriza_direto(self):
        self.client.force_login(self.admin)

        # quem já tem conta vira orientador na hora
        self.indicar(self.servidor.email)
        self.assertTrue(self.eh_orientador(self.servidor))

        # quem ainda não entrou fica aprovado, esperando o primeiro login
        self.indicar('nova@restinga.ifrs.edu.br')
        pedido = PedidoOrientador.objects.get(email='nova@restinga.ifrs.edu.br')
        self.assertEqual((pedido.status, pedido.origem, pedido.decidido_por), ('APROVADO', 'ADMIN', self.admin))
        pagina = self.client.get(reverse('listar_bolsistas_orientadores'))
        self.assertEqual(list(pagina.context['aprovados_sem_conta']), [pedido])
        self.assertEqual(acoes_auditadas(), ['ORIENTADOR_APROVADO', 'ORIENTADOR_APROVADO'])

    # -- retirar o papel --

    def test_retirar_mostra_quem_perde_o_acesso_antes_de_confirmar(self):
        dar_cartao(self.aluno, 'A1 B2 C3 D4')
        dar_cartao(self.aluno2, '11 22 33 44')
        orientar(self.professor, self.aluno)            # só tem este orientador
        orientar(self.professor, self.aluno2)
        orientar(self.outro_professor, self.aluno2)     # tem outro orientador em dia

        self.client.force_login(self.admin)
        pagina = self.client.get(reverse('retirar_orientador', args=[self.professor.id]))
        self.assertEqual(pagina.context['perdem'], [self.aluno])
        self.assertEqual(pagina.context['total'], 2)
        self.assertTrue(self.eh_orientador(self.professor))  # só olhar não retira

        self.client.post(reverse('retirar_orientador', args=[self.professor.id]))

        self.assertFalse(self.eh_orientador(self.professor))
        self.assertFalse(situacao(self.aluno).liberado)
        self.assertTrue(situacao(self.aluno2).liberado)
        self.assertEqual(Orientacao.objects.filter(orientador=self.professor).count(), 2)  # continuam registradas
        registro = RegistroAuditoria.objects.get()
        self.assertEqual((registro.acao, registro.detalhe),
                         ('ORIENTADOR_REVOGADO', '1 bolsista(s) ficaram sem orientador em dia'))

    def test_retirar_revoga_o_pedido_aprovado(self):
        self.client.force_login(self.admin)
        self.indicar(self.servidor.email)
        self.client.post(reverse('retirar_orientador', args=[self.servidor.id]))
        self.assertFalse(self.eh_orientador(self.servidor))
        self.assertEqual(PedidoOrientador.objects.get().status, 'REVOGADO')

    def test_quem_esta_na_lista_do_servidor_nao_e_retirado_pela_tela(self):
        self.client.force_login(self.admin)
        with override_settings(PROFESSOR_EMAILS=[self.professor.email]):
            pagina = self.client.get(reverse('listar_bolsistas_orientadores'))
            self.assertContains(pagina, 'Lista PROFESSOR_EMAILS do servidor')
            self.client.post(reverse('retirar_orientador', args=[self.professor.id]))
        self.assertTrue(self.eh_orientador(self.professor))

    def test_so_o_admin_retira_e_so_de_quem_e_orientador(self):
        self.client.force_login(self.outro_professor)
        self.assertBarrado(self.client.post(reverse('retirar_orientador', args=[self.professor.id])))
        self.assertTrue(self.eh_orientador(self.professor))

        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse('retirar_orientador', args=[self.aluno.id])).status_code, 404)


# --- Comando revogar_dominio --------------------------------------------------

class RevogarDominioTests(TestCase):
    def setUp(self):
        self.gmail = criar_usuario('antigo@gmail.com', 'Professores')
        self.aluno = criar_usuario('2020000001@aluno.restinga.ifrs.edu.br', 'Alunos')
        dar_cartao(self.aluno, 'A1 B2 C3 D4')
        orientar(self.gmail, self.aluno)
        self.superuser = criar_usuario('dono@gmail.com', is_superuser=True, is_staff=True)
        self.institucional = criar_usuario('prof@restinga.ifrs.edu.br', 'Professores')

    def rodar(self, *args):
        saida = StringIO()
        call_command('revogar_dominio', *args, stdout=saida)
        return saida.getvalue()

    def test_sem_aplicar_so_lista(self):
        saida = self.rodar('gmail.com')
        self.assertIn('antigo@gmail.com', saida)
        self.assertIn('orienta 1 bolsista(s)', saida)
        self.assertIn('PERDEM o acesso', saida)
        self.assertIn('Nada foi alterado', saida)
        self.gmail.refresh_from_db()
        self.assertTrue(self.gmail.is_active)
        self.assertTrue(situacao(self.aluno).liberado)

    def test_aplicar_desativa_a_conta_e_o_bolsista_perde_o_acesso(self):
        self.rodar('gmail.com', '--aplicar')
        self.gmail.refresh_from_db()
        self.assertFalse(self.gmail.is_active)
        self.assertFalse(self.gmail.groups.filter(name='Professores').exists())
        # a orientação continua registrada, mas não vale mais
        self.assertTrue(Orientacao.objects.filter(aluno=self.aluno).exists())
        self.assertFalse(situacao(self.aluno).liberado)
        # superusuário e contas institucionais não são tocados
        self.superuser.refresh_from_db()
        self.institucional.refresh_from_db()
        self.assertTrue(self.superuser.is_active and self.superuser.is_superuser)
        self.assertTrue(self.institucional.is_active)
        self.assertTrue(self.institucional.groups.filter(name='Professores').exists())


# --- Migração dos dados antigos -----------------------------------------------

class MigracaoDeBolsistasTests(TransactionTestCase):
    """Os vínculos professor -> bolsista do modelo antigo viram orientações com validade."""

    def migrar_para(self, nome):
        executor = MigrationExecutor(connection)
        executor.migrate([('core', nome)])
        return executor.loader.project_state([('core', nome)]).apps

    def test_bolsistas_antigos_viram_orientacoes(self):
        antes = self.migrar_para('0009_orientacao_e_auditoria')
        UserAntigo = antes.get_model('auth', 'User')
        ProfessorProfile = antes.get_model('core', 'ProfessorProfile')
        professor = UserAntigo.objects.create(username='professor')
        aluno = UserAntigo.objects.create(username='aluno')
        perfil = ProfessorProfile.objects.create(user=professor)
        perfil.bolsistas.add(aluno, professor)  # o segundo é um vínculo consigo mesmo

        depois = self.migrar_para('0011_remove_professorprofile')

        OrientacaoNova = depois.get_model('core', 'Orientacao')
        self.assertEqual(
            list(OrientacaoNova.objects.values_list('orientador__username', 'aluno__username')),
            [('professor', 'aluno')],  # o vínculo consigo mesmo não foi levado adiante
        )
        # ninguém perde o acesso no dia da atualização
        self.assertGreaterEqual(OrientacaoNova.objects.get().valido_ate, dia(179))
