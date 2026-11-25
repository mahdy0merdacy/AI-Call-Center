"""Reusable building blocks for tenant-scoped API views."""

from django.db.models import Model, QuerySet
from rest_framework.serializers import BaseSerializer

from accounts.permissions import HasTenant


class TenantScopedQuerysetMixin:
    """Restrict a DRF generic view to the caller's own tenant.

    The tenant always comes from the authenticated user
    (`request.user.tenant_id`), never from the request body or URL, so a
    tenant cannot read or write another tenant's rows by changing ids.

    Usage::

        class DocumentViewSet(TenantScopedQuerysetMixin, viewsets.ModelViewSet):
            queryset = Document.objects.all()
            serializer_class = DocumentSerializer

    Objects of other tenants are filtered out of the queryset, so detail
    lookups for them return 404 (not 403), which does not leak existence.
    """

    #: Name of the ForeignKey to Tenant on the model.
    tenant_field = "tenant"
    permission_classes = [HasTenant]

    def get_queryset(self) -> QuerySet[Model]:
        queryset = super().get_queryset()  # type: ignore[misc]
        tenant_id = self.request.user.tenant_id  # type: ignore[attr-defined]
        if tenant_id is None:
            return queryset.none()
        return queryset.filter(**{self.tenant_field: tenant_id})

    def perform_create(self, serializer: BaseSerializer) -> None:
        """Force the tenant on create, ignoring any client-supplied value."""
        serializer.save(**{self.tenant_field: self.request.user.tenant})  # type: ignore[attr-defined]
