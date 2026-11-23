from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError, CommandParser

from system.constants import OPERATOR_GROUP_NAME


class Command(BaseCommand):
    help = "Agrega una cuenta existente al rol OPERADOR de forma local."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--username", required=True)

    def handle(self, *args: Any, **options: Any) -> None:
        username = options["username"]
        user_model = get_user_model()
        try:
            user = user_model.objects.get(username=username)
        except user_model.DoesNotExist as exc:
            raise CommandError(f"No existe el usuario {username!r}.") from exc

        if user.is_superuser:
            raise CommandError("Una cuenta ADMIN no puede aprovisionarse como OPERADOR.")

        operator_group, _ = Group.objects.get_or_create(name=OPERATOR_GROUP_NAME)
        user.groups.add(operator_group)
        self.stdout.write(self.style.SUCCESS(f"{username} pertenece al grupo OPERADOR."))
