from django.shortcuts import get_object_or_404
from rest_framework import generics
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Document, SessionLink
from .serializers import DocumentSerializer, SessionLinkSerializer


class DocumentPagination(LimitOffsetPagination):
    default_limit = 50
    max_limit = 200


def get_tenant_id(request):
    """The calling tenant, taken from the verified JWT (see authentication.py).

    Never read from the query string or body: a client cannot choose its tenant.
    """
    return request.user.tenant_id


class DocumentListCreateView(generics.ListCreateAPIView):
    """GET  /api/documents/  -> the caller's documents, newest first
    POST /api/documents/  (multipart/form-data: file) -> upload

    Requires `Authorization: Bearer <jwt>`; the tenant comes from the token.
    """

    serializer_class = DocumentSerializer
    pagination_class = DocumentPagination
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        return Document.objects.filter(tenant_id=get_tenant_id(self.request))

    def perform_create(self, serializer):
        serializer.save(tenant_id=get_tenant_id(self.request))


class DocumentDetailView(generics.RetrieveAPIView):
    """GET /api/documents/<id>/

    Scoped to the caller's tenant: another tenant's document returns 404, exactly
    like a document that doesn't exist, so its existence is not revealed (NFR-06).
    """

    serializer_class = DocumentSerializer

    def get_queryset(self):
        return Document.objects.filter(tenant_id=get_tenant_id(self.request))


class SessionLinkListCreateView(generics.ListCreateAPIView):
    """GET  /api/session-links/  -> the caller's links, newest first
    POST /api/session-links/  (JSON: optional expires_at) -> generate one

    Requires `Authorization: Bearer <jwt>`; the tenant comes from the token, so a
    caller only ever sees (and can only create) its own tenant's links and tokens.
    """

    serializer_class = SessionLinkSerializer
    pagination_class = DocumentPagination
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        return SessionLink.objects.filter(tenant_id=get_tenant_id(self.request))

    def perform_create(self, serializer):
        serializer.save(tenant_id=get_tenant_id(self.request))


class SessionLinkDeactivateView(APIView):
    """POST /api/session-links/<id>/deactivate/

    Switches the link off so its token no longer starts sessions. Idempotent:
    deactivating an already-inactive link succeeds. Another tenant's link returns
    404, exactly like one that doesn't exist (NFR-06).
    """

    def post(self, request, pk):
        link = get_object_or_404(SessionLink, pk=pk, tenant_id=get_tenant_id(request))
        if link.is_active:
            link.is_active = False
            link.save(update_fields=['is_active'])
        return Response(SessionLinkSerializer(link).data)
