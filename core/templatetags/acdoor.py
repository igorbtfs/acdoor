# core/templatetags/acdoor.py
from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def uid_para(uid, ver_completo):
    """
    Mostra o UID de um cartão. O UID é a credencial que abre a porta e pode
    ser copiado para outro cartão, então só o administrador vê por inteiro;
    os demais veem os dois últimos bytes, o bastante para reconhecer o cartão.

        {{ cartao|uid_para:eh_admin }}  ->  "A1 B2 C3 D4" ou "•• •• C3 D4"
    """
    if not uid:
        return ''
    if ver_completo:
        return uid
    partes = uid.split()
    visiveis = partes[-2:] if len(partes) > 2 else partes[-1:]
    return ' '.join(['••'] * (len(partes) - len(visiveis)) + visiveis)


@register.filter
def ha_quanto(momento):
    """Tempo desde uma leitura de cartão: "há menos de 1 minuto", "há 3 minutos"."""
    minutos = int((timezone.now() - momento).total_seconds() // 60)
    if minutos < 1:
        return "há menos de 1 minuto"
    return f"há {minutos} minuto{'s' if minutos != 1 else ''}"
