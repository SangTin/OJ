import base64
import hashlib
import json
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve, reverse
from django.utils import timezone
from oauth2_provider.models import get_access_token_model, get_application_model, get_grant_model, \
    get_refresh_token_model

from judge.mcp_auth import MCPOAuthValidator, authenticate, register
from judge.models import Contest, Problem
from judge.models.tests.util import create_contest, create_problem, create_user


@override_settings(MCP_ENABLED=True, MCP_RESOURCE_URL='http://testserver/mcp', MCP_ALLOW_LOOPBACK_REDIRECTS=False)
class MCPAuthTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.user = create_user(username='mcpowner')
        self.application = get_application_model().objects.create(
            name='MCP', client_type='public', authorization_grant_type='authorization-code',
            redirect_uris='https://claude.ai/api/mcp/auth_callback', registration_source='dcr',
        )
        self.token = get_access_token_model().objects.create(
            application=self.application, user=self.user, token='mcp-test-token', scope='mcp',
            resource=['http://testserver/mcp'], expires=timezone.now() + timedelta(hours=1),
        )

    def request(self, token='mcp-test-token'):
        return self.factory.post('/mcp', '{}', content_type='application/json',
                                 HTTP_AUTHORIZATION='Bearer ' + token)

    def test_valid_token(self):
        user, response = authenticate(self.request())
        self.assertIsNone(response)
        self.assertEqual(user.pk, self.user.pk)

    def test_scope_resource_expiry_and_revocation(self):
        for field, value in [('scope', 'profile'), ('resource', []),
                             ('resource', ['http://testserver/']),
                             ('resource', ['http://elsewhere/mcp']),
                             ('expires', timezone.now() - timedelta(seconds=1))]:
            with self.subTest(field=field, value=value):
                original = getattr(self.token, field)
                setattr(self.token, field, value)
                self.token.save()
                self.assertEqual(authenticate(self.request())[1].status_code, 401)
                setattr(self.token, field, original)
                self.token.save()
        self.token.delete()
        self.assertEqual(authenticate(self.request())[1].status_code, 401)

    def test_account_changes_apply_to_existing_tokens(self):
        for field in ['is_approved', 'must_change_password']:
            setattr(self.user.profile, field, field == 'must_change_password')
            self.user.profile.save()
            self.assertEqual(authenticate(self.request())[1].status_code, 403)
            setattr(self.user.profile, field, field != 'must_change_password')
            self.user.profile.save()
        self.user.is_active = False
        self.user.save()
        self.assertEqual(authenticate(self.request())[1].status_code, 403)

    def test_cookie_does_not_authorize_and_query_token_rejected(self):
        request = self.factory.post('/mcp', '{}', content_type='application/json')
        request.user = self.user
        self.assertEqual(authenticate(request)[1].status_code, 401)
        request = self.request()
        request.GET = {'access_token': 'mcp-test-token'}
        self.assertEqual(authenticate(request)[1].status_code, 401)

    def registration(self, **kwargs):
        body = {'redirect_uris': ['https://claude.ai/api/mcp/auth_callback'], **kwargs}
        return register(self.factory.post('/mcp/register', json.dumps(body), content_type='application/json'))

    def test_registration_public_no_secret_no_privilege_options(self):
        response = self.registration(client_name='Claude')
        self.assertEqual(response.status_code, 201)
        data = json.loads(response.content)
        self.assertNotIn('client_secret', data)
        application = get_application_model().objects.get(client_id=data['client_id'])
        self.assertFalse(application.skip_authorization)
        self.assertEqual(application.client_type, 'public')
        for extra in [{'redirect_uris': ['https://evil.example/callback']},
                      {'scope': 'profile'}, {'token_endpoint_auth_method': 'client_secret_post'},
                      {'grant_types': ['client_credentials']}]:
            self.assertEqual(self.registration(**extra).status_code, 400)

    @override_settings(MCP_REGISTRATION_LIMIT=1)
    def test_registration_rate_limit(self):
        self.assertEqual(self.registration().status_code, 201)
        self.assertEqual(self.registration().status_code, 429)

    def test_dcr_pkce_always_required(self):
        request = type('Request', (), {'scopes': ['mcp']})()
        self.assertTrue(MCPOAuthValidator().is_pkce_required(self.application.client_id, request))

    def test_authorization_code_flow_s256_and_resource(self):
        self.client.force_login(self.user)
        verifier = 'a' * 64
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        data = {
            'client_id': self.application.client_id,
            'redirect_uri': self.application.redirect_uris,
            'response_type': 'code', 'scope': 'mcp', 'state': 'test-state',
            'code_challenge': challenge, 'code_challenge_method': 'S256',
            'resource': 'http://testserver/mcp', 'allow': 'Authorize',
        }
        response = self.client.post('/o/authorize/', data)
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response['Location']).query)
        self.assertIn('code', query, response['Location'])
        wrong = self.client.post('/o/token/', {
            'grant_type': 'authorization_code', 'client_id': self.application.client_id,
            'redirect_uri': data['redirect_uri'], 'code': query['code'][0], 'code_verifier': 'b' * 64,
        })
        self.assertEqual(wrong.status_code, 400)
        response = self.client.post('/o/token/', {
            'grant_type': 'authorization_code', 'client_id': self.application.client_id,
            'redirect_uri': data['redirect_uri'], 'code': query['code'][0], 'code_verifier': verifier,
        })
        self.assertEqual(response.status_code, 200, response.content)
        tokens = response.json()
        self.assertLessEqual(tokens['expires_in'], settings.MCP_ACCESS_TOKEN_EXPIRE_SECONDS)
        token = tokens['access_token']
        self.assertIsNone(authenticate(self.request(token))[1])
        response = self.client.post('/mcp', json.dumps({
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list',
        }), content_type='application/json', HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['result']['tools']), 10)
        # Authorization codes are single-use.
        replay = self.client.post('/o/token/', {
            'grant_type': 'authorization_code', 'client_id': self.application.client_id,
            'redirect_uri': data['redirect_uri'], 'code': query['code'][0], 'code_verifier': verifier,
        })
        self.assertEqual(replay.status_code, 400)
        return tokens

    def test_authorization_without_s256_denied(self):
        self.client.force_login(self.user)
        response = self.client.get('/o/authorize/', {
            'client_id': self.application.client_id, 'response_type': 'code', 'scope': 'mcp',
            'redirect_uri': 'https://claude.ai/api/mcp/auth_callback',
            'resource': 'http://testserver/mcp',
        })
        self.assertTrue(response.status_code == 400 or 'error=' in response.get('Location', ''))

    def test_endpoint_ignores_cookie_and_legacy_api_tokens(self):
        self.client.force_login(self.user)
        body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'ping'})
        for headers in ({}, {'HTTP_AUTHORIZATION': 'Bearer ' + 'a' * 48}):
            response = self.client.post('/mcp', body, content_type='application/json', **headers)
            self.assertEqual(response.status_code, 401)
            self.assertIn('resource_metadata', response['WWW-Authenticate'])

    def test_discovery_and_disabled_registration(self):
        data = self.client.get('/.well-known/oauth-protected-resource/mcp').json()
        self.assertEqual(data['resource'], 'http://testserver/mcp')
        self.assertEqual(data['scopes_supported'], ['mcp'])
        data = self.client.get('/.well-known/oauth-authorization-server').json()
        self.assertEqual(data['code_challenge_methods_supported'], ['S256'])
        with override_settings(MCP_ENABLED=False):
            self.assertEqual(self.registration().status_code, 404)
            self.assertEqual(self.client.get('/.well-known/oauth-protected-resource/mcp').status_code, 404)

    def test_web_application_type_from_sdk(self):
        self.assertEqual(self.registration(application_type='web').status_code, 201)
        self.assertEqual(self.registration(application_type='other').status_code, 400)

    def test_native_loopback_is_opt_in(self):
        response = self.registration(application_type='native', redirect_uris=['http://127.0.0.1:45678/callback'])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content)['error'], 'invalid_redirect_uri')

    @override_settings(MCP_ALLOW_LOOPBACK_REDIRECTS=True, MCP_REGISTRATION_LIMIT=30)
    def test_native_loopback_callback_validation(self):
        for uri in ['http://127.0.0.1:45678/callback', 'http://localhost:45679/callback',
                    'http://[::1]:45680/callback']:
            response = self.registration(application_type='native', redirect_uris=[uri])
            self.assertEqual(response.status_code, 201, response.content)
            application = get_application_model().objects.get(client_id=json.loads(response.content)['client_id'])
            self.assertEqual(application.redirect_uris, uri)
        for uri in ['http://127.0.0.1.evil.example:45678/callback', 'http://192.168.1.10:45678/callback',
                    'http://0.0.0.0:45678/callback', 'http://user@127.0.0.1:45678/callback',
                    'http://localhost:45678/other', 'http://localhost:45678/callback?x=1',
                    'http://localhost:45678/callback#fragment', 'http://localhost:0/callback',
                    'http://localhost/callback', 'http://localhost:bad/callback']:
            response = self.registration(application_type='native', redirect_uris=[uri])
            self.assertEqual(response.status_code, 400, uri)
        self.assertEqual(self.registration(application_type='web',
                                           redirect_uris=['http://localhost:45678/callback']).status_code, 400)

    @override_settings(MCP_ALLOW_LOOPBACK_REDIRECTS=True)
    def test_native_loopback_authorization_code_flow(self):
        response = self.registration(application_type='native', redirect_uris=['http://127.0.0.1:45678/callback'])
        self.assertEqual(response.status_code, 201)
        self.application = get_application_model().objects.get(client_id=json.loads(response.content)['client_id'])
        self.test_authorization_code_flow_s256_and_resource()

    def test_manual_self_registered_application_cannot_request_mcp(self):
        attacker = create_user(username='attacker')
        self.client.force_login(attacker)
        response = self.client.post('/o/applications/register/', {
            'name': 'Claude', 'client_id': 'evil-app', 'client_secret': 'evil-secret',
            'client_type': 'public', 'authorization_grant_type': 'authorization-code',
            'redirect_uris': 'https://evil.example/cb', 'hash_client_secret': True,
        })
        self.assertEqual(response.status_code, 302)
        application = get_application_model().objects.get(client_id='evil-app')
        self.assertEqual(application.registration_source, 'manual')
        self.client.force_login(self.user)
        verifier = 'a' * 64
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        response = self.client.post('/o/authorize/', {
            'client_id': application.client_id, 'redirect_uri': application.redirect_uris,
            'response_type': 'code', 'scope': 'mcp', 'code_challenge': challenge,
            'code_challenge_method': 'S256', 'resource': 'http://testserver/mcp', 'allow': 'Authorize',
        })
        self.assertTrue(response.status_code == 400 or 'error=' in response.get('Location', ''))
        self.assertFalse(get_grant_model().objects.filter(application=application).exists())
        # A token issued before this fix must also stop working.
        self.token.application = application
        self.token.save()
        self.assertEqual(authenticate(self.request())[1].status_code, 401)

    def test_existing_manual_authorization_code_cannot_be_exchanged(self):
        self.application.registration_source = 'manual'
        self.application.save()
        verifier = 'a' * 64
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        get_grant_model().objects.create(
            application=self.application, user=self.user, code='old-code',
            expires=timezone.now() + timedelta(minutes=5), redirect_uri=self.application.redirect_uris,
            scope='mcp', code_challenge=challenge, code_challenge_method='S256', resource=['http://testserver/mcp'],
        )
        response = self.client.post('/o/token/', {
            'client_id': self.application.client_id, 'grant_type': 'authorization_code', 'code': 'old-code',
            'redirect_uri': self.application.redirect_uris, 'code_verifier': verifier,
        })
        self.assertEqual(response.status_code, 400)

    def test_client_redirect_policy_rechecked_for_existing_token(self):
        self.application.redirect_uris = 'https://evil.example/cb'
        self.application.save()
        self.assertEqual(authenticate(self.request())[1].status_code, 401)

    def test_optional_and_unknown_metadata_ignored_without_privilege(self):
        response = self.registration(client_uri='https://client.example', logo_uri='https://client.example/logo',
                                     software_id='example', software_version='1', skip_authorization=True,
                                     client_type='confidential', registration_source='manual')
        self.assertEqual(response.status_code, 201)
        application = get_application_model().objects.get(client_id=json.loads(response.content)['client_id'])
        self.assertEqual(application.client_type, 'public')
        self.assertFalse(application.skip_authorization)
        self.assertEqual(application.registration_source, 'dcr')

    def test_deep_registration_json_rejected(self):
        response = self.client.post('/mcp/register', '[' * 1500 + '0' + ']' * 1500,
                                    content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'invalid_client_metadata')

    @override_settings(MCP_RESOURCE_URL='https://oj.example/mcp')
    def test_challenge_uses_public_tls_url_behind_proxy(self):
        response = authenticate(self.request('bad'))[1]
        self.assertIn('https://oj.example/.well-known/oauth-protected-resource/mcp',
                      response['WWW-Authenticate'])

    def test_refresh_expiry_enforced(self):
        refresh = get_refresh_token_model().objects.create(
            application=self.application, user=self.user, access_token=self.token, token='refresh-test',
            resource=['http://testserver/mcp'],
        )
        get_refresh_token_model().objects.filter(pk=refresh.pk).update(
            created=timezone.now() - timedelta(seconds=settings.MCP_REFRESH_TOKEN_EXPIRE_SECONDS + 1))
        response = self.client.post('/o/token/', {
            'client_id': self.application.client_id, 'grant_type': 'refresh_token', 'refresh_token': 'refresh-test',
        })
        self.assertEqual(response.status_code, 400)

    def test_manual_refresh_token_rejected(self):
        self.application.registration_source = 'manual'
        self.application.save()
        get_refresh_token_model().objects.create(
            application=self.application, user=self.user, access_token=self.token, token='refresh-test',
            resource=['http://testserver/mcp'],
        )
        response = self.client.post('/o/token/', {
            'client_id': self.application.client_id, 'grant_type': 'refresh_token', 'refresh_token': 'refresh-test',
        })
        self.assertEqual(response.status_code, 400)

    def test_password_change_revokes_mcp_but_preserves_legacy_tokens(self):
        get_refresh_token_model().objects.create(
            application=self.application, user=self.user, access_token=self.token, token='refresh-test',
            resource=['http://testserver/mcp'],
        )
        legacy_app = get_application_model().objects.create(
            client_type='public', authorization_grant_type='authorization-code',
            redirect_uris='https://legacy.example/cb',
        )
        legacy = get_access_token_model().objects.create(
            application=legacy_app, user=self.user, scope='profile', token='legacy',
            expires=timezone.now() + timedelta(hours=1),
        )
        legacy_refresh = get_refresh_token_model().objects.create(
            application=legacy_app, user=self.user, access_token=legacy, token='legacy-refresh')
        get_grant_model().objects.create(
            application=self.application, user=self.user, code='pending',
            expires=timezone.now() + timedelta(minutes=5), scope='mcp', redirect_uri=self.application.redirect_uris,
        )
        self.user.set_password('changed-password')
        self.user.save()
        self.assertEqual(authenticate(self.request())[1].status_code, 401)
        self.assertFalse(get_grant_model().objects.filter(code='pending').exists())
        response = self.client.post('/o/token/', {
            'client_id': self.application.client_id, 'grant_type': 'refresh_token', 'refresh_token': 'refresh-test',
        })
        self.assertEqual(response.status_code, 400)
        self.assertTrue(get_access_token_model().objects.filter(pk=legacy.pk).exists())
        legacy_refresh.refresh_from_db()
        self.assertIsNone(legacy_refresh.revoked)

    def test_existing_overlong_access_token_cannot_exceed_mcp_age(self):
        get_access_token_model().objects.filter(pk=self.token.pk).update(
            created=timezone.now() - timedelta(seconds=settings.MCP_ACCESS_TOKEN_EXPIRE_SECONDS + 1))
        self.assertEqual(authenticate(self.request())[1].status_code, 401)

    def test_registration_accepts_grant_types_in_any_order(self):
        for grant_types in (['authorization_code'], ['authorization_code', 'refresh_token'],
                            ['refresh_token', 'authorization_code']):
            with self.subTest(grant_types=grant_types):
                cache.clear()
                self.assertEqual(self.registration(grant_types=grant_types).status_code, 201)
        for grant_types in (['refresh_token'], ['authorization_code', 'password'], [], 'authorization_code',
                            [{'unhashable': 1}], ['authorization_code', 'authorization_code', 'refresh_token']):
            with self.subTest(grant_types=grant_types):
                cache.clear()
                self.assertEqual(self.registration(grant_types=grant_types).status_code, 400)

    def assert_server_error(self, response):
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {'error': 'server_error'})
        self.assertNotIn('boom', response.content.decode())
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_unexpected_errors_return_json_without_html_error_page(self):
        with patch('judge.mcp_auth.get_application_model', side_effect=RuntimeError('boom')):
            self.assert_server_error(self.client.post(
                '/mcp/register', json.dumps({'redirect_uris': ['https://claude.ai/api/mcp/auth_callback']}),
                content_type='application/json'))
        with patch('judge.mcp_auth.get_oauthlib_core', side_effect=RuntimeError('boom')):
            self.assert_server_error(self.client.post(
                '/mcp', '{}', content_type='application/json', HTTP_AUTHORIZATION='Bearer mcp-test-token'))
        with patch('judge.mcp_auth.resource_url', side_effect=RuntimeError('boom')):
            for path in ('/.well-known/oauth-protected-resource/mcp', '/.well-known/oauth-authorization-server'):
                self.assert_server_error(self.client.get(path))

    def test_authentication_error_fails_closed(self):
        with patch('judge.mcp_auth.authenticate', side_effect=RuntimeError('boom')), \
                patch('judge.mcp.call_tool') as call:
            self.assert_server_error(self.client.post(
                '/mcp', json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                    'params': {'name': 'delete_contest'}}),
                content_type='application/json', HTTP_AUTHORIZATION='Bearer mcp-test-token'))
        call.assert_not_called()

    def test_oversized_body_is_a_client_error(self):
        response = self.client.post('/mcp/register', 'x' * 3000000, content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'invalid_request'})

    def test_views_keep_middleware_bypass_after_decoration(self):
        for path in ('/mcp', '/mcp/register', '/.well-known/oauth-protected-resource/mcp',
                     '/.well-known/oauth-authorization-server'):
            self.assertTrue(resolve(path).func.short_circuit_middleware, path)
        self.assertTrue(resolve('/mcp').func.csrf_exempt)
        self.assertTrue(resolve('/mcp/register').func.csrf_exempt)

    def test_mcp_token_cannot_finish_deletion_on_the_website(self):
        self.user.user_permissions.add(*Permission.objects.filter(
            codename__in=('edit_own_problem', 'edit_own_contest', 'delete_problem', 'delete_contest')))
        problem = create_problem('tokendelete', authors=('mcpowner',))
        contest = create_contest('tokendelete', authors=('mcpowner',))
        for url in (reverse('problem_delete', args=[problem.code]), reverse('contest_delete', args=[contest.key])):
            with self.subTest(url=url):
                self.assertNotEqual(self.client.post(url, HTTP_AUTHORIZATION='Bearer mcp-test-token').status_code, 200)
        self.assertIsNone(Problem.objects.get(pk=problem.pk).deleted_at)
        self.assertTrue(Contest.objects.filter(pk=contest.pk).exists())
