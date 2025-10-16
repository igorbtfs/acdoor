# core/admin.py
from django.contrib import admin
from .models import ProfessorProfile, AlunoProfile

admin.site.register(ProfessorProfile)
admin.site.register(AlunoProfile) 