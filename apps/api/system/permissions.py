from typing import Any

from rest_framework.request import Request
from rest_framework.permissions import BasePermission, IsAuthenticated

from system.constants import ADMIN_ROLE, OPERATOR_GROUP_NAME, OPERATOR_ROLE


def get_user_role(user: Any) -> str | None:
    if user.is_superuser:
        return ADMIN_ROLE
    if user.groups.filter(name=OPERATOR_GROUP_NAME).exists():
        return OPERATOR_ROLE
    return None


class HasApplicationRole(BasePermission):
    def has_permission(self, request: Request, view: Any) -> bool:
        required_role = getattr(view, "required_role", None)
        return get_user_role(request.user) == required_role


class AuthenticatedApplicationUser(IsAuthenticated):
    def has_permission(self, request: Request, view: Any) -> bool:
        if not super().has_permission(request, view):
            return False
        return get_user_role(request.user) is not None
