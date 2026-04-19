import base64
import hmac
import secrets
import struct

from django.conf import settings
from django.contrib.auth.models import Permission, User
from django.db import models, transaction
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.translation import gettext_lazy as _

__all__ = ['MCPToken']


class MCPToken(models.Model):
    """API token for the MCP endpoint.

    Scopes are a set of ``auth.Permission`` objects and form a strict subset
    of the owning user's current permissions. A tool call is authorized only
    if every required permission is both linked to the token (token gate) and
    granted to ``token.user`` via the standard Django auth system (user
    gate). Revoking a permission from the user therefore invalidates that
    capability on every token without rewriting any token rows.

    The raw token is a 48-character urlsafe-base64 string encoding the token
    row id and a 32-byte secret. Only the HMAC-SHA256 digest of the secret is
    stored in ``token_hash``; the raw token is shown once at creation time.
    """

    user = models.ForeignKey(
        User, verbose_name=_('user'), on_delete=models.CASCADE,
        related_name='mcp_tokens',
    )
    name = models.CharField(
        verbose_name=_('token name'), max_length=100,
        help_text=_('Human-readable label, e.g. "Claude Desktop on laptop".'),
    )
    token_hash = models.CharField(
        verbose_name=_('token hash'), max_length=64,
        help_text=_('HMAC-SHA256 hex digest of the token secret.'),
    )
    scopes = models.ManyToManyField(
        Permission, verbose_name=_('scopes'), blank=True,
        related_name='mcp_tokens',
        help_text=_('Permissions this token is allowed to exercise. Must be a '
                    'subset of the permissions granted to the owning user.'),
    )
    created = models.DateTimeField(verbose_name=_('created'), auto_now_add=True)
    last_used = models.DateTimeField(verbose_name=_('last used'), null=True, blank=True)
    expires_at = models.DateTimeField(verbose_name=_('expires at'), null=True, blank=True)
    revoked_at = models.DateTimeField(verbose_name=_('revoked at'), null=True, blank=True)

    class Meta:
        verbose_name = _('MCP API token')
        verbose_name_plural = _('MCP API tokens')
        permissions = (
            ('use_mcp_api', _('Use MCP API')),
            ('mcp_read_problem', _('MCP: read problems')),
            ('mcp_read_submission', _('MCP: read submissions')),
            ('mcp_read_ticket', _('MCP: read tickets')),
            ('mcp_read_contest', _('MCP: read contests')),
        )

    def __str__(self):
        return f'{self.user.username}: {self.name}'

    @property
    def is_active(self):
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and self.expires_at < timezone.now():
            return False
        return True

    def revoke(self):
        self.revoked_at = timezone.now()
        self.save(update_fields=['revoked_at'])

    revoke.alters_data = True

    def touch(self):
        self.last_used = timezone.now()
        self.save(update_fields=['last_used'])

    touch.alters_data = True

    def scope_codenames(self):
        """Return the token's scopes as ``app_label.codename`` strings."""
        return [
            f'{ct_label}.{codename}'
            for ct_label, codename in self.scopes.values_list(
                'content_type__app_label', 'codename',
            )
        ]

    @classmethod
    @transaction.atomic
    def generate(cls, user, name, permissions, expires_at=None):
        """Create an MCPToken row and return (instance, raw_token).

        ``permissions`` must be an iterable of ``auth.Permission`` instances.
        Callers are responsible for ensuring the user currently holds each —
        see ``judge.management.commands.gen_mcp_token`` for the canonical
        resolution and subset-check flow.

        The raw token is returned ONCE; only its HMAC digest is persisted.
        """
        secret = secrets.token_bytes(32)
        token_hash = hmac.new(
            force_bytes(settings.SECRET_KEY), msg=secret, digestmod='sha256',
        ).hexdigest()
        instance = cls.objects.create(
            user=user, name=name, token_hash=token_hash, expires_at=expires_at,
        )
        if permissions:
            instance.scopes.set(permissions)
        raw = base64.urlsafe_b64encode(struct.pack('>I32s', instance.id, secret))
        return instance, raw.decode('utf-8')

    @classmethod
    def verify(cls, raw_token):
        """Resolve a raw token string to a live MCPToken row, or return None."""
        try:
            token_id, secret = struct.unpack('>I32s', base64.urlsafe_b64decode(raw_token))
        except (ValueError, struct.error):
            return None
        try:
            instance = (
                cls.objects
                .select_related('user')
                .prefetch_related('scopes__content_type')
                .get(id=token_id)
            )
        except cls.DoesNotExist:
            return None
        if not instance.is_active:
            return None
        expected = hmac.new(
            force_bytes(settings.SECRET_KEY), msg=secret, digestmod='sha256',
        ).hexdigest()
        if not hmac.compare_digest(expected, instance.token_hash):
            return None
        return instance
