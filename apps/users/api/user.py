# ~*~ coding: utf-8 ~*~
from collections import defaultdict
from django.utils.translation import ugettext as _
from rest_framework.decorators import action
from rest_framework import generics
from rest_framework.response import Response
from rest_framework_bulk import BulkModelViewSet

from users.notifications import ResetMFAMsg
from common.permissions import IsOrgAdmin
from common.mixins import CommonApiMixin
from common.utils import get_logger
from orgs.utils import current_org
from users.utils import LoginBlockUtil, MFABlockUtils
from .mixins import UserQuerysetMixin
from .. import serializers
from ..serializers import (
    UserSerializer, UserRetrieveSerializer,
    MiniUserSerializer, InviteSerializer
)
from ..models import User
from ..signals import post_user_create
from ..filters import UserFilter
from rbac.models import Role, RoleBinding


logger = get_logger(__name__)
__all__ = [
    'UserViewSet', 'UserChangePasswordApi',
    'UserUnblockPKApi', 'UserResetOTPApi',
]


class UserViewSet(CommonApiMixin, UserQuerysetMixin, BulkModelViewSet):
    filterset_class = UserFilter
    search_fields = ('username', 'email', 'name', 'id', 'source', 'role')
    serializer_classes = {
        'default': UserSerializer,
        'retrieve': UserRetrieveSerializer,
        'suggestion': MiniUserSerializer,
        'invite': InviteSerializer,
    }

    def get_queryset(self):
        queryset = super().get_queryset().prefetch_related('groups')
        return queryset

    @staticmethod
    def set_roles(queryset):
        # Todo: 未来有机会用 SQL 实现
        queryset_list = list(queryset)
        user_ids = [u.id for u in queryset_list]
        role_bindings = RoleBinding.objects.filter(user__in=user_ids) \
            .values('user_id', 'role_id', 'scope')

        role_mapper = {r.id: r for r in Role.objects.all()}
        user_org_role_mapper = defaultdict(set)
        user_system_role_mapper = defaultdict(set)

        for binding in role_bindings:
            role_id = binding['role_id']
            user_id = binding['user_id']
            if binding['scope'] == RoleBinding.Scope.system:
                user_system_role_mapper[user_id].add(role_mapper[role_id])
            else:
                user_org_role_mapper[user_id].add(role_mapper[role_id])

        for u in queryset_list:
            system_roles = user_system_role_mapper[u.id]
            org_roles = user_org_role_mapper[u.id]
            u.roles.cache_set(system_roles | org_roles)
            u.org_roles.cache_set(org_roles)
            u.system_roles.cache_set(system_roles)
        return queryset_list

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        queryset_list = self.set_roles(queryset)
        return queryset_list

    def perform_create(self, serializer):
        users = serializer.save()
        # system_roles, org_roles = self.get_serializer_roles(serializer)
        if isinstance(users, User):
            users = [users]
        # self.add_users_to_org(users)
        # self.set_users_roles(users, system_roles, org_roles)
        self.send_created_signal(users)

    def perform_update(self, serializer):
        users = serializer.save()
        # system_roles, org_roles = self.get_serializer_roles(serializer)
        if isinstance(users, User):
            users = [users]
        # self.add_users_to_org(users)
        # self.set_users_roles(users, system_roles, org_roles, update=True)

    def perform_bulk_update(self, serializer):
        user_ids = [
            d.get("id") or d.get("pk") for d in serializer.validated_data
        ]
        users = current_org.get_members().filter(id__in=user_ids)
        for user in users:
            self.check_object_permissions(self.request, user)
        return super().perform_bulk_update(serializer)

    def perform_bulk_destroy(self, objects):
        for obj in objects:
            self.check_object_permissions(self.request, obj)
            self.perform_destroy(obj)

    # def get_permissions(self):
    #     return []
        # if self.action in ["retrieve", "list"]:
        #     if self.request.query_params.get('all'):
        #         self.#     else:
        #         self.permission_classes = (IsOrgAdminOrAppUser,)
        # elif self.action in ['destroy']:
        #     self.return super().get_permissions()

    @action(methods=['get'], detail=False, permission_classes=(IsOrgAdmin,))
    def suggestion(self, request):
        # Todo: Role 这里有问题
        queryset = User.objects.exclude(role=User.ROLE.APP)
        queryset = self.filter_queryset(queryset)[:3]
        queryset = self.filter_queryset(queryset)
        queryset = queryset[:3]

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(methods=['post'], detail=False, permission_classes=(IsOrgAdmin,))
    def invite(self, request):
        data = request.data
        if not isinstance(data, list):
            data = [request.data]
        if not current_org or current_org.is_root():
            error = {"error": "Not a valid org"}
            return Response(error, status=400)

        serializer_cls = self.get_serializer_class()
        serializer = serializer_cls(data=data, many=True)
        serializer.is_valid(raise_exception=True)
        validated_data = serializer.validated_data

        users = [data['user'] for data in validated_data]
        org_roles = self.get_serializer_roles(serializer)

        self.add_users_to_org(users)
        self.set_users_roles(users, org_roles=org_roles)

        return Response(serializer.data, status=201)

    @action(methods=['post'], detail=True, permission_classes=(IsOrgAdmin,))
    def remove(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.remove()
        return Response(status=204)

    @action(methods=['post'], detail=False, permission_classes=(IsOrgAdmin,), url_path='remove')
    def bulk_remove(self, request, *args, **kwargs):
        qs = self.get_queryset()
        filtered = self.filter_queryset(qs)

        for instance in filtered:
            instance.remove()
        return Response(status=204)

    def send_created_signal(self, users):
        if not isinstance(users, list):
            users = [users]
        for user in users:
            post_user_create.send(self.__class__, user=user)


class UserChangePasswordApi(UserQuerysetMixin, generics.UpdateAPIView):
    serializer_class = serializers.ChangeUserPasswordSerializer

    def perform_update(self, serializer):
        user = self.get_object()
        user.password_raw = serializer.validated_data["password"]
        user.save()


class UserUnblockPKApi(UserQuerysetMixin, generics.UpdateAPIView):
    serializer_class = serializers.UserSerializer

    def perform_update(self, serializer):
        user = self.get_object()
        username = user.username if user else ''
        LoginBlockUtil.unblock_user(username)
        MFABlockUtils.unblock_user(username)


class UserResetOTPApi(UserQuerysetMixin, generics.RetrieveAPIView):
    serializer_class = serializers.ResetOTPSerializer

    def retrieve(self, request, *args, **kwargs):
        user = self.get_object() if kwargs.get('pk') else request.user
        if user == request.user:
            msg = _("Could not reset self otp, use profile reset instead")
            return Response({"error": msg}, status=401)
        if user.mfa_enabled:
            user.reset_mfa()
            user.save()

            ResetMFAMsg(user).publish_async()
        return Response({"msg": "success"})
