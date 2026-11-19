# core/forms.py
from django import forms
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from .models import CURSO_CHOICES, AlunoProfile

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

        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("Um usuário com este e-mail já existe no sistema.")

        return email
    
class EditAlunoForm(forms.Form):
    first_name = forms.CharField(label="Nome")
    last_name = forms.CharField(label="Sobrenome")
    email = forms.EmailField(label="E-mail")
    curso = forms.ChoiceField(label="Curso", choices=CURSO_CHOICES)
    # `required=False` permite que o campo seja salvo em branco (para alunos que ainda não têm um cartão)
    rfid_token = forms.CharField(
        label="UID do Cartão RFID",
        required=False,
        widget=forms.TextInput(attrs={'placeholder': 'Ex: A1 B2 C3 D4'})
    )


    def __init__(self, *args, **kwargs):
        # Guardamos o usuário original para usar na validação
        self.instance = kwargs.pop('instance', None)
        super().__init__(*args, **kwargs)

    def clean_email(self):
        """
        Valida o campo de e-mail para garantir que não haja duplicatas.
        """
        email = self.cleaned_data['email'].lower()

        # Verifica se o e-mail já está em uso por OUTRO usuário.
        em_uso = User.objects.filter(email__iexact=email)
        if self.instance:
            em_uso = em_uso.exclude(pk=self.instance.pk)
        if em_uso.exists():
            raise ValidationError("Este e-mail já está em uso por outro usuário.")

        return email
    
    def clean_rfid_token(self):
        """
        Validação para o UID do cartão.
        """
        # Mesmo formato enviado pelo ESP32: maiúsculas, bytes separados por um espaço.
        token = ' '.join((self.cleaned_data.get('rfid_token') or '').upper().split())

        # Campo vazio = aluno sem cartão. Retorna None (e não '') porque o
        # campo é único no banco: dois alunos com '' dariam erro 500.
        if not token:
            return None

        # Verifica se o UID já está em uso por OUTRO aluno
        em_uso = AlunoProfile.objects.filter(rfid_token=token)
        if self.instance:
            em_uso = em_uso.exclude(user=self.instance)
        if em_uso.exists():
            raise ValidationError("Este UID de cartão já está atribuído a outro aluno.")

        return token