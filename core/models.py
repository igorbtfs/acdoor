# core/models.py
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

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
    # UID do cartão RFID do aluno, no formato enviado pelo ESP32 ("A1 B2 C3 D4").
    # Fica NULL enquanto o aluno não tem cartão (nunca '' - o campo é único).
    rfid_token = models.CharField(
        max_length=32,
        unique=True,
        blank=True,
        null=True,
        verbose_name="UID do Cartão RFID"
    )

    def __str__(self):
        return f"Perfil de {self.user.get_full_name() or self.user.username}"


def eh_gestor(user):
    """
    Professores (grupo 'Professores') e administradores (superusuários) podem
    gerenciar bolsistas e ser orientadores. Usa `user.groups.all()` para
    aproveitar o prefetch quando a lista de usuários já veio com os grupos.
    """
    if not user.is_active:
        return False
    return user.is_superuser or any(g.name == 'Professores' for g in user.groups.all())


class Orientacao(models.Model):
    """
    Um orientador se responsabiliza pelo acesso de um aluno ao laboratório,
    até uma data de validade. É isto que autoriza o cartão do aluno na porta.
    """
    orientador = models.ForeignKey(User, on_delete=models.CASCADE, related_name='orientacoes_dadas')
    aluno = models.ForeignKey(User, on_delete=models.CASCADE, related_name='orientacoes_recebidas')
    valido_ate = models.DateField(verbose_name="Válido até")
    criado_em = models.DateTimeField(auto_now_add=True)
    concedido_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
        verbose_name="Concedido por",
    )

    class Meta:
        verbose_name = "Orientação"
        verbose_name_plural = "Orientações"
        ordering = ['aluno__first_name', 'aluno__last_name']
        constraints = [
            models.UniqueConstraint(fields=['orientador', 'aluno'], name='orientacao_unica'),
        ]

    def __str__(self):
        return f"{self.orientador.get_full_name() or self.orientador.username} orienta {self.aluno.get_full_name() or self.aluno.username}"

    @property
    def vencida(self):
        return self.valido_ate < timezone.localdate()

    @property
    def vigente(self):
        """
        Só conta para liberar a porta se: não venceu, o orientador continua
        ativo e com papel de gestor, e ninguém é orientador de si mesmo.
        """
        return (
            not self.vencida
            and self.orientador_id != self.aluno_id
            and eh_gestor(self.orientador)
        )


class RegistroAuditoria(models.Model):
    """Quem concedeu, renovou ou retirou acesso, e quem mexeu em cartões e cadastros."""
    ACOES = [
        ('ORIENTACAO_CRIADA', 'Acesso concedido'),
        ('ORIENTACAO_RENOVADA', 'Validade alterada'),
        ('ORIENTACAO_ENCERRADA', 'Acesso retirado'),
        ('CARTAO_VINCULADO', 'Cartão vinculado'),
        ('CARTAO_TROCADO', 'Cartão trocado'),
        ('CARTAO_REMOVIDO', 'Cartão removido'),
        ('ALUNO_CADASTRADO', 'Aluno cadastrado'),
        ('ALUNO_EDITADO', 'Cadastro alterado'),
        ('ALUNO_EXCLUIDO', 'Aluno excluído'),
        ('ORIENTADOR_SOLICITADO', 'Pedido para ser orientador'),
        ('ORIENTADOR_INDICADO', 'Indicação de orientador'),
        ('ORIENTADOR_APROVADO', 'Orientador aprovado'),
        ('ORIENTADOR_RECUSADO', 'Pedido de orientador recusado'),
        ('ORIENTADOR_REVOGADO', 'Papel de orientador retirado'),
    ]

    quando = models.DateTimeField(auto_now_add=True)
    acao = models.CharField(max_length=30, choices=ACOES)
    # Os nomes ficam copiados no registro: ele continua legível mesmo que a
    # conta do autor ou do aluno seja excluída depois.
    autor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    autor_nome = models.CharField(max_length=200)
    # "aluno" é a pessoa afetada pela ação. Quase sempre é um aluno; nas ações
    # de orientador é o servidor que pediu, foi indicado, aprovado ou revogado.
    aluno = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    aluno_nome = models.CharField(max_length=200)
    detalhe = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "Registro de auditoria"
        verbose_name_plural = "Registros de auditoria"
        ordering = ['-quando', '-id']

    def __str__(self):
        return f"{self.quando:%d/%m/%Y %H:%M} - {self.autor_nome}: {self.get_acao_display()} ({self.aluno_nome})"


class PedidoOrientador(models.Model):
    """
    Pedido para que um servidor do campus passe a ser orientador, isto é, possa
    conceder acesso ao laboratório. Entrar com o e-mail institucional não basta:
    o administrador precisa aprovar. O pedido é feito por e-mail, e não por
    usuário, porque um orientador pode indicar um colega que ainda não entrou
    no portal; nesse caso a aprovação vale no primeiro login dele.
    """
    STATUS = [
        ('PENDENTE', 'Aguardando aprovação'),
        ('APROVADO', 'Aprovado'),
        ('RECUSADO', 'Recusado'),
        ('REVOGADO', 'Papel retirado'),
    ]
    ORIGEM = [
        ('PROPRIO', 'Pedido pelo próprio servidor'),
        ('INDICACAO', 'Indicado por um orientador'),
        ('ADMIN', 'Autorizado direto pelo administrador'),
    ]

    email = models.EmailField()
    usuario = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='pedidos_orientador',
    )
    origem = models.CharField(max_length=10, choices=ORIGEM)
    pedido_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    pedido_por_nome = models.CharField(max_length=200)
    justificativa = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default='PENDENTE')
    criado_em = models.DateTimeField(auto_now_add=True)
    decidido_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    decidido_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Pedido de orientador"
        verbose_name_plural = "Pedidos de orientador"
        ordering = ['-criado_em', '-id']
        constraints = [
            # Só um pedido aguardando por pessoa.
            models.UniqueConstraint(
                fields=['email'], condition=models.Q(status='PENDENTE'), name='pedido_orientador_pendente_unico',
            ),
        ]

    def __str__(self):
        return f"{self.email} ({self.get_status_display()})"


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