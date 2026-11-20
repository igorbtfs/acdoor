# core/admin.py
from django.contrib import admin
from .models import AlunoProfile, AccessLog, Orientacao, PedidoOrientador, RegistroAuditoria, UnassignedUIDLog

class AccessLogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'uid', 'status', 'user')
    list_filter = ('status', 'timestamp')
    search_fields = ('uid', 'user__first_name', 'user__last_name')

class UnassignedUIDLogAdmin(admin.ModelAdmin):
    list_display = ('uid', 'first_seen', 'last_seen')
    ordering = ('-last_seen',)

class OrientacaoAdmin(admin.ModelAdmin):
    list_display = ('aluno', 'orientador', 'valido_ate', 'concedido_por', 'criado_em')
    list_filter = ('valido_ate',)
    search_fields = (
        'aluno__first_name', 'aluno__last_name', 'aluno__email',
        'orientador__first_name', 'orientador__last_name',
    )

class RegistroAuditoriaAdmin(admin.ModelAdmin):
    """A trilha de auditoria é só para consulta: ninguém cria, altera ou apaga por aqui."""
    list_display = ('quando', 'autor_nome', 'acao', 'aluno_nome', 'detalhe')
    list_filter = ('acao', 'quando')
    search_fields = ('autor_nome', 'aluno_nome', 'detalhe')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

admin.site.register(AlunoProfile)
admin.site.register(AccessLog, AccessLogAdmin)
admin.site.register(UnassignedUIDLog, UnassignedUIDLogAdmin)
admin.site.register(Orientacao, OrientacaoAdmin)
admin.site.register(RegistroAuditoria, RegistroAuditoriaAdmin)


@admin.register(PedidoOrientador)
class PedidoOrientadorAdmin(admin.ModelAdmin):
    list_display = ('email', 'status', 'origem', 'pedido_por_nome', 'criado_em', 'decidido_por', 'decidido_em')
    list_filter = ('status', 'origem')
    search_fields = ('email', 'pedido_por_nome', 'justificativa')
