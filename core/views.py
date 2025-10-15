# core/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.http import HttpResponseForbidden


def home(request):
    return render(request, 'home.html')

def custom_login(request):
    return render(request, 'login.html')
# Função auxiliar para verificar se o usuário está no grupo 'Professores'
def is_professor(user):
    return user.groups.filter(name='Professores').exists()

@login_required
@user_passes_test(is_professor, login_url='/') # Redireciona para home se não for professor
def gerenciar_bolsistas(request):
    # Pega o professor logado (seu perfil)
    professor_profile = request.user.professor_profile

    if request.method == 'POST':
        # Pega os dados enviados pelo formulário
        student_id = request.POST.get('student_id')
        action = request.POST.get('action')

        # Busca o objeto do aluno no banco de dados
        aluno = get_object_or_404(User, id=student_id)

        if action == 'add':
            # Adiciona o aluno à lista de bolsistas do professor
            professor_profile.bolsistas.add(aluno)
        elif action == 'remove':
            # Remove o aluno da lista de bolsistas do professor
            professor_profile.bolsistas.remove(aluno)

        # Redireciona para a mesma página para evitar reenvio do formulário
        return redirect('gerenciar_bolsistas')

    # Se a requisição for GET (carregando a página)
    # Pega a lista de bolsistas que já são gerenciados por este professor
    bolsistas_gerenciados = professor_profile.bolsistas.all()

    # Pega a lista de todos os usuários que estão no grupo 'Alunos'
    todos_os_alunos = User.objects.filter(groups__name='Alunos')

    context = {
        'todos_os_alunos': todos_os_alunos,
        'bolsistas_gerenciados': bolsistas_gerenciados,
    }
    return render(request, 'gerenciamento/gerenciar_bolsistas.html', context)
