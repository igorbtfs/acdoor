# core/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    #path('login/', views.custom_login, name='login'),
    # Adicione esta linha
    path('gerenciar-bolsistas/', views.gerenciar_bolsistas, name='gerenciar_bolsistas'),
    path('painel-bolsistas/', views.listar_bolsistas_orientadores, name='listar_bolsistas_orientadores'),
    path('delete-aluno/<int:student_id>/', views.delete_aluno, name='delete_aluno'),
    path('edit-aluno/<int:student_id>/', views.edit_aluno, name='edit_aluno'),
    path('logs-de-acesso/', views.access_logs_view, name='access_logs'),
]
