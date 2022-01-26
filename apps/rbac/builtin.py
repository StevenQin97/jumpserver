from django.utils.translation import ugettext_noop

from .const import Scope, system_exclude_permissions, org_exclude_permissions


auditor_perms = (
    ('audits', '*', '*', '*'),
    ('rbac', 'menupermission', 'view', 'auditview'),
    ('terminal', 'session', '*', '*'),
    ('terminal', 'command', '*', '*'),
)

user_perms = (
    ('rbac', 'menupermission', 'view', 'userview'),
    ('perms', 'assetpermission', 'view,connect', 'myassets'),
    ('perms', 'applicationpermission', 'view,connect', 'myapps'),
)

app_exclude_perms = [
    ('users', 'user', 'add,delete', 'user'),
    ('orgs', 'org', 'add,delete,change', 'org'),
    ('rbac', '*', '*', '*'),
]

need_check = [
    *auditor_perms, *user_perms, *app_exclude_perms,
    *system_exclude_permissions, *org_exclude_permissions
]
defines_errors = [d for d in need_check if len(d) != 4]
if len(defines_errors) != 0:
    raise ValueError('Perms define error: {}'.format(defines_errors))


class PreRole:
    id_prefix = '00000000-0000-0000-0000-00000000000'

    def __init__(self, index, name, scope, perms, perms_type='include'):
        self.id = self.id_prefix + index
        self.name = name
        self.scope = scope
        self.perms = perms
        self.perms_type = perms_type

    def get_role(self):
        from rbac.models import Role
        return Role.objects.get(name=self.name)

    def get_defaults(self):
        from rbac.models import Permission
        q = Permission.get_define_permissions_q(self.perms)
        permissions = Permission.get_permissions(self.scope)
        if not q:
            permissions = permissions.none()
        if self.perms_type == 'include':
            permissions = permissions.filter(q)
        else:
            permissions = permissions.exclude(q)
        perms = permissions.values_list('id', flat=True)
        defaults = {
            'id': self.id, 'name': self.name, 'scope': self.scope,
            'builtin': True, 'permissions': perms
        }
        return defaults

    def get_or_create_role(self):
        from rbac.models import Role
        defaults = self.get_defaults()
        permissions = defaults.pop('permissions', [])
        role, created = Role.objects.get_or_create(defaults, id=self.id)
        role.permissions.set(permissions)
        return role, created


class BuiltinRole:
    system_admin = PreRole(
        '1', ugettext_noop('SystemAdmin'), Scope.system, []
    )
    system_auditor = PreRole(
        '2', ugettext_noop('SystemAuditor'), Scope.system, auditor_perms
    )
    system_user = PreRole(
        '3', ugettext_noop('User'), Scope.system, []
    )
    system_app = PreRole(
        '4', ugettext_noop('App'), Scope.system, app_exclude_perms, 'exclude'
    )
    org_admin = PreRole(
        '5', ugettext_noop('OrgAdmin'), Scope.org, []
    )
    org_auditor = PreRole(
        '6', ugettext_noop('OrgAuditor'), Scope.org, auditor_perms
    )
    org_user = PreRole(
        '7', ugettext_noop('OrgUser'), Scope.org, user_perms
    )

    @classmethod
    def get_roles(cls):
        roles = {
            k: v
            for k, v in cls.__dict__.items()
            if isinstance(v, PreRole)
        }
        return roles

    @classmethod
    def get_system_role_by_old_name(cls, name):
        mapper = {
            'App': cls.system_app,
            'Admin': cls.system_admin,
            'User': cls.system_user,
            'Auditor': cls.system_auditor
        }
        return mapper[name].get_role()

    @classmethod
    def get_org_role_by_old_name(cls, name):
        mapper = {
            'Admin': cls.org_admin,
            'User': cls.org_user,
            'Auditor': cls.org_auditor,
        }
        return mapper[name].get_role()

    @classmethod
    def sync_to_db(cls):
        roles = cls.get_roles()

        for pre_role in roles.values():
            role, created = pre_role.get_or_create_role()
            print("Create builtin Role: {} - {}".format(role.name, created))

