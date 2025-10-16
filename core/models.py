# core/models.py
from django.db import models
from django.contrib.auth.models import User
import uuid 

# Lista de cursos pré-estipulada
CURSO_CHOICES = [
    ('INDEFINIDO', 'A Definir'),
    ('ADS', 'Análise e Desenvolvimento de Sistemas'),
    ('LETRAS', 'Licenciatura em Letras Português e Espanhol'),
    ('AGROECO', 'Tecnologia em Agroecologia'),
    ('ELETRO', 'Tecnologia em Eletrônica Industrial'),
    ('GESTAO', 'Tecnologia em Gestão Desportiva e de Lazer'),
    ('PROC', 'Tecnologia em Processos Gerenciais'),
    ('TECELETRO', 'Técnicos Integrados ao Ensino Médio | Eletrônica'),
    ('TECINFO', 'Técnicos Integrados ao Ensino Médio | Informática'),
    ('TECLAZER', 'Técnicos Integrados ao Ensino Médio | Lazer'),
    ('TECGUIA', 'Técnicos Subsequentes ao Ensino Médio | Guia de Turismo'),
    ('EJAAGRO', 'PROEJA | Agroecologia'),
    ('EJACIA', 'PROEJA | Comércio'),
    ('POS', 'Especialização'),
]

class AlunoProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='aluno_profile')
    curso = models.CharField(max_length=100, choices=CURSO_CHOICES)
    # --- NOVO CAMPO ---
    # Gera um token único para cada perfil de aluno criado.
    # `editable=False` impede que seja modificado no admin.
    rfid_token = models.CharField(
        max_length=32,
        unique=True,
        blank=True,
        null=True,
        verbose_name="UID do Cartão RFID"
    )
    # --- FIM DO NOVO CAMPO ---

    def __str__(self):
        return f"Perfil de {self.user.get_full_name() or self.user.username}"


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
    
class AccessLog(models.Model):
    STATUS_CHOICES = [
        ('GRANTED', 'Acesso Concedido'),
        ('DENIED', 'Acesso Negado'),
    ]

    timestamp = models.DateTimeField(auto_now_add=True, verbose_name="Data e Hora")
    uid = models.CharField(max_length=32, verbose_name="UID do Cartão")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, verbose_name="Status")
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Usuário (se conhecido)"
    )

    def __str__(self):
        return f"{self.timestamp.strftime('%d/%m/%Y %H:%M')} - {self.uid} - {self.status}"

class UnassignedUIDLog(models.Model):
    uid = models.CharField(max_length=32, unique=True, verbose_name="UID não atribuído")
    first_seen = models.DateTimeField(auto_now_add=True, verbose_name="Visto pela primeira vez")
    last_seen = models.DateTimeField(auto_now=True, verbose_name="Visto por último")

    def __str__(self):
        return self.uid