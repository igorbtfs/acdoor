# core/forms.py
from django import forms
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from .models import CURSO_CHOICES

class AddBolsistaForm(forms.Form):
    # Adicione o campo de nome
    nome_completo = forms.CharField(
        label="Nome Completo do Aluno",
        widget=forms.TextInput(attrs={'placeholder': 'Nome Sobrenome'})
    )
    email = forms.EmailField(
        label="E-mail do Aluno",
        widget=forms.EmailInput(attrs={'placeholder': 'matricula@aluno.restinga.ifrs.edu.br'})
    )
    # Adicione o campo de curso como uma seleção
    curso = forms.ChoiceField(
        label="Curso",
        choices=CURSO_CHOICES
    )

    def clean_email(self):
        # ... (esta função continua a mesma) ...
        email = self.cleaned_data['email'].lower()
        domain = email.split('@')[1]

        allowed_domain = 'aluno.restinga.ifrs.edu.br'
        if domain != allowed_domain:
            raise ValidationError(f"O e-mail deve pertencer ao domínio '{allowed_domain}'.")

        if User.objects.filter(email=email).exists():
            raise ValidationError("Um usuário com este e-mail já existe no sistema.")

        return email
    
class EditAlunoForm(forms.Form):
    first_name = forms.CharField(label="Nome")
    last_name = forms.CharField(label="Sobrenome")
    email = forms.EmailField(label="E-mail")
    curso = forms.ChoiceField(label="Curso", choices=CURSO_CHOICES)

    def __init__(self, *args, **kwargs):
        # Guardamos o usuário original para usar na validação
        self.instance = kwargs.pop('instance', None)
        super().__init__(*args, **kwargs)

    def clean_email(self):
        """
        Valida o campo de e-mail para garantir que não haja duplicatas.
        """
        email = self.cleaned_data['email'].lower()

        # Se o e-mail não mudou, ele é válido.
        if self.instance and self.instance.email == email:
            return email

        # Se o e-mail mudou, verifica se ele já está em uso por outro usuário.
        if User.objects.filter(email=email).exists():
            raise ValidationError("Este e-mail já está em uso por outro aluno.")

        return email