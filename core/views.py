# core/views.py
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages  # Para exibir mensagens de sucesso/erro
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import Group
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .acesso import cartao_de, com_orientacoes, nome_de, registrar, situacao_do_aluno, validade_padrao
from .forms import CadastrarAlunoForm, EditAlunoForm, ValidadeForm
from .models import AccessLog, AlunoProfile, Orientacao, RegistroAuditoria, UnassignedUIDLog
from .permissions import eh_admin, pode_gerenciar, pode_gerenciar_aluno

User = get_user_model()

POR_PAGINA = 50


# --- Apoio -------------------------------------------------------------------

def gestor(view):
    """Só professores e administradores. Os demais voltam para a página inicial."""
    return login_required(user_passes_test(pode_gerenciar, login_url='/')(view))


def administrador(view):
    """Só administradores (superusuários)."""
    return login_required(user_passes_test(eh_admin, login_url='/')(view))


def alunos_queryset():
    """
    Usuários que as telas de gerenciamento podem tocar: apenas o grupo 'Alunos'.
    Sem esse filtro seria possível alterar professores e administradores só
    trocando o ID na URL.
    """
    return User.objects.filter(groups__name='Alunos')


def username_unico(base):
    """Gera um username livre a partir do prefixo do e-mail."""
    username = base
    sufixo = 1
    while User.objects.filter(username=username).exists():
        sufixo += 1
        username = f"{base}{sufixo}"
    return username


def voltar(request, padrao):
    """Volta para a tela de onde a ação partiu (campo oculto `next`), se for um endereço do próprio portal."""
    destino = request.POST.get('next', '')
    if destino and url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()}):
        return redirect(destino)
    return redirect(padrao)


def linhas_de_alunos(alunos, user):
    """Junta, para cada aluno, o que as listas mostram: cartão, situação e orientações."""
    linhas = []
    for aluno in alunos:
        orientacoes = list(aluno.orientacoes_recebidas.all())
        linhas.append({
            'aluno': aluno,
            'cartao': cartao_de(aluno),
            'situacao': situacao_do_aluno(aluno),
            'orientacoes': orientacoes,
            'minha': next((o for o in orientacoes if o.orientador_id == user.id), None),
        })
    return linhas


def conceder_acesso(orientador, aluno, valido_ate, autor):
    """Cria a orientação ou, se já existe, ajusta a validade. Sempre deixa registro."""
    orientacao, criada = Orientacao.objects.get_or_create(
        orientador=orientador, aluno=aluno,
        defaults={'valido_ate': valido_ate, 'concedido_por': autor},
    )
    if criada:
        registrar(autor, 'ORIENTACAO_CRIADA', aluno,
                  f"orientador: {nome_de(orientador)}; válido até {valido_ate:%d/%m/%Y}")
    elif orientacao.valido_ate != valido_ate:
        anterior = orientacao.valido_ate
        orientacao.valido_ate = valido_ate
        orientacao.save(update_fields=['valido_ate'])
        registrar(autor, 'ORIENTACAO_RENOVADA', aluno,
                  f"orientador: {nome_de(orientador)}; de {anterior:%d/%m/%Y} para {valido_ate:%d/%m/%Y}")
    return orientacao, criada


def limite_cartoes_pendentes():
    return timezone.now() - timedelta(minutes=settings.CARTAO_PENDENTE_MINUTOS)


def erros_do_form(form):
    return ' '.join(erro for erros in form.errors.values() for erro in erros)


# --- Página inicial ----------------------------------------------------------

def home(request):
    contexto = {}
    user = request.user

    if user.is_authenticated:
        # Quem é aluno vê a própria situação: cartão, orientadores e últimos acessos.
        if user.groups.filter(name='Alunos').exists():
            aluno = com_orientacoes(User.objects.filter(pk=user.pk)).get()
            contexto.update({
                'sou_aluno': True,
                'meu_acesso': situacao_do_aluno(aluno),
                'meu_cartao': cartao_de(aluno),
                'minhas_orientacoes': list(aluno.orientacoes_recebidas.all()),
                'meus_acessos': AccessLog.objects.filter(user=user).order_by('-timestamp')[:20],
            })

        # Quem gerencia vê um resumo do que precisa de atenção.
        if pode_gerenciar(user):
            hoje = timezone.localdate()
            minhas = Orientacao.objects.filter(orientador=user)
            contexto['resumo'] = {
                'bolsistas': minhas.filter(valido_ate__gte=hoje).count(),
                'vencendo': minhas.filter(valido_ate__gte=hoje, valido_ate__lte=hoje + timedelta(days=30)).count(),
                'vencidas': minhas.filter(valido_ate__lt=hoje).count(),
                'acessos_hoje': AccessLog.objects.filter(timestamp__date=hoje, status='GRANTED').count(),
            }

    return render(request, 'home.html', contexto)


# --- Bolsistas e alunos ------------------------------------------------------

@gestor
def gerenciar_bolsistas(request):
    """Meus bolsistas: só os alunos que o usuário orienta (inclusive os vencidos, para renovar)."""
    alunos = com_orientacoes(
        alunos_queryset()
        .filter(orientacoes_recebidas__orientador=request.user)
        .distinct()
        .order_by('first_name', 'last_name')
    )
    context = {
        'linhas': linhas_de_alunos(alunos, request.user),
        'validade_padrao': validade_padrao(),
    }
    return render(request, 'gerenciamento/meus_bolsistas.html', context)


@gestor
def alunos(request):
    """Todos os alunos: buscar, reivindicar e pré-cadastrar quem ainda não entrou no portal."""
    form = CadastrarAlunoForm()

    if request.method == 'POST':
        form = CadastrarAlunoForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            nome_completo = form.cleaned_data['nome_completo']

            # Separa o nome completo em primeiro e último nome
            parts = nome_completo.split(' ', 1)

            with transaction.atomic():
                # create_user sem senha já deixa a senha inutilizável:
                # o aluno entra somente pelo Google.
                novo = User.objects.create_user(
                    username=username_unico(email.split('@')[0]),
                    email=email,
                    first_name=parts[0],
                    last_name=parts[1] if len(parts) > 1 else '',
                )
                grupo, _ = Group.objects.get_or_create(name='Alunos')
                novo.groups.add(grupo)
                AlunoProfile.objects.create(user=novo, curso=form.cleaned_data['curso'])
                registrar(request.user, 'ALUNO_CADASTRADO', novo, f"e-mail: {email}")
                # Quem cadastra se responsabiliza pelo aluno.
                conceder_acesso(request.user, novo, form.cleaned_data['valido_ate'], request.user)

            messages.success(
                request,
                f"{nome_completo} foi cadastrado como seu bolsista. "
                "Para liberar a porta, falta vincular o cartão em \"Cartões pendentes\"."
            )
            return redirect('alunos')
        messages.error(request, "Não foi possível cadastrar. Confira os campos do formulário.")

    busca = request.GET.get('q', '').strip()
    lista = alunos_queryset()
    for termo in busca.split():
        lista = lista.filter(
            Q(first_name__icontains=termo) | Q(last_name__icontains=termo) | Q(email__icontains=termo)
        )
    lista = com_orientacoes(lista.distinct().order_by('first_name', 'last_name'))

    context = {
        'form': form,
        'busca': busca,
        'linhas': linhas_de_alunos(lista, request.user),
        'validade_padrao': validade_padrao(),
    }
    return render(request, 'gerenciamento/alunos.html', context)


@gestor
@require_POST
def reivindicar_aluno(request, student_id):
    """O usuário passa a ser orientador do aluno (ou ajusta a validade, se já for)."""
    aluno = get_object_or_404(alunos_queryset(), id=student_id)

    if aluno.pk == request.user.pk:
        messages.error(request, "Ninguém pode ser orientador de si mesmo. Peça a um professor para conceder o seu acesso.")
        return voltar(request, 'alunos')

    form = ValidadeForm(request.POST)
    if not form.is_valid():
        messages.error(request, erros_do_form(form))
        return voltar(request, 'alunos')

    _, criada = conceder_acesso(request.user, aluno, form.cleaned_data['valido_ate'], request.user)
    if criada:
        messages.success(request, f"{nome_de(aluno)} agora é seu bolsista, com acesso até {form.cleaned_data['valido_ate']:%d/%m/%Y}.")
    else:
        messages.success(request, f"Validade de {nome_de(aluno)} ajustada para {form.cleaned_data['valido_ate']:%d/%m/%Y}.")
    return voltar(request, 'alunos')


def orientacao_do_usuario(request, orientacao_id):
    """Um professor só alcança as próprias orientações; o administrador alcança todas."""
    orientacoes = Orientacao.objects.select_related('orientador', 'aluno')
    if not eh_admin(request.user):
        orientacoes = orientacoes.filter(orientador=request.user)
    return get_object_or_404(orientacoes, id=orientacao_id)


@gestor
@require_POST
def renovar_orientacao(request, orientacao_id):
    orientacao = orientacao_do_usuario(request, orientacao_id)

    form = ValidadeForm(request.POST)
    if not form.is_valid():
        messages.error(request, erros_do_form(form))
        return voltar(request, 'gerenciar_bolsistas')

    conceder_acesso(orientacao.orientador, orientacao.aluno, form.cleaned_data['valido_ate'], request.user)
    messages.success(request, f"Acesso de {nome_de(orientacao.aluno)} válido até {form.cleaned_data['valido_ate']:%d/%m/%Y}.")
    return voltar(request, 'gerenciar_bolsistas')


@gestor
@require_POST
def encerrar_orientacao(request, orientacao_id):
    orientacao = orientacao_do_usuario(request, orientacao_id)
    aluno = orientacao.aluno

    registrar(request.user, 'ORIENTACAO_ENCERRADA', aluno, f"orientador: {nome_de(orientacao.orientador)}")
    orientacao.delete()

    messages.success(request, f"{nome_de(aluno)} não é mais bolsista de {nome_de(orientacao.orientador)}.")
    return voltar(request, 'gerenciar_bolsistas')


@gestor
def edit_aluno(request, student_id):
    aluno = get_object_or_404(alunos_queryset().select_related('aluno_profile'), id=student_id)

    if not pode_gerenciar_aluno(request.user, aluno):
        messages.error(request, "Você só pode editar os seus próprios bolsistas.")
        return redirect('alunos')

    admin = eh_admin(request.user)
    cartao_atual = cartao_de(aluno)

    if request.method == 'POST':
        form = EditAlunoForm(request.POST, instance=aluno, admin=admin)
        if form.is_valid():
            cd = form.cleaned_data

            with transaction.atomic():
                alterados = []
                for campo, rotulo in (('first_name', 'nome'), ('last_name', 'sobrenome'), ('email', 'e-mail')):
                    if campo in cd and getattr(aluno, campo) != cd[campo]:
                        setattr(aluno, campo, cd[campo])
                        alterados.append(rotulo)
                aluno.save()

                # Atualiza ou cria o AlunoProfile
                profile, _ = AlunoProfile.objects.get_or_create(user=aluno, defaults={'curso': cd['curso']})
                if profile.curso != cd['curso']:
                    profile.curso = cd['curso']
                    alterados.append('curso')

                if admin:
                    novo_cartao = cd['rfid_token']
                elif cd.get('remover_cartao'):
                    novo_cartao = None
                else:
                    novo_cartao = cartao_atual
                profile.rfid_token = novo_cartao
                profile.save()

                if alterados:
                    registrar(request.user, 'ALUNO_EDITADO', aluno, "alterou: " + ", ".join(alterados))
                if novo_cartao != cartao_atual:
                    if not novo_cartao:
                        registrar(request.user, 'CARTAO_REMOVIDO', aluno, f"cartão {cartao_atual}")
                    elif not cartao_atual:
                        registrar(request.user, 'CARTAO_VINCULADO', aluno, f"cartão {novo_cartao}, digitado pelo administrador")
                    else:
                        registrar(request.user, 'CARTAO_TROCADO', aluno, f"de {cartao_atual} para {novo_cartao}")
                    if novo_cartao:
                        # O cartão deixou de ser "desconhecido".
                        UnassignedUIDLog.objects.filter(uid=novo_cartao).delete()

            messages.success(request, f"Dados de {nome_de(aluno)} atualizados com sucesso!")
            return redirect('alunos' if admin else 'gerenciar_bolsistas')
    else:
        profile = getattr(aluno, 'aluno_profile', None)  # Busca segura do perfil
        form = EditAlunoForm(
            initial={
                'first_name': aluno.first_name,
                'last_name': aluno.last_name,
                'email': aluno.email,
                'curso': profile.curso if profile else 'INDEFINIDO',
                'rfid_token': cartao_atual or '',
            },
            instance=aluno,
            admin=admin,
        )

    context = {
        'form': form,
        'aluno': aluno,
        'cartao': cartao_atual,
    }
    return render(request, 'gerenciamento/edit_aluno.html', context)


@administrador
@require_POST
def delete_aluno(request, student_id):
    """Excluir um aluno apaga o cadastro inteiro; por isso é só do administrador."""
    aluno = get_object_or_404(alunos_queryset(), id=student_id)
    aluno_nome = nome_de(aluno)

    # Contas de administrador não podem ser apagadas por esta tela.
    if aluno.is_superuser or aluno.is_staff:
        messages.error(request, f"{aluno_nome} é administrador e não pode ser excluído por aqui.")
    else:
        registrar(request.user, 'ALUNO_EXCLUIDO', aluno, f"e-mail: {aluno.email}")
        aluno.delete()
        messages.success(request, f"O aluno {aluno_nome} foi excluído permanentemente.")

    return redirect('alunos')


# --- Cartões pendentes -------------------------------------------------------

@gestor
def cartoes_pendentes(request):
    """Cartões desconhecidos lidos há pouco no leitor, prontos para vincular a um aluno."""
    pendentes = UnassignedUIDLog.objects.filter(last_seen__gte=limite_cartoes_pendentes()).order_by('-last_seen')

    sem_cartao = com_orientacoes(
        alunos_queryset()
        .filter(Q(aluno_profile__isnull=True) | Q(aluno_profile__rfid_token__isnull=True))
        .distinct()
        .order_by('first_name', 'last_name')
    )
    linhas = linhas_de_alunos(sem_cartao, request.user)

    context = {
        'pendentes': pendentes,
        'meus': [linha['aluno'] for linha in linhas if linha['minha']],
        'outros': [linha['aluno'] for linha in linhas if not linha['minha']],
        'minutos': settings.CARTAO_PENDENTE_MINUTOS,
    }
    return render(request, 'gerenciamento/cartoes_pendentes.html', context)


@gestor
@require_POST
def vincular_cartao(request):
    """
    Vincula um cartão recém-lido a um aluno, em dois passos: primeiro mostra a
    confirmação (cartão, horário da leitura e dados do aluno); só o segundo
    envio, com `confirmado=1`, grava.
    """
    minutos = settings.CARTAO_PENDENTE_MINUTOS
    cartao = UnassignedUIDLog.objects.filter(
        pk=request.POST.get('cartao_id') or 0, last_seen__gte=limite_cartoes_pendentes()
    ).first()
    if cartao is None:
        messages.error(
            request,
            f"Esse cartão não foi lido nos últimos {minutos} minutos. "
            "Peça para o aluno aproximar o cartão do leitor outra vez."
        )
        return redirect('cartoes_pendentes')

    aluno = alunos_queryset().select_related('aluno_profile').filter(pk=request.POST.get('aluno_id') or 0).first()
    if aluno is None:
        messages.error(request, "Escolha o aluno que vai receber o cartão.")
        return redirect('cartoes_pendentes')

    if cartao_de(aluno):
        messages.error(request, f"{nome_de(aluno)} já tem um cartão. Remova o cartão atual antes de vincular outro.")
        return redirect('cartoes_pendentes')

    admin = eh_admin(request.user)
    proprio = aluno.pk == request.user.pk
    ja_oriento = Orientacao.objects.filter(orientador=request.user, aluno=aluno).exists()
    # Um professor só mexe nos próprios bolsistas: ao vincular o cartão de um
    # aluno que ainda não orienta, ele passa a orientá-lo. Para o administrador
    # isso é opcional (ele pode estar só cadastrando o cartão para outro professor).
    deve_orientar = not admin and not ja_oriento
    pode_orientar = admin and not ja_oriento and not proprio

    confirmando = request.POST.get('confirmado') == '1'
    vai_orientar = deve_orientar or (pode_orientar and request.POST.get('tornar_orientador') == 'on')
    form = ValidadeForm(request.POST if confirmando and vai_orientar else None)

    if not confirmando or (vai_orientar and not form.is_valid()):
        return render(request, 'gerenciamento/confirmar_vinculo.html', {
            'cartao': cartao,
            'aluno': aluno,
            'form': form,
            'deve_orientar': deve_orientar,
            'pode_orientar': pode_orientar,
            'proprio': proprio,
        })

    with transaction.atomic():
        profile, _ = AlunoProfile.objects.get_or_create(user=aluno, defaults={'curso': 'INDEFINIDO'})
        profile.rfid_token = cartao.uid
        profile.save(update_fields=['rfid_token'])
        registrar(request.user, 'CARTAO_VINCULADO', aluno,
                  f"cartão {cartao.uid}, lido no leitor às {timezone.localtime(cartao.last_seen):%H:%M}")
        if vai_orientar:
            conceder_acesso(request.user, aluno, form.cleaned_data['valido_ate'], request.user)
        cartao.delete()

    messages.success(request, f"Cartão vinculado a {nome_de(aluno)}.")
    return redirect('cartoes_pendentes')


# --- Consultas ---------------------------------------------------------------

@gestor
def access_logs_view(request):
    """
    Tentativas de acesso registradas pelo leitor, das mais recentes às mais
    antigas. Só para quem gerencia: cada aluno vê os próprios acessos na
    página inicial.
    """
    # .select_related('user') otimiza a consulta, buscando os dados do usuário
    # relacionado na mesma query para evitar múltiplas chamadas ao banco.
    logs = AccessLog.objects.select_related('user').order_by('-timestamp')

    status = request.GET.get('status', '')
    if status in ('GRANTED', 'DENIED'):
        logs = logs.filter(status=status)

    busca = request.GET.get('q', '').strip()
    for termo in busca.split():
        logs = logs.filter(
            Q(uid__icontains=termo) | Q(user__first_name__icontains=termo) | Q(user__last_name__icontains=termo)
        )

    context = {
        'pagina': Paginator(logs, POR_PAGINA).get_page(request.GET.get('page')),
        'status': status,
        'busca': busca,
        'filtros': urlencode({k: v for k, v in (('status', status), ('q', busca)) if v}),
    }
    return render(request, 'gerenciamento/access_logs.html', context)


@administrador
def listar_bolsistas_orientadores(request):
    """Orientadores: quem responde por quem, e até quando."""
    orientacoes = (
        Orientacao.objects
        .select_related('orientador', 'aluno')
        .prefetch_related('orientador__groups')
        .order_by('orientador__first_name', 'orientador__last_name', 'orientador_id',
                  'aluno__first_name', 'aluno__last_name')
    )
    context = {
        'orientacoes': orientacoes,
        'validade_padrao': validade_padrao(),
    }
    return render(request, 'gerenciamento/orientadores.html', context)


@administrador
def auditoria(request):
    """Quem concedeu, renovou ou retirou acesso, e quem mexeu em cartões e cadastros."""
    registros = RegistroAuditoria.objects.all()

    busca = request.GET.get('q', '').strip()
    for termo in busca.split():
        registros = registros.filter(
            Q(autor_nome__icontains=termo) | Q(aluno_nome__icontains=termo) | Q(detalhe__icontains=termo)
        )

    context = {
        'pagina': Paginator(registros, POR_PAGINA).get_page(request.GET.get('page')),
        'busca': busca,
        'filtros': urlencode({'q': busca}) if busca else '',
    }
    return render(request, 'gerenciamento/auditoria.html', context)
