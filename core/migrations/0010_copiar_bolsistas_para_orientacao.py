from datetime import timedelta

from django.db import migrations
from django.utils import timezone

# Quem já era bolsista não pode perder o acesso no dia da atualização:
# as orientações existentes ganham esta validade inicial e depois são renovadas
# pelo orientador.
VALIDADE_INICIAL_DIAS = 180


def copiar_para_orientacao(apps, schema_editor):
    ProfessorProfile = apps.get_model('core', 'ProfessorProfile')
    Orientacao = apps.get_model('core', 'Orientacao')
    valido_ate = timezone.localdate() + timedelta(days=VALIDADE_INICIAL_DIAS)

    for perfil in ProfessorProfile.objects.all():
        for aluno in perfil.bolsistas.all():
            # Ninguém é orientador de si mesmo: esse vínculo não é levado adiante.
            if aluno.pk == perfil.user_id:
                continue
            Orientacao.objects.get_or_create(
                orientador_id=perfil.user_id,
                aluno_id=aluno.pk,
                defaults={'valido_ate': valido_ate},
            )


def voltar_para_bolsistas(apps, schema_editor):
    ProfessorProfile = apps.get_model('core', 'ProfessorProfile')
    Orientacao = apps.get_model('core', 'Orientacao')

    for orientacao in Orientacao.objects.all():
        perfil, _ = ProfessorProfile.objects.get_or_create(user_id=orientacao.orientador_id)
        perfil.bolsistas.add(orientacao.aluno_id)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0009_orientacao_e_auditoria'),
    ]

    operations = [
        migrations.RunPython(copiar_para_orientacao, voltar_para_bolsistas),
    ]
