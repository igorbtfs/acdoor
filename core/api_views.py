# core/api_views.py
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import TokenAuthentication
from rest_framework.views import APIView
from rest_framework.response import Response
from django.contrib.auth import get_user_model

from .acesso import cartao_de, com_orientacoes, nome_de, situacao_do_aluno
from .models import AccessLog, UnassignedUIDLog
from .serializers import AccessLogSerializer


class BolsistaListView(APIView):
    """
    Lista os cartões que abrem a porta neste momento (nome, UID e validade).

    Serve para o dispositivo guardar uma cópia e continuar funcionando se
    ficar sem conexão com o servidor. Só entram alunos com acesso liberado:
    conta ativa, cartão cadastrado e orientação vigente.
    Acesso restrito a clientes com um token de API válido.
    """
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        User = get_user_model()
        alunos = com_orientacoes(
            User.objects.filter(
                groups__name='Alunos', is_active=True, aluno_profile__rfid_token__isnull=False
            ).distinct()
        )
        autorizados = []
        for aluno in alunos:
            situacao = situacao_do_aluno(aluno)
            if situacao.liberado:
                autorizados.append({
                    'nome_completo': nome_de(aluno),
                    'rfid_token': cartao_de(aluno),
                    'valido_ate': situacao.valido_ate.isoformat(),
                })
        return Response(autorizados)


class LogAccessView(APIView):
    """
    Recebe um UID de um dispositivo, verifica a permissão,
    cria um log e retorna se o acesso é permitido ou não.
    """
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = AccessLogSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Normaliza para o mesmo formato salvo pelo portal.
        uid = ' '.join(serializer.validated_data['uid'].upper().split())

        User = get_user_model()
        # 1. Tenta encontrar o aluno dono do cartão.
        aluno = com_orientacoes(User.objects.filter(aluno_profile__rfid_token=uid)).first()

        if aluno is None:
            # 3. NENHUM PERFIL ENCONTRADO => ACESSO NEGADO
            # O UID não pertence a ninguém cadastrado. Ele entra na lista de
            # cartões pendentes, de onde um orientador pode vinculá-lo a um aluno.
            AccessLog.objects.create(uid=uid, status='DENIED', user=None)
            # update_or_create (e não get_or_create) para atualizar o "visto por último".
            UnassignedUIDLog.objects.update_or_create(uid=uid)

            # Retorna 403 Forbidden, informando que o usuário é desconhecido.
            return Response({
                'status': 'access denied',
                'user': 'unknown'
            }, status=status.HTTP_403_FORBIDDEN)

        # 2. O dono do cartão foi encontrado. A regra de autorização é a mesma
        # mostrada nas telas: conta ativa e orientação vigente (não vencida,
        # com orientador ativo). Veja core/acesso.py.
        if situacao_do_aluno(aluno).liberado:
            # 2a. ACESSO CONCEDIDO
            AccessLog.objects.create(uid=uid, status='GRANTED', user=aluno)

            # Retorna 200 OK, informando que o acesso foi liberado.
            return Response({
                'status': 'access granted',
                'user': aluno.get_full_name()
            }, status=status.HTTP_200_OK)

        # 2b. ACESSO NEGADO
        AccessLog.objects.create(uid=uid, status='DENIED', user=aluno)

        # Retorna 403 Forbidden, mas informa qual usuário foi negado.
        return Response({
            'status': 'access denied',
            'user': aluno.get_full_name()
        }, status=status.HTTP_403_FORBIDDEN)
