# core/api_views.py
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import TokenAuthentication
from django.contrib.auth import get_user_model
from .serializers import BolsistaSerializer

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