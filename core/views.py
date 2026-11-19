# core/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import Group
from django.contrib.auth import get_user_model
from django.contrib import messages # Para exibir mensagens de sucesso/erro
from django.db import transaction
from .models import ProfessorProfile, AlunoProfile, AccessLog
from .forms import AddBolsistaForm, EditAlunoForm
from .permissions import pode_gerenciar

User = get_user_model()


def home(request):
    return render(request, 'home.html')


def alunos_queryset():
    """
    Usuários que as telas de gerenciamento podem tocar: apenas o grupo 'Alunos'.
    Sem esse filtro, um professor conseguia editar ou excluir qualquer usuário
    (outros professores e administradores) só trocando o ID na URL.
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


@login_required
@user_passes_test(pode_gerenciar, login_url='/')
def gerenciar_bolsistas(request):
    professor_profile, created = ProfessorProfile.objects.get_or_create(user=request.user)
    add_form = AddBolsistaForm()

    # --- Lógica para ADICIONAR/REMOVER um aluno da orientação do professor ---
    if request.method == 'POST' and 'action' in request.POST:
        student_id = request.POST.get('student_id')
        action = request.POST.get('action')
        aluno = get_object_or_404(alunos_queryset(), id=student_id)
        if action == 'add':
            # Verifica se o aluno já tem um perfil. O hasattr() faz isso
            # de forma segura, sem levantar um erro se o perfil não existir.
            if not hasattr(aluno, 'aluno_profile'):
                # Se não tiver, cria um perfil com o curso padrão. O UID do
                # cartão RFID é preenchido depois, na tela de edição.
                AlunoProfile.objects.create(user=aluno, curso='INDEFINIDO')
                messages.info(request, f"Perfil criado para {aluno.get_full_name()}. Cadastre o cartão RFID em \"Editar\".")

            professor_profile.bolsistas.add(aluno)

        elif action == 'remove':
            professor_profile.bolsistas.remove(aluno)

        return redirect('gerenciar_bolsistas')

    # --- Lógica para CADASTRAR um aluno manualmente ---
    if request.method == 'POST':
        add_form = AddBolsistaForm(request.POST)
        if add_form.is_valid():
            email = add_form.cleaned_data['email']
            nome_completo = add_form.cleaned_data['nome_completo']
            curso = add_form.cleaned_data['curso']

            # Separa o nome completo em primeiro e último nome
            parts = nome_completo.split(' ', 1)

            with transaction.atomic():
                # create_user sem senha já deixa a senha inutilizável:
                # o aluno entra somente pelo Google.
                new_student = User.objects.create_user(
                    username=username_unico(email.split('@')[0]),
                    email=email,
                    first_name=parts[0],
                    last_name=parts[1] if len(parts) > 1 else '',
                )
                student_group, created = Group.objects.get_or_create(name='Alunos')
                new_student.groups.add(student_group)
                AlunoProfile.objects.create(user=new_student, curso=curso)
                professor_profile.bolsistas.add(new_student)

            messages.success(request, f"Aluno {nome_completo} adicionado com sucesso!")
            return redirect('gerenciar_bolsistas')
        else:
            messages.error(request, "Erro ao adicionar aluno. Verifique os dados informados.")

    bolsistas_gerenciados = professor_profile.bolsistas.all()
    todos_os_alunos = alunos_queryset().select_related('aluno_profile')

    context = {
        'add_form': add_form,
        'todos_os_alunos': todos_os_alunos,
        'bolsistas_gerenciados': bolsistas_gerenciados,
    }
    return render(request, 'gerenciamento/gerenciar_bolsistas.html', context)



@login_required
@user_passes_test(pode_gerenciar, login_url='/')
def listar_bolsistas_orientadores(request):
    # A lógica principal é buscar todos os perfis de professores.
    # O '.prefetch_related()' é uma otimização de performance. Ele busca todos os
    # bolsistas relacionados de uma só vez, evitando múltiplas consultas ao banco de dados.
    todos_os_perfis = ProfessorProfile.objects.select_related('user').prefetch_related('bolsistas')

    # Alunos que ainda não têm nenhum orientador.
    alunos_sem_orientador = alunos_queryset().filter(orientadores__isnull=True)

    context = {
        'todos_os_perfis': todos_os_perfis,
        'alunos_sem_orientador': alunos_sem_orientador,
    }

    return render(request, 'gerenciamento/listar_bolsistas.html', context)

@login_required
@user_passes_test(pode_gerenciar, login_url='/')
def delete_aluno(request, student_id):
    # Esta view só age no método POST
    if request.method == 'POST':
        aluno = get_object_or_404(alunos_queryset(), id=student_id)
        aluno_nome = aluno.get_full_name() or aluno.username

        # Contas de administrador não podem ser apagadas por esta tela.
        if aluno.is_superuser or aluno.is_staff:
            messages.error(request, f"{aluno_nome} é administrador e não pode ser excluído por aqui.")
        else:
            aluno.delete()
            messages.success(request, f"O aluno {aluno_nome} foi excluído permanentemente.")

    # Redireciona de volta para a página de gerenciamento em qualquer caso
    return redirect('gerenciar_bolsistas')


@login_required
@user_passes_test(pode_gerenciar, login_url='/')
def edit_aluno(request, student_id):
    aluno = get_object_or_404(alunos_queryset(), id=student_id)

    if request.method == 'POST':
        form = EditAlunoForm(request.POST, instance=aluno)
        if form.is_valid():
            cd = form.cleaned_data

            with transaction.atomic():
                # Atualiza os dados do User
                aluno.first_name = cd['first_name']
                aluno.last_name = cd['last_name']
                aluno.email = cd['email']
                aluno.save()

                # Atualiza ou cria o AlunoProfile
                profile, created = AlunoProfile.objects.get_or_create(
                    user=aluno, defaults={'curso': cd['curso']}
                )
                profile.curso = cd['curso']
                # None (e não '') quando o aluno ainda não tem cartão: o campo é
                # único, então dois alunos com '' quebrariam com erro 500.
                profile.rfid_token = cd['rfid_token'] or None
                profile.save()

            messages.success(request, f"Dados de {aluno.get_full_name()} atualizados com sucesso!")
            return redirect('gerenciar_bolsistas')
    else:
        # Preenche o formulário com os dados iniciais
        profile = getattr(aluno, 'aluno_profile', None) # Busca segura do perfil
        initial_data = {
            'first_name': aluno.first_name,
            'last_name': aluno.last_name,
            'email': aluno.email,
            'curso': profile.curso if profile else 'INDEFINIDO',
            'rfid_token': (profile.rfid_token or '') if profile else '',
        }
        form = EditAlunoForm(initial=initial_data, instance=aluno)

    context = {
        'form': form,
        'aluno': aluno
    }
    return render(request, 'gerenciamento/edit_aluno.html', context)

@login_required
def access_logs_view(request):
    """
    Busca e exibe todos os logs de acesso, dos mais recentes aos mais antigos.
    """
    # .select_related('user') otimiza a consulta, buscando os dados do usuário
    # relacionado na mesma query para evitar múltiplas chamadas ao banco.
    logs = AccessLog.objects.select_related('user').order_by('-timestamp')

    context = {
        'logs': logs
    }
    return render(request, 'gerenciamento/access_logs.html', context)
