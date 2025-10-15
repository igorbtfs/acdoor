# core/models.py
from django.db import models
from django.contrib.auth.models import User

class ProfessorProfile(models.Model):
    # Cria um link um-para-um com o modelo de usuário padrão do Django.
    # Se um usuário for deletado, seu perfil também será.
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='professor_profile')

    # Cria uma relação Muitos-para-Muitos.
    # Um professor (através do seu perfil) pode gerenciar muitos usuários (alunos).
    # O 'limit_choices_to' garante que na interface do Admin só aparecerão usuários do grupo 'Alunos'.
    bolsistas = models.ManyToManyField(
        User,
        related_name='orientadores',
        blank=True,
        limit_choices_to={'groups__name': 'Alunos'}
    )

    def __str__(self):
        return f"Perfil de {self.user.get_full_name() or self.user.username}"