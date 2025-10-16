# core/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User, Group # Adicione Group aqui
from django.contrib.auth import get_user_model
from django.http import HttpResponseForbidden
from django.contrib import messages # Para exibir mensagens de sucesso/erro
from .models import ProfessorProfile, AlunoProfile, AccessLog
from .forms import AddBolsistaForm, EditAlunoForm

def home(request):
    return render(request, 'home.html')

#def custom_login(request):
#    return render(request, 'login.html')
# Função auxiliar para verificar se o usuário está no grupo 'Professores'
def is_professor(user):
    return user.groups.filter(name='Professores').exists()

@login_required
@user_passes_test(is_professor, login_url='/')
def gerenciar_bolsistas(request):
    # ---- CORREÇÃO AQUI ----
    User = get_user_model() # Mova esta linha para o topo da função
    # -----------------------

    professor_profile, created = ProfessorProfile.objects.get_or_create(user=request.user)
    add_form = AddBolsistaForm() # <-- 2. Instancie o formulário

    # --- Lógica para ADICIONAR/REMOVER (que você já tem) ---
    if 'action' in request.POST:
        student_id = request.POST.get('student_id')
        action = request.POST.get('action')
        aluno = get_object_or_404(User, id=student_id)
        if action == 'add':
            # --- LÓGICA PRINCIPAL DA MUDANÇA ---
            # Verifica se o aluno já tem um perfil. O hasattr() faz isso
            # de forma segura, sem levantar um erro se o perfil não existir.
            if not hasattr(aluno, 'aluno_profile'):
                # Se não tiver, cria um perfil para ele com o curso padrão.
                # O token RFID será gerado automaticamente pelo `default=uuid.uuid4` no modelo.
                AlunoProfile.objects.create(user=aluno, curso='INDEFINIDO')
                messages.info(request, f"Perfil e token RFID foram criados para {aluno.get_full_name()}.")
            # --- FIM DA LÓGICA ---

            professor_profile.bolsistas.add(aluno)

        elif action == 'remove':
            professor_profile.bolsistas.remove(aluno)

        return redirect('gerenciar_bolsistas')

 
    if request.method == 'POST' and 'action' not in request.POST:
        add_form = AddBolsistaForm(request.POST)
        if add_form.is_valid():
            email = add_form.cleaned_data['email']
            nome_completo = add_form.cleaned_data['nome_completo']
            curso = add_form.cleaned_data['curso']

            username = email.split('@')[0]
            new_student = User.objects.create_user(username=username, email=email)
            new_student.set_unusable_password()

            # Separa o nome completo em primeiro e último nome
            parts = nome_completo.split(' ', 1)
            new_student.first_name = parts[0]
            new_student.last_name = parts[1] if len(parts) > 1 else ''

            student_group, created = Group.objects.get_or_create(name='Alunos')
            new_student.groups.add(student_group)
            new_student.save()

            # --- NOVO TRECHO: Cria o perfil do aluno ---
            AlunoProfile.objects.create(user=new_student, curso=curso)
            # --- FIM DO NOVO TRECHO ---

            professor_profile.bolsistas.add(new_student)
            messages.success(request, f"Aluno {nome_completo} adicionado com sucesso!")
            return redirect('gerenciar_bolsistas')
        else:
            messages.error(request, "Erro ao adicionar aluno. Verifique os dados informados.")

    bolsistas_gerenciados = professor_profile.bolsistas.all()
    todos_os_alunos = User.objects.filter(groups__name='Alunos').select_related('aluno_profile')

    context = {
        'add_form': add_form,
        'todos_os_alunos': todos_os_alunos,
        'bolsistas_gerenciados': bolsistas_gerenciados,
    }
    return render(request, 'gerenciamento/gerenciar_bolsistas.html', context)



@login_required
@user_passes_test(is_professor, login_url='/')
def listar_bolsistas_orientadores(request):
    # A lógica principal é buscar todos os perfis de professores.
    # O '.prefetch_related()' é uma otimização de performance. Ele busca todos os
    # bolsistas relacionados de uma só vez, evitando múltiplas consultas ao banco de dados.
    todos_os_perfis = ProfessorProfile.objects.prefetch_related('bolsistas').all()

    # Também vamos pegar a lista de todos os alunos para identificar aqueles que
    # ainda não têm um orientador.
    todos_os_alunos = User.objects.filter(groups__name='Alunos')

    # Vamos criar uma lista simples de todos os bolsistas que já têm orientador.
    bolsistas_com_orientador_ids = []
    for perfil in todos_os_perfis:
        for bolsista in perfil.bolsistas.all():
            bolsistas_com_orientador_ids.append(bolsista.id)

    # Agora, filtramos a lista de todos os alunos para encontrar
    # apenas aqueles que não estão na lista de quem já tem orientador.
    alunos_sem_orientador = todos_os_alunos.exclude(id__in=bolsistas_com_orientador_ids)


    context = {
        'todos_os_perfis': todos_os_perfis,
        'alunos_sem_orientador': alunos_sem_orientador,
    }

    return render(request, 'gerenciamento/listar_bolsistas.html', context)

@login_required
@user_passes_test(is_professor, login_url='/')
def delete_aluno(request, student_id):
    # Esta view agora só aceita o método POST
    if request.method == 'POST':
        aluno = get_object_or_404(get_user_model(), id=student_id)
        aluno_nome = aluno.get_full_name()
        aluno.delete()
        messages.success(request, f"O aluno {aluno_nome} foi excluído permanentemente.")

    # Redireciona de volta para a página de gerenciamento em qualquer caso
    return redirect('gerenciar_bolsistas')


@login_required
@user_passes_test(is_professor, login_url='/')
def edit_aluno(request, student_id):
    User = get_user_model()
    aluno = get_object_or_404(User, id=student_id)

    if request.method == 'POST':
        form = EditAlunoForm(request.POST, instance=aluno)
        if form.is_valid():
            cd = form.cleaned_data

            # Atualiza os dados do User
            aluno.first_name = cd['first_name']
            aluno.last_name = cd['last_name']
            aluno.email = cd['email']
            aluno.save()

            # Atualiza ou cria o AlunoProfile
            profile, created = AlunoProfile.objects.get_or_create(user=aluno)
            profile.curso = cd['curso']
            # --- LINHA MODIFICADA PARA SALVAR O TOKEN ---
            profile.rfid_token = cd['rfid_token']
            # --- FIM DA MODIFICAÇÃO ---
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
            # --- LINHA MODIFICADA PARA CARREGAR O TOKEN ---
            'rfid_token': profile.rfid_token if profile else ''
            # --- FIM DA MODIFICAÇÃO ---
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
