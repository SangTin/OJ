from mptt.utils import get_cached_trees

__all__ = ['nav_tree', 'register_nav_badge']

# Navigation item key -> callable returning the number shown in a badge next to that item (0 hides it).
_badges = {}


def register_nav_badge(key, count):
    _badges[key] = count


def _prepare(nodes, user):
    visible = []
    for node in nodes:
        if not node.is_visible_to(user):
            continue
        children = _prepare(node.get_children(), user)
        # get_cached_trees() stores the children here; keep only the ones this user may see.
        node._cached_children = children
        badge = _badges.get(node.key)
        node.badge = (badge() if badge is not None else 0) + sum(child.badge for child in children)
        visible.append(node)
    return visible


def nav_tree(nav_bar, user):
    """The navigation tree without items the user lacks the permission for, with badge counts.

    A parent's badge is the sum of its children's, so it shows even while the dropdown is closed.
    """
    return _prepare(get_cached_trees(nav_bar), user)
