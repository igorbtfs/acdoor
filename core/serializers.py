# core/serializers.py
from rest_framework import serializers


class AccessLogSerializer(serializers.Serializer):
    """
    Serializer para validar o UID recebido do ESP32.
    """
    uid = serializers.CharField(max_length=32)
