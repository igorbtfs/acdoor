# core/serializers.py
from rest_framework import serializers
from django.contrib.auth.models import User

class BolsistaSerializer(serializers.ModelSerializer):
    # Um campo customizado para obter o nome completo do usuário
    nome_completo = serializers.CharField(source='get_full_name')

    # Um campo para acessar o token RFID através do perfil do aluno
    rfid_token = serializers.UUIDField(source='aluno_profile.rfid_token', read_only=True)

    class Meta:
        model = User
        # Lista de campos que serão expostos na API
        fields = ['nome_completo', 'email', 'rfid_token']