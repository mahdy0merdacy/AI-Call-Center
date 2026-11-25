import os
import secrets
import uuid

from django.db import models


def document_upload_path(instance, filename):
    """Namespace stored files by tenant and document: tenants/<tenant_id>/<doc_id>/<name>.

    `instance.id` is a UUID assigned in Python at instantiation, so it is already
    available here, before the row is saved.
    """
    # Keep only the final path component; never trust directories in a client filename.
    safe_name = os.path.basename(filename)
    return f'tenants/{instance.tenant_id}/{instance.id}/{safe_name}'


class Document(models.Model):
    class IngestionStatus(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PROCESSING = 'processing', 'Processing'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # There is no Tenant model in this service: tenants are owned by a separate
    # microservice, so this is a plain UUID and deliberately not a ForeignKey.
    # No db_index here: the (tenant_id, -created_at) index in Meta already serves
    # lookups by tenant_id alone.
    tenant_id = models.UUIDField()

    # max_length is set well above Django's default of 100: the path is
    # "tenants/<36-char uuid>/<36-char uuid>/" (82 chars) plus the filename, which
    # alone can be up to 255 chars.
    # NOTE: files are written to local container disk (default storage). This
    # conflicts with the spec's stateless-services requirement (NFR-01). Known,
    # accepted gap for now; move to object storage in a later task.
    file = models.FileField(upload_to=document_upload_path, max_length=512)

    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=255)
    file_size = models.PositiveBigIntegerField(help_text='Size in bytes.')

    ingestion_status = models.CharField(
        max_length=20,
        choices=IngestionStatus.choices,
        default=IngestionStatus.PENDING,
        db_index=True,
    )
    # Populated when ingestion_status == FAILED (spec FR-03: per-document errors).
    error_message = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            # Serves the main query: "list this tenant's documents, newest first".
            models.Index(fields=['tenant_id', '-created_at']),
        ]

    def __str__(self):
        return f'{self.original_filename} ({self.ingestion_status})'


def generate_session_token():
    """A URL-safe, cryptographically secure random token (256 bits, 43 chars)."""
    return secrets.token_urlsafe(32)


class SessionLink(models.Model):
    """A shareable link a tenant gives to its own customers (spec FR-04).

    The token is the link's only secret: anyone holding it can start a session for
    the tenant, so it is generated with `secrets` (never sequential or guessable)
    and is unique. Customers then use it without registering (FR-06); validating
    the token at session start happens in another service.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Plain UUID, not a ForeignKey: tenants are owned by a separate microservice.
    # Indexed via the composite index in Meta, so no separate db_index.
    tenant_id = models.UUIDField()

    token = models.CharField(
        max_length=64, unique=True, default=generate_session_token, editable=False
    )
    is_active = models.BooleanField(default=True)
    # Null means the link never expires.
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant_id', '-created_at']),
        ]

    def __str__(self):
        return f'SessionLink {self.id} (tenant {self.tenant_id})'
