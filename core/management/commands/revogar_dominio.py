"""
Revoga contas antigas de um domínio de e-mail que deixou de ser aceito.

O login com @gmail.com foi removido do código, mas contas criadas enquanto ele
era aceito continuam no banco, dentro do grupo 'Professores'. Este comando
lista essas contas e, com --aplicar, tira do grupo e desativa cada uma.

    python manage.py revogar_dominio gmail.com            # só mostra
    python manage.py revogar_dominio gmail.com --aplicar  # executa

Nada é apagado, mas uma conta desativada deixa de valer como orientador: os
bolsistas dela perdem o acesso à porta se não tiverem outro orientador ativo.
O comando mostra quantos são antes de aplicar. Superusuários não são alterados.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Lista (e com --aplicar desativa) contas de um domínio de e-mail que não é mais aceito."

    def add_arguments(self, parser):
        parser.add_argument('dominio', help="Domínio a revogar, ex.: gmail.com")
        parser.add_argument('--aplicar', action='store_true', help="Executa as alterações (sem isto, só lista).")

    def handle(self, *args, **options):
        dominio = options['dominio'].lower().lstrip('@')
        User = get_user_model()
        contas = User.objects.filter(email__iendswith=f'@{dominio}').order_by('email')

        if not contas:
            self.stdout.write(f"Nenhuma conta @{dominio} encontrada.")
            return

        professores = Group.objects.filter(name='Professores').first()
        alteradas = 0

        for user in contas:
            grupos = ', '.join(user.groups.values_list('name', flat=True)) or 'nenhum'
            bolsistas = user.orientacoes_dadas.count()
            cartao = getattr(getattr(user, 'aluno_profile', None), 'rfid_token', None)

            self.stdout.write(
                f"- {user.email} (id={user.pk}) | ativo={user.is_active} | "
                f"superusuario={user.is_superuser} | grupos: {grupos} | "
                f"orienta {bolsistas} bolsista(s) | cartao: {cartao or '-'}"
            )

            if user.is_superuser:
                self.stdout.write("    -> superusuario: mantido como esta.")
                continue
            if bolsistas:
                self.stdout.write(
                    "    -> ATENCAO: os bolsistas desta conta PERDEM o acesso a porta ao desativar, "
                    "a menos que tenham outro orientador ativo. Transfira a orientacao antes."
                )
            if cartao:
                self.stdout.write("    -> ATENCAO: ao desativar, o cartao desta conta deixa de abrir a porta.")

            if options['aplicar']:
                if professores:
                    user.groups.remove(professores)
                user.is_active = False
                user.save(update_fields=['is_active'])
                alteradas += 1

        if options['aplicar']:
            self.stdout.write(self.style.SUCCESS(f"{alteradas} conta(s) desativada(s) e removida(s) do grupo Professores."))
        else:
            self.stdout.write("Nada foi alterado. Rode novamente com --aplicar para executar.")
