# core/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('login/', views.custom_login, name='login'),
    # Adicione esta linha
    path('gerenciar-bolsistas/', views.gerenciar_bolsistas, name='gerenciar_bolsistas'),
]