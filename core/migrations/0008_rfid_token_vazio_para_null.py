from django.db import migrations


def vazio_para_null(apps, schema_editor):
    """
    O campo rfid_token é único. Alunos sem cartão eram salvos com '' (string
    vazia), o que só funciona para UM aluno; o segundo causava erro 500.
    Sem cartão passa a ser NULL, que pode se repetir.
    """
    AlunoProfile = apps.get_model('core', 'AlunoProfile')
    AlunoProfile.objects.filter(rfid_token='').update(rfid_token=None)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0007_unassigneduidlog_accesslog'),
    ]

    operations = [
        migrations.RunPython(vazio_para_null, migrations.RunPython.noop),
    ]
