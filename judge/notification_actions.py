"""Quick actions shown on notifications (e.g. Approve/Reject on an "account awaiting approval" notification).

To add actions to a new kind of notification:

1. Subclass ``NotificationActionHandler`` with a unique ``kind`` and decorate it with ``@register``.
2. Implement ``get_actions`` (what the viewer can do *right now*; return ``[]`` once it has been handled)
   and ``perform`` (do it and return a message for the user, or raise ``NotificationActionError``).
   Call ``resolve_notifications(kind, key=value)`` once handled, wherever that happens.
   Optionally implement ``get_title`` to render the title in the viewer's language.
3. Create the notifications with ``make_notification(..., kind=YourHandler.kind, data={...})``.
4. Make sure the module defining the handler is imported at startup (see ``JudgeAppConfig.ready``).

The panel, the notification page and the ``notification_action`` endpoint pick up the actions automatically.
Actions are re-checked on the server when performed, so stale buttons are harmless.
"""
from dataclasses import asdict, dataclass

from judge.models import Notification
from judge.utils.cache_helper import bulk_invalidate_notification_caches

__all__ = ['NotificationAction', 'NotificationActionError', 'NotificationActionHandler', 'register',
           'get_handler', 'get_actions', 'serialize_actions', 'resolve_notifications', 'display_title']


@dataclass
class NotificationAction:
    name: str
    label: str
    # 'primary' or 'danger'; used for the button colour.
    style: str = 'primary'
    # Asked with confirm() before performing the action, if set.
    confirm: str = ''


class NotificationActionError(Exception):
    """Raised by ``perform`` with a message to show when the action can't be done."""


class NotificationActionHandler:
    kind = None

    def get_actions(self, notification, request):
        raise NotImplementedError()

    def perform(self, notification, action, request):
        raise NotImplementedError()

    def get_title(self, notification):
        """Title shown to the viewer; None keeps the stored one (which is in the language it was created in)."""
        return None


def resolve_notifications(kind, **data):
    """Mark unread notifications of `kind` whose data matches as read, so other recipients don't act on them again."""
    lookups = {f'data__{key}': value for key, value in data.items()}
    siblings = Notification.objects.filter(kind=kind, read=False, **lookups)
    recipient_ids = list(siblings.values_list('recipient_id', flat=True))
    siblings.update(read=True)
    bulk_invalidate_notification_caches(recipient_ids)


_handlers = {}


def register(handler_class):
    _handlers[handler_class.kind] = handler_class()
    return handler_class


def get_handler(kind):
    return _handlers.get(kind) if kind else None


def get_actions(notification, request):
    handler = get_handler(notification.kind)
    return handler.get_actions(notification, request) if handler is not None else []


def display_title(notification):
    handler = get_handler(notification.kind)
    return (handler.get_title(notification) if handler is not None else None) or notification.title


def serialize_actions(notification, request):
    return [asdict(action) for action in get_actions(notification, request)]
