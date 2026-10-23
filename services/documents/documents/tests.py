import base64
import hashlib
import hmac
import io
import json
import shutil
import tempfile
import time
import uuid
import zipfile
from datetime import timedelta

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import Document, SessionLink

TENANT_A = 'aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa'
TENANT_B = 'bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb'
PDF_BYTES = b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n'


def make_zip(member):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr(member, '<xml/>')
    return buf.getvalue()


DOCX_BYTES = make_zip('word/document.xml')
XLSX_BYTES = make_zip('xl/workbook.xml')

_tmp_media = tempfile.mkdtemp()

# --- Authentication helpers ------------------------------------------------------
# A throwaway key pair plays the role of the separate auth service: it signs tokens
# (private key) and our service verifies them (public key), exactly as in production.
JWT_ISSUER = 'test-auth-service'
JWT_AUDIENCE = 'documents-service'


def _make_key_pair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private, public.decode()


_PRIVATE_KEY, PUBLIC_KEY = _make_key_pair()
_ATTACKER_PRIVATE_KEY, _ = _make_key_pair()


def make_jwt(tenant=None, *, key=None, algorithm='RS256', drop=(), **overrides):
    """Sign a token like the auth service would. `drop` removes claims; `overrides` change them."""
    claims = {
        'tenant_id': tenant,
        'role': 'tenant_admin',
        'exp': int(time.time()) + 300,
        'iss': JWT_ISSUER,
        'aud': JWT_AUDIENCE,
        **overrides,
    }
    for name in drop:
        claims.pop(name, None)
    return jwt.encode(claims, key or _PRIVATE_KEY, algorithm=algorithm)


def hs256_forgery(claims, secret):
    """HS256 token built by hand: PyJWT refuses to sign HS256 with a PEM key, attackers don't."""

    def b64(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b'=')

    head = b64(json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode())
    body = b64(json.dumps(claims).encode())
    sig = b64(hmac.new(secret.encode(), head + b'.' + body, hashlib.sha256).digest())
    return (head + b'.' + body + b'.' + sig).decode()


def bearer(token):
    return {'HTTP_AUTHORIZATION': f'Bearer {token}'}


def auth(tenant):
    """Extra kwargs for the test client: a valid token for `tenant`."""
    return bearer(make_jwt(tenant))


@override_settings(JWT_PUBLIC_KEY=PUBLIC_KEY, JWT_ISSUER=JWT_ISSUER, JWT_AUDIENCE=JWT_AUDIENCE)
class AuthTestCase(TestCase):
    """Base class: configures our service to trust tokens signed by the test key."""


@override_settings(MEDIA_ROOT=_tmp_media, MAX_UPLOAD_SIZE_BYTES=1024 * 1024)
class DocumentUploadTests(AuthTestCase):
    url = '/api/documents/'

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_tmp_media, ignore_errors=True)

    def upload(self, name, content, tenant=TENANT_A, **extra):
        data = {'file': SimpleUploadedFile(name, content), **extra}
        return self.client.post(self.url, data, **auth(tenant))

    def test_allowed_types_are_accepted_with_canonical_content_type(self):
        cases = {
            'a.pdf': (PDF_BYTES, 'application/pdf'),
            'a.docx': (
                DOCX_BYTES,
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            ),
            'a.xlsx': (
                XLSX_BYTES,
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            ),
            'a.csv': (b'x,y\n1,2\n', 'text/csv'),
            'a.txt': ('héllo'.encode(), 'text/plain'),
        }
        for name, (content, content_type) in cases.items():
            with self.subTest(name=name):
                resp = self.upload(name, content)
                self.assertEqual(resp.status_code, 201, resp.content)
                self.assertEqual(resp.json()['content_type'], content_type)
                self.assertEqual(resp.json()['ingestion_status'], 'pending')
                self.assertEqual(resp.json()['file_size'], len(content))

    def test_file_is_stored_under_tenant_and_document_id(self):
        resp = self.upload('policy.pdf', PDF_BYTES)
        doc = Document.objects.get(pk=resp.json()['id'])
        self.assertEqual(doc.file.name, f'tenants/{TENANT_A}/{doc.id}/policy.pdf')

    def test_rejects_disallowed_extension(self):
        resp = self.upload('run.exe', b'MZ\x90\x00')
        self.assertEqual(resp.status_code, 400)

    def test_rejects_content_that_does_not_match_extension(self):
        for name in ('fake.pdf', 'fake.docx', 'fake.xlsx', 'bin.txt'):
            with self.subTest(name=name):
                content = b'\x00\x01\x02binary' if name == 'bin.txt' else b'not what it claims'
                self.assertEqual(self.upload(name, content).status_code, 400)

    def test_xlsx_content_is_not_accepted_as_docx(self):
        self.assertEqual(self.upload('sheet.docx', XLSX_BYTES).status_code, 400)

    def test_rejects_empty_and_oversized(self):
        self.assertEqual(self.upload('e.txt', b'').status_code, 400)
        self.assertEqual(self.upload('big.txt', b'a' * (1024 * 1024 + 1)).status_code, 400)

    def test_tenant_comes_from_token_not_from_the_request(self):
        resp = self.upload('a.txt', b'hi', tenant_id=TENANT_B)  # body tries to pick another tenant
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()['tenant_id'], TENANT_A)
        self.assertEqual(Document.objects.get().tenant_id, uuid.UUID(TENANT_A))

    def test_long_filename_exceeding_default_100_char_path_is_stored(self):
        name = 'n' * 240 + '.txt'
        resp = self.upload(name, b'hello')
        self.assertEqual(resp.status_code, 201, resp.content)
        doc = Document.objects.get(pk=resp.json()['id'])
        self.assertGreater(len(doc.file.name), 100)
        self.assertEqual(doc.original_filename, name)

    def test_client_cannot_set_server_controlled_fields(self):
        resp = self.client.post(
            self.url,
            {
                'file': SimpleUploadedFile('a.txt', b'hi'),
                'ingestion_status': 'completed',
                'content_type': 'evil/type',
                'id': str(uuid.uuid4()),
            },
            **auth(TENANT_A),
        )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body['ingestion_status'], 'pending')
        self.assertEqual(body['content_type'], 'text/plain')


@override_settings(MEDIA_ROOT=_tmp_media, MAX_UPLOAD_SIZE_BYTES=1024 * 1024)
class DocumentReadTests(AuthTestCase):
    """List/detail scoping. The key property: one tenant never sees another's data."""

    list_url = '/api/documents/'

    @classmethod
    def setUpTestData(cls):
        def make(tenant, name):
            return Document.objects.create(
                tenant_id=tenant,
                file=SimpleUploadedFile(name, b'hi'),
                original_filename=name,
                content_type='text/plain',
                file_size=2,
            )

        cls.a1 = make(TENANT_A, 'a1.txt')
        cls.a2 = make(TENANT_A, 'a2.txt')
        cls.b1 = make(TENANT_B, 'b1.txt')

    def names(self, tenant):
        resp = self.client.get(self.list_url, **auth(tenant))
        self.assertEqual(resp.status_code, 200)
        return sorted(d['original_filename'] for d in resp.json()['results'])

    def test_list_returns_only_the_requesting_tenants_documents(self):
        self.assertEqual(self.names(TENANT_A), ['a1.txt', 'a2.txt'])
        self.assertEqual(self.names(TENANT_B), ['b1.txt'])

    def test_query_string_tenant_id_is_ignored(self):
        resp = self.client.get(self.list_url, {'tenant_id': TENANT_B}, **auth(TENANT_A))
        names = sorted(d['original_filename'] for d in resp.json()['results'])
        self.assertEqual(names, ['a1.txt', 'a2.txt'])

    def test_list_for_unknown_tenant_is_empty(self):
        self.assertEqual(self.names(str(uuid.uuid4())), [])

    def test_list_includes_status_and_never_exposes_file_path(self):
        body = self.client.get(self.list_url, **auth(TENANT_A)).json()
        for doc in body['results']:
            self.assertEqual(doc['ingestion_status'], 'pending')
            self.assertNotIn('file', doc)

    def test_list_is_newest_first(self):
        results = self.client.get(self.list_url, **auth(TENANT_A)).json()['results']
        self.assertEqual([d['original_filename'] for d in results], ['a2.txt', 'a1.txt'])

    def test_detail_returns_own_document(self):
        resp = self.client.get(f'{self.list_url}{self.a1.id}/', **auth(TENANT_A))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['original_filename'], 'a1.txt')
        self.assertEqual(resp.json()['ingestion_status'], 'pending')

    def test_detail_of_another_tenants_document_is_404(self):
        resp = self.client.get(f'{self.list_url}{self.b1.id}/', **auth(TENANT_A))
        self.assertEqual(resp.status_code, 404)
        # Identical to a document that does not exist, so existence is not revealed.
        missing = self.client.get(f'{self.list_url}{uuid.uuid4()}/', **auth(TENANT_A))
        self.assertEqual(resp.json(), missing.json())

    def test_uploads_appear_in_the_right_tenants_list(self):
        self.client.post(
            self.list_url, {'file': SimpleUploadedFile('new.txt', b'x')}, **auth(TENANT_B)
        )
        self.assertEqual(self.names(TENANT_B), ['b1.txt', 'new.txt'])
        self.assertEqual(self.names(TENANT_A), ['a1.txt', 'a2.txt'])


class HealthTests(TestCase):
    def test_health_ok_and_does_no_database_queries(self):
        with self.assertNumQueries(0):
            resp = self.client.get('/health/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {'status': 'ok'})

    def test_health_needs_no_token(self):
        # Not an AuthTestCase: no key configured, no Authorization header, still 200.
        self.assertEqual(self.client.get('/health/').status_code, 200)

    def test_health_rejects_non_get(self):
        self.assertEqual(self.client.post('/health/').status_code, 405)


@override_settings(MEDIA_ROOT=_tmp_media)
class SessionLinkTests(AuthTestCase):
    url = '/api/session-links/'

    def create(self, tenant=TENANT_A, **extra):
        return self.client.post(self.url, extra, content_type='application/json', **auth(tenant))

    def test_generate_returns_secure_token_and_defaults(self):
        resp = self.create()
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        self.assertEqual(body['tenant_id'], TENANT_A)
        self.assertTrue(body['is_active'])
        self.assertIsNone(body['expires_at'])
        self.assertGreaterEqual(len(body['token']), 43)
        self.assertRegex(body['token'], r'^[A-Za-z0-9_-]+$')  # URL-safe

    def test_tokens_are_unique(self):
        tokens = {self.create().json()['token'] for _ in range(25)}
        self.assertEqual(len(tokens), 25)

    def test_future_expiry_accepted_past_rejected(self):
        future = (timezone.now() + timedelta(days=7)).isoformat()
        past = (timezone.now() - timedelta(minutes=1)).isoformat()
        self.assertEqual(self.create(expires_at=future).status_code, 201)
        self.assertEqual(self.create(expires_at=past).status_code, 400)

    def test_client_cannot_set_token_or_active_flag(self):
        resp = self.create(token='attacker-chosen', is_active=False)
        self.assertEqual(resp.status_code, 201)
        self.assertNotEqual(resp.json()['token'], 'attacker-chosen')
        self.assertTrue(resp.json()['is_active'])

    def test_tenant_comes_from_token_not_from_the_request(self):
        resp = self.create(TENANT_A, tenant_id=TENANT_B)  # body tries to pick another tenant
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()['tenant_id'], TENANT_A)
        self.assertEqual(SessionLink.objects.get().tenant_id, uuid.UUID(TENANT_A))

    def test_list_is_scoped_to_tenant_and_newest_first(self):
        a1 = self.create(TENANT_A).json()['id']
        a2 = self.create(TENANT_A).json()['id']
        b1 = self.create(TENANT_B).json()['id']

        def ids(t):
            return [x['id'] for x in self.client.get(self.url, **auth(t)).json()['results']]

        self.assertEqual(ids(TENANT_A), [a2, a1])
        self.assertEqual(ids(TENANT_B), [b1])
        self.assertEqual(ids(str(uuid.uuid4())), [])

    def test_list_never_shows_another_tenants_secret_tokens(self):
        secret = self.create(TENANT_A).json()['token']
        self.assertNotIn(secret, self.client.get(self.url, **auth(TENANT_B)).content.decode())
        # Asking for tenant A through the query string doesn't help either.
        resp = self.client.get(self.url, {'tenant_id': TENANT_A}, **auth(TENANT_B))
        self.assertNotIn(secret, resp.content.decode())

    def test_deactivate_turns_link_off_and_is_idempotent(self):
        link = self.create().json()
        url = f'{self.url}{link["id"]}/deactivate/'
        for _ in range(2):
            resp = self.client.post(url, **auth(TENANT_A))
            self.assertEqual(resp.status_code, 200, resp.content)
            self.assertFalse(resp.json()['is_active'])
        self.assertFalse(SessionLink.objects.get(pk=link['id']).is_active)

    def test_deactivate_other_tenants_or_unknown_link_is_404(self):
        link = self.create(TENANT_A).json()
        url = f'{self.url}{link["id"]}/deactivate/'
        self.assertEqual(self.client.post(url, **auth(TENANT_B)).status_code, 404)
        self.assertTrue(SessionLink.objects.get(pk=link['id']).is_active)
        unknown = f'{self.url}{uuid.uuid4()}/deactivate/'
        self.assertEqual(self.client.post(unknown, **auth(TENANT_A)).status_code, 404)

    @override_settings(SESSION_LINK_BASE_URL='https://app.example.com/s/')
    def test_response_includes_shareable_url_built_from_setting(self):
        body = self.create().json()
        self.assertEqual(body['url'], f'https://app.example.com/s/{body["token"]}')
        listed = self.client.get(self.url, **auth(TENANT_A)).json()['results'][0]
        self.assertEqual(listed['url'], body['url'])

    def test_client_cannot_set_url(self):
        body = self.create(url='https://evil.example/x').json()
        self.assertTrue(body['url'].endswith(body['token']))
        self.assertNotIn('evil', body['url'])


@override_settings(MEDIA_ROOT=_tmp_media)
class AuthenticationTests(AuthTestCase):
    """Who gets in. Every protected endpoint must refuse anything but a valid token."""

    @classmethod
    def setUpTestData(cls):
        cls.doc = Document.objects.create(
            tenant_id=TENANT_A,
            file=SimpleUploadedFile('a.txt', b'hi'),
            original_filename='a.txt',
            content_type='text/plain',
            file_size=2,
        )
        cls.link = SessionLink.objects.create(tenant_id=TENANT_A)

    def calls(self):
        """Every protected endpoint as (method, url)."""
        return [
            ('get', '/api/documents/'),
            ('post', '/api/documents/'),
            ('get', f'/api/documents/{self.doc.id}/'),
            ('get', '/api/session-links/'),
            ('post', '/api/session-links/'),
            ('post', f'/api/session-links/{self.link.id}/deactivate/'),
        ]

    def assert_all_refused(self, **headers):
        for method, url in self.calls():
            with self.subTest(endpoint=f'{method.upper()} {url}'):
                resp = getattr(self.client, method)(url, **headers)
                self.assertEqual(resp.status_code, 401, resp.content)
                self.assertEqual(resp.headers['WWW-Authenticate'], 'Bearer')

    def test_no_token_is_refused_everywhere(self):
        self.assert_all_refused()

    def test_old_style_tenant_id_without_token_is_refused(self):
        for method, url in self.calls():
            with self.subTest(endpoint=f'{method.upper()} {url}'):
                resp = getattr(self.client, method)(f'{url}?tenant_id={TENANT_A}')
                self.assertEqual(resp.status_code, 401)

    def test_valid_token_is_accepted(self):
        resp = self.client.get('/api/documents/', **auth(TENANT_A))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['count'], 1)

    def test_bad_tokens_are_refused_everywhere(self):
        now = int(time.time())
        claims = {
            'tenant_id': TENANT_A,
            'role': 'tenant_admin',
            'exp': now + 300,
            'iss': JWT_ISSUER,
            'aud': JWT_AUDIENCE,
        }
        bad = {
            'expired': make_jwt(TENANT_A, exp=now - 10),
            'signed by a different key (forged)': make_jwt(TENANT_A, key=_ATTACKER_PRIVATE_KEY),
            'wrong issuer': make_jwt(TENANT_A, iss='someone-else'),
            'wrong audience': make_jwt(TENANT_A, aud='another-service'),
            'no issuer': make_jwt(TENANT_A, drop=['iss']),
            'no audience': make_jwt(TENANT_A, drop=['aud']),
            'no expiry': make_jwt(TENANT_A, drop=['exp']),
            'no tenant': make_jwt(TENANT_A, drop=['tenant_id']),
            'tenant is not a UUID': make_jwt('not-a-uuid'),
            'tenant is empty': make_jwt(''),
            'HS256 signed with the public key': hs256_forgery(claims, PUBLIC_KEY),
            # Deliberate: an unsigned 'none' token, to prove the service refuses it.
            # nosemgrep: python.jwt.security.jwt-none-alg.jwt-python-none-alg
            'algorithm none': jwt.encode(claims, None, algorithm='none'),
            'garbage': 'abc.def.ghi',
        }
        for label, token in bad.items():
            with self.subTest(token=label):
                self.assert_all_refused(**bearer(token))

    def test_malformed_authorization_headers_are_refused(self):
        token = make_jwt(TENANT_A)
        for label, header in {
            'wrong scheme': f'Basic {token}',
            'no scheme': token,
            'extra part': f'Bearer {token} extra',
            'scheme only': 'Bearer',
        }.items():
            with self.subTest(header=label):
                self.assert_all_refused(HTTP_AUTHORIZATION=header)

    @override_settings(JWT_PUBLIC_KEY='')
    def test_service_refuses_everything_when_no_key_is_configured(self):
        # Fail closed: a missing key must never mean "let everyone in".
        with self.assertLogs('documents.authentication', level='ERROR') as logs:
            self.assert_all_refused(**auth(TENANT_A))
        self.assertIn('JWT_PUBLIC_KEY is not configured', logs.output[0])

    @override_settings(JWT_TENANT_CLAIM='tid')
    def test_tenant_claim_name_is_configurable(self):
        ok = bearer(make_jwt(None, tid=TENANT_A, drop=['tenant_id']))
        self.assertEqual(self.client.get('/api/documents/', **ok).status_code, 200)
        # A token that only has the default claim name is no longer enough.
        self.assertEqual(self.client.get('/api/documents/', **auth(TENANT_A)).status_code, 401)

    def assert_all_forbidden(self, **headers):
        for method, url in self.calls():
            with self.subTest(endpoint=f'{method.upper()} {url}'):
                resp = getattr(self.client, method)(url, **headers)
                self.assertEqual(resp.status_code, 403, resp.content)

    def test_other_roles_are_forbidden_everywhere(self):
        # platform_admin tokens carry no tenant at all (tenant_id is null).
        self.assert_all_forbidden(**bearer(make_jwt(None, role='platform_admin')))
        # A platform_admin token that does carry a tenant is still not allowed.
        self.assert_all_forbidden(**bearer(make_jwt(TENANT_A, role='platform_admin')))
        self.assert_all_forbidden(**bearer(make_jwt(TENANT_A, role='viewer')))

    def test_token_without_a_role_is_forbidden(self):
        self.assert_all_forbidden(**bearer(make_jwt(TENANT_A, drop=['role'])))

    def test_forged_token_is_401_not_403(self):
        # The role is only looked at after the signature is proven valid.
        forged = make_jwt(TENANT_A, role='platform_admin', key=_ATTACKER_PRIVATE_KEY)
        self.assert_all_refused(**bearer(forged))

    @override_settings(JWT_ALLOWED_ROLES=['tenant_admin', 'tenant_member'])
    def test_allowed_roles_are_configurable(self):
        ok = bearer(make_jwt(TENANT_A, role='tenant_member'))
        self.assertEqual(self.client.get('/api/documents/', **ok).status_code, 200)
        no = bearer(make_jwt(TENANT_A, role='platform_admin'))
        self.assertEqual(self.client.get('/api/documents/', **no).status_code, 403)

    @override_settings(JWT_ALLOWED_ROLES=[])
    def test_empty_allowed_roles_skips_the_role_check(self):
        token = bearer(make_jwt(TENANT_A, drop=['role']))
        self.assertEqual(self.client.get('/api/documents/', **token).status_code, 200)

    @override_settings(JWT_ROLE_CLAIM='r')
    def test_role_claim_name_is_configurable(self):
        ok = bearer(make_jwt(TENANT_A, r='tenant_admin', drop=['role']))
        self.assertEqual(self.client.get('/api/documents/', **ok).status_code, 200)
        self.assertEqual(self.client.get('/api/documents/', **auth(TENANT_A)).status_code, 403)

    def test_a_tenant_cannot_reach_another_tenants_data(self):
        b = auth(TENANT_B)
        self.assertEqual(self.client.get('/api/documents/', **b).json()['count'], 0)
        self.assertEqual(self.client.get(f'/api/documents/{self.doc.id}/', **b).status_code, 404)
        self.assertEqual(self.client.get('/api/session-links/', **b).json()['count'], 0)
        self.assertEqual(
            self.client.post(f'/api/session-links/{self.link.id}/deactivate/', **b).status_code, 404
        )
        self.assertTrue(SessionLink.objects.get(pk=self.link.id).is_active)


@override_settings(CORS_ALLOWED_ORIGINS=['http://localhost:3000'])
class CorsTests(AuthTestCase):
    """Browser frontends on listed origins may call the API; everyone else may not."""

    def preflight(self, origin):
        return self.client.options(
            '/api/documents/',
            HTTP_ORIGIN=origin,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST',
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS='authorization',
        )

    def test_allowed_origin_passes_preflight_and_may_send_authorization(self):
        resp = self.preflight('http://localhost:3000')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers['Access-Control-Allow-Origin'], 'http://localhost:3000')
        self.assertIn('authorization', resp.headers['Access-Control-Allow-Headers'].lower())

    def test_unlisted_origin_gets_no_cors_headers(self):
        resp = self.preflight('http://evil.example.com')
        self.assertNotIn('Access-Control-Allow-Origin', resp.headers)

    def test_real_response_carries_cors_header_for_allowed_origin(self):
        resp = self.client.get(
            '/api/documents/', HTTP_ORIGIN='http://localhost:3000', **auth(TENANT_A)
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers['Access-Control-Allow-Origin'], 'http://localhost:3000')

    def test_cookies_are_not_allowed_cross_origin(self):
        resp = self.preflight('http://localhost:3000')
        self.assertNotIn('Access-Control-Allow-Credentials', resp.headers)

    @override_settings(CORS_ALLOWED_ORIGINS=[])
    def test_no_origins_configured_means_no_cors(self):
        resp = self.preflight('http://localhost:3000')
        self.assertNotIn('Access-Control-Allow-Origin', resp.headers)
