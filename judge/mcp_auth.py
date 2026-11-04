"""OAuth boundaries for the problem/contest MCP endpoint (DOT 3.4.1)."""
import hashlib
import json
import logging
from datetime import timedelta
from functools import wraps
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import SuspiciousOperation
from django.db.models import Q
from django.http import HttpResponseNotAllowed, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from oauth2_provider.models import get_application_model
from oauth2_provider.oauth2_backends import get_oauthlib_core

from judge.oauth_validator import VNOIOAuthValidator

logger = logging.getLogger(__name__)


def resource_url(request):
    return getattr(settings, 'MCP_RESOURCE_URL', '') or request.build_absolute_uri('/mcp')


def _origin(uri):
    parts = urlsplit(uri)
    return '%s://%s' % (parts.scheme, parts.netloc)


def _json(data, status=200):
    response = JsonResponse(data, status=status)
    response['Cache-Control'] = 'no-store'
    return response


def _unavailable():
    return _json({'error': 'not_found'}, 404)


def json_server_errors(view):
    """Answer in JSON: short-circuited views skip the middleware that HTML error pages need."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except SuspiciousOperation:
            logger.warning('MCP request rejected: %s', request.path)
            return _json({'error': 'invalid_request'}, 400)
        except Exception:
            logger.exception('MCP request failed: %s', request.path)
            return _json({'error': 'server_error'}, 500)
    return wrapper


def authenticate(request):
    """Never use cookie authentication, legacy API tokens, or unrestricted tokens."""
    challenge = ('Bearer resource_metadata="%s/.well-known/oauth-protected-resource/mcp"' %
                 _origin(resource_url(request)))
    header = request.headers.get('Authorization', '').split()
    if len(header) != 2 or header[0].lower() != 'bearer' or 'access_token' in request.GET:
        response = _json({'error': 'invalid_token'}, 401)
        response['WWW-Authenticate'] = challenge
        return None, response
    valid, oauth_request = get_oauthlib_core().verify_request(request, scopes=['mcp'])
    token = getattr(oauth_request, 'access_token', None)
    if (not valid or token is None or resource_url(request) not in token.resource or
            not valid_mcp_client(token.application) or
            token.created + timedelta(seconds=settings.MCP_ACCESS_TOKEN_EXPIRE_SECONDS) <= timezone.now()):
        response = _json({'error': 'invalid_token'}, 401)
        response['WWW-Authenticate'] = challenge
        return None, response
    user = oauth_request.user
    if not user or not user.is_active or not hasattr(user, 'profile'):
        return None, _json({'error': 'access_denied'}, 403)
    profile = user.profile
    if not profile.is_approved or profile.must_change_password:
        return None, _json({'error': 'access_denied'}, 403)
    return user, None


def revoke_user_mcp_tokens(user_id, access_model, refresh_model, grant_model):
    access_ids = [token.pk for token in access_model.objects.filter(user_id=user_id, scope__contains='mcp')
                  if 'mcp' in token.scope.split()]
    # Include old rotated refresh tokens whose access token has been removed.
    refresh_model.objects.filter(user_id=user_id).filter(
        Q(application__registration_source='dcr') | Q(access_token_id__in=access_ids),
    ).update(revoked=timezone.now())
    access_model.objects.filter(pk__in=access_ids).delete()
    grant_ids = [grant.pk for grant in grant_model.objects.filter(user_id=user_id, scope__contains='mcp')
                 if 'mcp' in grant.scope.split()]
    grant_model.objects.filter(pk__in=grant_ids).delete()


@json_server_errors
def protected_resource_metadata(request):
    if not getattr(settings, 'MCP_ENABLED', False):
        return _unavailable()
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    return _json({
        'resource': resource_url(request),
        'authorization_servers': [_origin(resource_url(request))],
        'scopes_supported': ['mcp'],
        'bearer_methods_supported': ['header'],
        'resource_name': 'VNOJ problem and contest management',
    })


@json_server_errors
def authorization_server_metadata(request):
    if not getattr(settings, 'MCP_ENABLED', False):
        return _unavailable()
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    origin = _origin(resource_url(request))
    return _json({
        'issuer': origin,
        'authorization_endpoint': origin + '/o/authorize/',
        'token_endpoint': origin + '/o/token/',
        'revocation_endpoint': origin + '/o/revoke_token/',
        'registration_endpoint': origin + '/mcp/register',
        'response_types_supported': ['code'],
        'grant_types_supported': ['authorization_code', 'refresh_token'],
        'token_endpoint_auth_methods_supported': ['none'],
        'code_challenge_methods_supported': ['S256'],
        'scopes_supported': ['mcp'],
    })


def valid_redirect_uri(uri, application_type, allowlist):
    if not isinstance(uri, str) or len(uri) > 2048 or any(character.isspace() for character in uri):
        return False
    try:
        parts = urlsplit(uri)
        port = parts.port
    except ValueError:
        return False
    if parts.fragment or parts.username is not None or parts.password is not None:
        return False
    if uri in allowlist and parts.scheme == 'https' and parts.hostname:
        return True
    # RFC 8252 loopback callbacks for native clients are opt-in. Register the
    # concrete URI, including its port; OAuth still exact-matches it later.
    return (getattr(settings, 'MCP_ALLOW_LOOPBACK_REDIRECTS', False) and application_type == 'native' and
            parts.scheme == 'http' and parts.hostname in ('127.0.0.1', 'localhost', '::1') and
            port is not None and 1 <= port <= 65535 and parts.path in ('/callback', '/oauth/callback') and
            not parts.query)


def valid_mcp_client(client):
    if (client is None or client.registration_source != 'dcr' or
            client.client_type != client.CLIENT_PUBLIC or client.skip_authorization or
            client.authorization_grant_type != client.GRANT_AUTHORIZATION_CODE):
        return False
    uris = client.redirect_uris.split()
    allowlist = getattr(settings, 'MCP_ALLOWED_REDIRECT_URIS', ())
    return bool(uris) and all(
        valid_redirect_uri(uri, 'native' if uri.startswith('http:') else 'web', allowlist) for uri in uris)


@csrf_exempt
@json_server_errors
def register(request):
    """Restricted RFC 7591 registration: public authorization-code clients only."""
    if not getattr(settings, 'MCP_ENABLED', False):
        return _unavailable()
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    if request.content_type != 'application/json':
        return _json({'error': 'invalid_client_metadata'}, 415)
    if len(request.body) > 16384:
        return _json({'error': 'invalid_client_metadata'}, 413)
    # Use the socket peer, never trust a caller-supplied X-Forwarded-For.
    key = 'mcp-register:' + hashlib.sha256(request.META.get('REMOTE_ADDR', '').encode()).hexdigest()
    cache.add(key, 0, 3600)
    try:
        count = cache.incr(key)
    except ValueError:
        return _json({'error': 'temporarily_unavailable'}, 503)
    if count > getattr(settings, 'MCP_REGISTRATION_LIMIT', 10):
        response = _json({'error': 'rate_limit_exceeded'}, 429)
        response['Retry-After'] = '3600'
        return response
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return _json({'error': 'invalid_client_metadata'}, 400)
    # RFC 7591: ignore unknown metadata. Only the supported fields below are
    # ever used to construct the application; flags such as skip_authorization
    # cannot be supplied through registration.
    if not isinstance(data, dict):
        return _json({'error': 'invalid_client_metadata'}, 400)
    application_type = data.get('application_type', 'web')
    if application_type not in ('web', 'native'):
        return _json({'error': 'invalid_client_metadata',
                      'error_description': 'application_type must be web or native.'}, 400)
    uris = data.get('redirect_uris')
    allowlist = getattr(settings, 'MCP_ALLOWED_REDIRECT_URIS', ())
    if (not isinstance(uris, list) or not 1 <= len(uris) <= 5 or
            any(not valid_redirect_uri(uri, application_type, allowlist) for uri in uris)):
        return _json({'error': 'invalid_redirect_uri',
                      'error_description': 'Use an allowlisted HTTPS callback or an enabled native loopback callback.'},
                     400)
    name = data.get('client_name', 'MCP client')
    if (not isinstance(name, str) or len(name) > 255 or
            data.get('grant_types', ['authorization_code', 'refresh_token']) not in
            (['authorization_code'], ['authorization_code', 'refresh_token'],
             ['refresh_token', 'authorization_code']) or
            data.get('response_types', ['code']) != ['code'] or
            data.get('token_endpoint_auth_method', 'none') != 'none' or data.get('scope', 'mcp') != 'mcp'):
        return _json({'error': 'invalid_client_metadata'}, 400)
    application_model = get_application_model()
    application = application_model.objects.create(
        name=name, client_type=application_model.CLIENT_PUBLIC,
        authorization_grant_type=application_model.GRANT_AUTHORIZATION_CODE,
        client_secret='', redirect_uris=' '.join(uris), skip_authorization=False,
        registration_source='dcr',
    )
    return _json({
        'client_id': application.client_id, 'client_name': name, 'redirect_uris': uris,
        'grant_types': ['authorization_code', 'refresh_token'], 'response_types': ['code'],
        'token_endpoint_auth_method': 'none', 'scope': 'mcp', 'application_type': application_type,
    }, 201)


class MCPOAuthValidator(VNOIOAuthValidator):
    """Require S256 PKCE for MCP without changing existing non-MCP clients."""

    def is_pkce_required(self, client_id, request):
        if 'mcp' in (getattr(request, 'scopes', None) or []):
            return True
        application = get_application_model().objects.filter(client_id=client_id).first()
        if application is not None and application.registration_source == 'dcr':
            return True
        return super().is_pkce_required(client_id, request)

    def validate_scopes(self, client_id, scopes, client, request, *args, **kwargs):
        if client.registration_source == 'dcr' and set(scopes) != {'mcp'}:
            return False
        if 'mcp' in scopes:
            if not getattr(settings, 'MCP_ENABLED', False) or not valid_mcp_client(client):
                return False
            if client.authorization_grant_type != client.GRANT_AUTHORIZATION_CODE:
                return False
            # On the authorization request, insist on S256; at token exchange
            # oauthlib verifies the stored challenge against code_verifier.
            if getattr(request, 'response_type', None) == 'code':
                if (getattr(request, 'code_challenge_method', None) != 'S256' or
                        not getattr(request, 'code_challenge', None)):
                    return False
        return super().validate_scopes(client_id, scopes, client, request, *args, **kwargs)

    def validate_code(self, client_id, code, client, request, *args, **kwargs):
        if not super().validate_code(client_id, code, client, request, *args, **kwargs):
            return False
        return 'mcp' not in request.scopes or (settings.MCP_ENABLED and valid_mcp_client(client))

    def validate_refresh_token(self, refresh_token, client, request, *args, **kwargs):
        if not super().validate_refresh_token(refresh_token, client, request, *args, **kwargs):
            return False
        refresh = request.refresh_token_instance
        token = refresh.access_token
        is_mcp = client.registration_source == 'dcr' or token and 'mcp' in token.scope.split()
        if is_mcp:
            if not settings.MCP_ENABLED or not valid_mcp_client(client):
                return False
            if refresh.created + timedelta(seconds=settings.MCP_REFRESH_TOKEN_EXPIRE_SECONDS) <= timezone.now():
                return False
            user = request.user
            if (not user.is_active or not hasattr(user, 'profile') or not user.profile.is_approved or
                    user.profile.must_change_password):
                return False
        return True

    def _save_bearer_token(self, token, request, *args, **kwargs):
        if 'mcp' in token.get('scope', '').split():
            token['expires_in'] = min(token.get('expires_in', settings.MCP_ACCESS_TOKEN_EXPIRE_SECONDS),
                                      settings.MCP_ACCESS_TOKEN_EXPIRE_SECONDS)
        return super()._save_bearer_token(token, request, *args, **kwargs)


# Discovery and public registration must not inherit cookie/legacy API authentication.
protected_resource_metadata.short_circuit_middleware = True
authorization_server_metadata.short_circuit_middleware = True
register.short_circuit_middleware = True
