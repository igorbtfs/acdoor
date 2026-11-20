# core/orientadores.py
"""
Quem pode ser orientador.

Entrar no portal com o e-mail de servidor do campus não dá nenhum poder: a
conta fica só no grupo 'Servidores'. O papel de orientador (grupo
'Professores', que permite conceder acesso à porta) depende de um pedido
aprovado pelo administrador. O pedido pode vir do próprio servidor, de um
orientador que indica um colega, ou ser criado já aprovado pelo administrador.
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils import timezone

from .acesso import com_orientacoes, nome_de, registrar
from .models import PedidoOrientador

DOMINIO_SERVIDORES = 'restinga.ifrs.edu.br'
GRUPO_SERVIDORES = 'Servidores'
GRUPO_ORIENTADORES = 'Professores'


def email_aprovado(email):
    """Existe um pedido aprovado (e não revogado depois) para este e-mail?"""
    return PedidoOrientador.objects.filter(email__iexact=email, status='APROVADO').exists()


def tem_pedido_pendente(email):
    return PedidoOrientador.objects.filter(email__iexact=email, status='PENDENTE').exists()


def eh_orientador(user):
    return user.groups.filter(name=GRUPO_ORIENTADORES).exists()


def na_lista_do_servidor(email):
    """O e-mail está em PROFESSOR_EMAILS (.env)? Nesse caso o papel volta a cada login."""
    return (email or '').lower() in settings.PROFESSOR_EMAILS


def vincular_pedidos(user, email):
    """Pedidos feitos para um e-mail antes de a pessoa ter conta passam a apontar para a conta."""
    if email:
        PedidoOrientador.objects.filter(email__iexact=email, usuario__isnull=True).update(usuario=user)


def dar_papel(user):
    grupo, _ = Group.objects.get_or_create(name=GRUPO_ORIENTADORES)
    user.groups.add(grupo)


def criar_pedido(email, origem, pedido_por, justificativa=''):
    """Registra o pedido. Quem decide é o administrador (aprovar_pedido / recusar_pedido)."""
    User = get_user_model()
    email = email.strip().lower()
    return PedidoOrientador.objects.create(
        email=email,
        usuario=User.objects.filter(email__iexact=email).first(),
        origem=origem,
        pedido_por=pedido_por,
        pedido_por_nome=nome_de(pedido_por),
        justificativa=justificativa.strip(),
    )


def nome_do_pedido(pedido):
    return nome_de(pedido.usuario) if pedido.usuario else pedido.email


def aprovar_pedido(pedido, admin):
    """Aprova e, se a pessoa já tem conta, dá o papel na hora; senão, vale no primeiro login."""
    pedido.status = 'APROVADO'
    pedido.decidido_por = admin
    pedido.decidido_em = timezone.now()
    pedido.save(update_fields=['status', 'decidido_por', 'decidido_em'])
    if pedido.usuario:
        dar_papel(pedido.usuario)
    registrar(admin, 'ORIENTADOR_APROVADO', pedido.usuario, pedido.get_origem_display().lower(),
              nome=nome_do_pedido(pedido))


def recusar_pedido(pedido, admin):
    pedido.status = 'RECUSADO'
    pedido.decidido_por = admin
    pedido.decidido_em = timezone.now()
    pedido.save(update_fields=['status', 'decidido_por', 'decidido_em'])
    registrar(admin, 'ORIENTADOR_RECUSADO', pedido.usuario, pedido.get_origem_display().lower(),
              nome=nome_do_pedido(pedido))


def alunos_que_perdem_acesso(orientador):
    """
    Bolsistas que hoje só têm orientação em dia com este orientador: se ele
    deixar de ser orientador, o cartão deles para de abrir a porta.
    """
    User = get_user_model()
    alunos = com_orientacoes(
        User.objects.filter(orientacoes_recebidas__orientador=orientador)
        .distinct()
        .order_by('first_name', 'last_name')
    )
    perdem = []
    for aluno in alunos:
        vigentes = [o for o in aluno.orientacoes_recebidas.all() if o.vigente]
        if vigentes and all(o.orientador_id == orientador.id for o in vigentes):
            perdem.append(aluno)
    return perdem


def retirar_papel(user, admin):
    """
    Tira o papel de orientador. As orientações dele continuam registradas, mas
    deixam de valer na porta enquanto ele não voltar a ser orientador.
    """
    afetados = len(alunos_que_perdem_acesso(user))
    grupo = Group.objects.filter(name=GRUPO_ORIENTADORES).first()
    if grupo:
        user.groups.remove(grupo)
    # Sem isto, o pedido aprovado devolveria o papel no próximo login.
    PedidoOrientador.objects.filter(email__iexact=user.email, status='APROVADO').update(
        status='REVOGADO', decidido_por=admin, decidido_em=timezone.now()
    )
    registrar(admin, 'ORIENTADOR_REVOGADO', user, f"{afetados} bolsista(s) ficaram sem orientador em dia")
    return afetados
