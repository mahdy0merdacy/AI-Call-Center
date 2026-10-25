from django.conf import settings
from django.utils import timezone
from rest_framework import serializers

from .models import Document, SessionLink
from .validators import validate_upload


class DocumentSerializer(serializers.ModelSerializer):
    """Input for POST /api/documents/; output for upload, list and detail.

    Only `file` is accepted from the client. The tenant comes from the caller's
    token (the view passes it to save()); everything else (filename, content type,
    size, status) is derived or defaulted server-side.
    `file` is write-only: the storage path is never exposed.
    """

    file = serializers.FileField(write_only=True)

    class Meta:
        model = Document
        fields = [
            'id',
            'tenant_id',
            'file',
            'original_filename',
            'content_type',
            'file_size',
            'ingestion_status',
            'error_message',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'tenant_id',
            'original_filename',
            'content_type',
            'file_size',
            'ingestion_status',
            'error_message',
            'created_at',
            'updated_at',
        ]

    def validate_file(self, uploaded_file):
        self._original_filename, self._content_type = validate_upload(uploaded_file)
        return uploaded_file

    def create(self, validated_data):
        uploaded_file = validated_data['file']
        # The Document id (UUID) exists as soon as the instance is built, so
        # upload_to can use it for the storage path when the file is saved.
        return Document.objects.create(
            tenant_id=validated_data['tenant_id'],
            file=uploaded_file,
            original_filename=self._original_filename,
            content_type=self._content_type,
            file_size=uploaded_file.size,
        )


class SessionLinkSerializer(serializers.ModelSerializer):
    """Input for POST /api/session-links/; output for generate and list.

    The client supplies only an optional `expires_at`. The tenant comes from the
    caller's token; the token, active flag and timestamps are set server-side. `url` is
    the shareable address, computed from SESSION_LINK_BASE_URL on every read (never stored).
    """

    url = serializers.SerializerMethodField()

    class Meta:
        model = SessionLink
        fields = ['id', 'tenant_id', 'token', 'url', 'is_active', 'expires_at', 'created_at']
        read_only_fields = ['id', 'tenant_id', 'token', 'url', 'is_active', 'created_at']

    def get_url(self, obj):
        return f'{settings.SESSION_LINK_BASE_URL.rstrip("/")}/{obj.token}'

    def validate_expires_at(self, value):
        if value is not None and value <= timezone.now():
            raise serializers.ValidationError('expires_at must be in the future.')
        return value
