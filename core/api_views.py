# core/api_views.py
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import TokenAuthentication
from rest_framework.views import APIView
from rest_framework.response import Response
from django.contrib.auth import get_user_model
from .models import AlunoProfile, AccessLog, UnassignedUIDLog
from .serializers import BolsistaSerializer, AccessLogSerializer

class BolsistaListView(generics.ListAPIView):
    """
    Endpoint de API que lista todos os alunos bolsistas ativos.
    Acesso restrito a clientes com um token de API válido.
    """
    serializer_class = BolsistaSerializer

    # Define como a API deve se autenticar
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """
        Retorna apenas usuários que estão no grupo 'Alunos' e estão ativos.
        """
        User = get_user_model()
        return User.objects.filter(groups__name='Alunos', is_active=True).select_related('aluno_profile')
    
class LogAccessView(APIView):
    """
    Recebe um UID de um dispositivo, verifica a permissão,
    cria um log e retorna se o acesso é permitido ou não.
    """
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = AccessLogSerializer(data=request.data)
        if serializer.is_valid():
            # Normaliza para o mesmo formato salvo pelo formulário de edição.
            uid = ' '.join(serializer.validated_data['uid'].upper().split())

            try:
                # 1. Tenta encontrar o perfil do aluno pelo UID.
                # .select_related('user') otimiza a busca, pegando o usuário na mesma consulta.
                profile = AlunoProfile.objects.select_related('user').get(rfid_token=uid)
                aluno = profile.user

                # 2. O perfil foi encontrado. Agora, vamos verificar a autorização.
                # A verificação é se a conta está ativa e se o aluno tem algum
                # orientador vinculado. (Desativar o usuário no /admin/ também
                # bloqueia o cartão.)
                if aluno.is_active and aluno.orientadores.exists():
                    # 2a. TEM ORIENTADOR => ACESSO CONCEDIDO
                    AccessLog.objects.create(uid=uid, status='GRANTED', user=aluno)
                    
                    # Retorna 200 OK, informando que o acesso foi liberado.
                    return Response({
                        'status': 'access granted', 
                        'user': aluno.get_full_name()
                    }, status=status.HTTP_200_OK)
                else:
                    # 2b. NÃO TEM ORIENTADOR => ACESSO NEGADO
                    AccessLog.objects.create(uid=uid, status='DENIED', user=aluno)
                    
                    # Retorna 403 Forbidden, mas informa qual usuário foi negado.
                    return Response({
                        'status': 'access denied', 
                        'user': aluno.get_full_name()
                    }, status=status.HTTP_403_FORBIDDEN)

            except AlunoProfile.DoesNotExist:
                # 3. NENHUM PERFIL ENCONTRADO => ACESSO NEGADO
                # O UID não pertence a ninguém cadastrado.
                AccessLog.objects.create(uid=uid, status='DENIED', user=None)
                # update_or_create (e não get_or_create) para atualizar o "visto por último".
                UnassignedUIDLog.objects.update_or_create(uid=uid)
                
                # Retorna 403 Forbidden, informando que o usuário é desconhecido.
                return Response({
                    'status': 'access denied', 
                    'user': 'unknown'
                }, status=status.HTTP_403_FORBIDDEN)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)