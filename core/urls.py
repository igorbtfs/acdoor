# core/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),

    # Bolsistas e alunos
    path('gerenciar-bolsistas/', views.gerenciar_bolsistas, name='gerenciar_bolsistas'),
    path('alunos/', views.alunos, name='alunos'),
    path('alunos/<int:student_id>/reivindicar/', views.reivindicar_aluno, name='reivindicar_aluno'),
    path('edit-aluno/<int:student_id>/', views.edit_aluno, name='edit_aluno'),
    path('delete-aluno/<int:student_id>/', views.delete_aluno, name='delete_aluno'),
    path('orientacoes/<int:orientacao_id>/renovar/', views.renovar_orientacao, name='renovar_orientacao'),
    path('orientacoes/<int:orientacao_id>/encerrar/', views.encerrar_orientacao, name='encerrar_orientacao'),

    # Cartões lidos no leitor que ainda não pertencem a ninguém
    path('cartoes-pendentes/', views.cartoes_pendentes, name='cartoes_pendentes'),
    path('cartoes-pendentes/vincular/', views.vincular_cartao, name='vincular_cartao'),

    # Consultas
    path('logs-de-acesso/', views.access_logs_view, name='access_logs'),
    path('painel-bolsistas/', views.listar_bolsistas_orientadores, name='listar_bolsistas_orientadores'),
    path('auditoria/', views.auditoria, name='auditoria'),

    # Quem pode ser orientador: pedido, indicação e decisão do administrador
    path('orientadores/solicitar/', views.solicitar_orientador, name='solicitar_orientador'),
    path('orientadores/indicar/', views.indicar_orientador, name='indicar_orientador'),
    path('orientadores/pedidos/<int:pedido_id>/decidir/', views.decidir_pedido_orientador, name='decidir_pedido_orientador'),
    path('orientadores/<int:user_id>/retirar/', views.retirar_orientador, name='retirar_orientador'),
]
