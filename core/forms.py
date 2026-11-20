# core/forms.py
from django import forms
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User

from .acesso import validade_maxima, validade_padrao
from .models import CURSO_CHOICES, AlunoProfile
from django.utils import timezone

DOMINIO_ALUNOS = 'aluno.restinga.ifrs.edu.br'


def campo_validade():
    """Campo de data "válido até", já preenchido com a validade padrão."""
    return forms.DateField(
        label="Acesso válido até",
        initial=validade_padrao,
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
    )


def validar_validade(data):
    hoje = timezone.localdate()
    if data < hoje:
        raise ValidationError("A validade não pode estar no passado.")
    if data > validade_maxima():
        raise ValidationError(
            f"O prazo máximo é {validade_maxima():%d/%m/%Y}. Depois disso é preciso renovar."
        )
    return data


def normalizar_uid(texto):
    """Mesmo formato enviado pelo ESP32: maiúsculas, bytes separados por um espaço."""
    return ' '.join((texto or '').upper().split())


class ValidadeForm(forms.Form):
    """Usado para conceder (reivindicar) e renovar o acesso de um aluno."""
    valido_ate = campo_validade()

    def clean_valido_ate(self):
        return validar_validade(self.cleaned_data['valido_ate'])


class CadastrarAlunoForm(forms.Form):
    """Pré-cadastro de um aluno que ainda não entrou no portal com o Google."""
    nome_completo = forms.CharField(
        label="Nome completo",
        widget=forms.TextInput(attrs={'placeholder': 'Nome Sobrenome'})
    )
    email = forms.EmailField(
        label="E-mail institucional",
        widget=forms.EmailInput(attrs={'placeholder': f'matricula@{DOMINIO_ALUNOS}'})
    )
    curso = forms.ChoiceField(label="Curso", choices=CURSO_CHOICES)
    valido_ate = campo_validade()

    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        domain = email.split('@')[1]

        if domain != DOMINIO_ALUNOS:
            raise ValidationError(f"O e-mail deve pertencer ao domínio '{DOMINIO_ALUNOS}'.")

        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("Já existe um usuário com este e-mail. Procure por ele na lista abaixo.")

        return email

    def clean_valido_ate(self):
        return validar_validade(self.cleaned_data['valido_ate'])


class EditAlunoForm(forms.Form):
    """
    Edição do cadastro de um aluno.

    O orientador altera nome e curso e pode remover o cartão. Só o
    administrador altera o e-mail (é ele que liga o cadastro à conta Google do
    aluno) e digita um UID à mão; o caminho normal para vincular um cartão é a
    tela "Cartões pendentes", que exige que o cartão tenha acabado de ser lido.
    """
    first_name = forms.CharField(label="Nome")
    last_name = forms.CharField(label="Sobrenome", required=False)
    email = forms.EmailField(label="E-mail")
    curso = forms.ChoiceField(label="Curso", choices=CURSO_CHOICES)
    rfid_token = forms.CharField(
        label="UID do cartão RFID",
        required=False,
        help_text="Deixe em branco para remover o cartão.",
        widget=forms.TextInput(attrs={'placeholder': 'Ex: A1 B2 C3 D4'})
    )
    remover_cartao = forms.BooleanField(label="Remover o cartão deste aluno", required=False)

    def __init__(self, *args, instance, admin, **kwargs):
        # Guardamos o usuário original para usar na validação
        self.instance = instance
        super().__init__(*args, **kwargs)

        perfil = getattr(instance, 'aluno_profile', None)
        tem_cartao = bool(perfil and perfil.rfid_token)

        if admin:
            del self.fields['remover_cartao']
        else:
            del self.fields['email']
            del self.fields['rfid_token']
            if not tem_cartao:
                del self.fields['remover_cartao']

    def clean_email(self):
        """
        Valida o campo de e-mail para garantir que não haja duplicatas.
        """
        email = self.cleaned_data['email'].lower()

        # Verifica se o e-mail já está em uso por OUTRO usuário.
        em_uso = User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk)
        if em_uso.exists():
            raise ValidationError("Este e-mail já está em uso por outro usuário.")

        return email

    def clean_rfid_token(self):
        """
        Validação para o UID do cartão.
        """
        token = normalizar_uid(self.cleaned_data.get('rfid_token'))

        # Campo vazio = aluno sem cartão. Retorna None (e não '') porque o
        # campo é único no banco: dois alunos com '' dariam erro 500.
        if not token:
            return None

        # Verifica se o UID já está em uso por OUTRO aluno
        em_uso = AlunoProfile.objects.filter(rfid_token=token).exclude(user=self.instance)
        if em_uso.exists():
            raise ValidationError("Este UID de cartão já está atribuído a outro aluno.")

        return token
