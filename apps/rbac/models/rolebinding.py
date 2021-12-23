from django.utils.translation import ugettext_lazy as _
from django.db import models
from django.db.models import Q

from common.db.models import JMSModel
from orgs.utils import current_org
from .role import Role
from .. const import Scope

__all__ = ['RoleBinding']


class RoleBindingManager(models.Manager):
    def get_queryset(self):
        queryset = super(RoleBindingManager, self).get_queryset()
        q = Q(scope=Scope.system)
        if not current_org.is_root():
            q |= Q(org_id=current_org.id, scope=Scope.org)
        queryset = queryset.filter(q)
        return queryset


class RoleBinding(JMSModel):
    Scope = Scope
    """ 定义 用户-角色 关系 """
    scope = models.CharField(
        max_length=128, choices=Scope.choices, default=Scope.system,
        verbose_name=_('Scope')
    )
    user = models.ForeignKey(
        'users.User', related_name='role_bindings', on_delete=models.CASCADE, verbose_name=_('User')
    )
    role = models.ForeignKey(
        Role, related_name='role_bindings', on_delete=models.CASCADE, verbose_name=_('Role')
    )
    org = models.ForeignKey(
        'orgs.Organization', related_name='role_bindings', blank=True, null=True,
        on_delete=models.CASCADE, verbose_name=_('Organization')
    )
    objects = RoleBindingManager()

    class Meta:
        verbose_name = _('Role binding')
        unique_together = ('user', 'role', 'org')

    def __str__(self):
        display = '{user} & {role}'.format(user=self.user, role=self.role)
        if self.org:
            display += ' | {org}'.format(org=self.org)
        return display

    def save(self, *args, **kwargs):
        self.scope = self.role.scope
        return super().save(*args, **kwargs)

    @classmethod
    def get_user_perms(cls, user):
        roles = cls.get_user_roles(user)
        return Role.get_roles_perms(roles)

    @classmethod
    def get_role_users(cls, role):
        from users.models import User
        bindings = cls.objects.filter(role=role, scope=role.scope)
        user_ids = bindings.values_list('user', flat=True).distinct()
        return User.objects.filter(id__in=user_ids)

    @classmethod
    def get_user_roles(cls, user):
        bindings = cls.objects.filter(user=user)
        roles_id = bindings.values_list('role', flat=True).distinct()
        return Role.objects.filter(id__in=roles_id)
