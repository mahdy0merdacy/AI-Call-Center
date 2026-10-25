"""Platform-admin tenant management and the tenant's own endpoints."""

import logging

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import generics
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Tenant
from accounts.permissions import HasTenant, IsPlatformAdmin
from accounts.serializers import TenantSerializer

logger = logging.getLogger(__name__)


@extend_schema(tags=["platform-admin"])
class AdminTenantListView(generics.ListAPIView):
    """All tenants (platform admins only)."""

    permission_classes = [IsPlatformAdmin]
    serializer_class = TenantSerializer
    queryset = Tenant.objects.order_by("created_at")


class AdminTenantSuspendView(APIView):
    permission_classes = [IsPlatformAdmin]

    @extend_schema(request=None, responses={200: TenantSerializer}, tags=["platform-admin"])
    def post(self, request: Request, tenant_id: str) -> Response:
        """Suspend a tenant: its users can no longer log in, refresh or call the API."""
        tenant = get_object_or_404(Tenant, pk=tenant_id)
        if tenant.is_active:
            tenant.is_active = False
            tenant.save(update_fields=["is_active", "updated_at"])
            logger.info(
                "tenant suspended",
                extra={"tenant_id": str(tenant.pk), "by_user_id": str(request.user.pk)},
            )
        return Response(TenantSerializer(tenant).data)


class TenantMeView(APIView):
    permission_classes = [HasTenant]

    @extend_schema(responses={200: TenantSerializer}, tags=["tenant"])
    def get(self, request: Request) -> Response:
        """The caller's own tenant (resolved from the token's user, never input)."""
        return Response(TenantSerializer(request.user.tenant).data)
