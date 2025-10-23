# core/admin.py
from django.contrib import admin
from .models import ProfessorProfile, AlunoProfile, AccessLog, UnassignedUIDLog

class AccessLogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'uid', 'status', 'user')
    list_filter = ('status', 'timestamp')
    search_fields = ('uid', 'user__first_name', 'user__last_name')

class UnassignedUIDLogAdmin(admin.ModelAdmin):
    list_display = ('uid', 'first_seen', 'last_seen')
    ordering = ('-last_seen',)

admin.site.register(ProfessorProfile)
admin.site.register(AlunoProfile) 
admin.site.register(AccessLog, AccessLogAdmin)
admin.site.register(UnassignedUIDLog, UnassignedUIDLogAdmin)

