"""Serializers turning model instances into plain dicts for MCP output.

Kept deliberately thin — tools may add or omit fields depending on the
permission context. Anything that is private test data (judge data zips,
internal flags) must never appear here.
"""
import json
import re

__all__ = [
    'serialize_problem', 'serialize_problem_summary',
    'serialize_submission', 'json_text',
    'serialize_contest_summary', 'serialize_contest_detail', 'serialize_contest_standings_row',
    'serialize_user_summary', 'serialize_user_detail',
]


_MD_IMAGE = re.compile(r'!\[(?P<alt>[^\]]*)\]\((?P<url>[^)\s]+)(?:\s+"[^"]*")?\)')


def _extract_markdown_images(markdown):
    """Return a list of {alt, url} pulled from markdown image syntax."""
    return [{'alt': m.group('alt'), 'url': m.group('url')}
            for m in _MD_IMAGE.finditer(markdown or '')]


def serialize_problem_summary(problem):
    return {
        'code': problem.code,
        'name': problem.name,
        'points': problem.points,
        'partial': problem.partial,
        'is_public': problem.is_public,
        'is_organization_private': problem.is_organization_private,
    }


def serialize_problem(problem):
    data = serialize_problem_summary(problem)
    data.update({
        'description': problem.description,
        'time_limit': problem.time_limit,
        'memory_limit': problem.memory_limit,
        'short_circuit': problem.short_circuit,
        'pdf_url': problem.absolute_pdf_url,
        'types': [str(t) for t in problem.types.all()],
        'authors': list(problem.authors.values_list('user__username', flat=True)),
        'allowed_languages': list(
            problem.allowed_languages.values_list('key', flat=True).order_by('key'),
        ),
        'images': _extract_markdown_images(problem.description),
    })
    return data


def serialize_submission(submission, include_source=False):
    data = {
        'id': submission.id,
        'user': submission.user.user.username,
        'problem': submission.problem.code,
        'date': submission.date.isoformat() if submission.date else None,
        'language': submission.language.key,
        'time': submission.time,
        'memory': submission.memory,
        'points': submission.points,
        'status': submission.status,
        'result': submission.result,
        'case_points': submission.case_points,
        'case_total': submission.case_total,
        'contest': submission.contest_object.key if submission.contest_object_id else None,
    }
    if include_source:
        try:
            data['source'] = submission.source.source
        except Exception:
            data['source'] = None
    return data


def json_text(obj):
    """Wrap an object as a single MCP TextContent block with JSON body."""
    return [{'type': 'text', 'text': json.dumps(obj, ensure_ascii=False, default=str)}]


def serialize_contest_summary(contest):
    return {
        'key': contest.key,
        'name': contest.name,
        'start_time': contest.start_time.isoformat() if contest.start_time else None,
        'end_time': contest.end_time.isoformat() if contest.end_time else None,
        'duration_seconds': int(contest.contest_window_length.total_seconds())
        if contest.contest_window_length is not None else None,
        'is_rated': contest.is_rated,
        'ended': contest.ended,
        'format_name': contest.format_name,
        'organization': contest.organization.slug if contest.organization else None,
    }


def serialize_contest_detail(contest):
    data = serialize_contest_summary(contest)
    data.update({
        'description': contest.description,
        'scoreboard_visibility': contest.scoreboard_visibility,
        'is_visible': contest.is_visible,
        'is_private': contest.is_private,
        'is_organization_private': contest.is_organization_private,
        'frozen_last_minutes': contest.frozen_last_minutes,
        'is_frozen': contest.is_frozen,
        'problems': [{
            'code': contest_problem.problem.code,
            'name': contest_problem.problem.name,
            'order': contest_problem.order,
            'points': contest_problem.points,
            'partial': contest_problem.partial,
            'is_pretested': contest_problem.is_pretested,
            'max_submissions': contest_problem.max_submissions,
        } for contest_problem in contest.contest_problems.all()],
    })
    return data


def serialize_contest_standings_row(participation, rank, use_frozen):
    score = participation.score
    cumtime = participation.cumtime
    tiebreaker = participation.tiebreaker
    if use_frozen:
        score = participation.frozen_score
        cumtime = participation.frozen_cumtime
        tiebreaker = participation.frozen_tiebreaker

    return {
        'rank': rank,
        'username': participation.user.user.username,
        'display_name': participation.user.display_name,
        'score': score,
        'cumtime': int(cumtime) if cumtime is not None else None,
        'tiebreaker': tiebreaker,
        'virtual': participation.virtual,
        'is_disqualified': participation.is_disqualified,
        'real_start': participation.real_start.isoformat() if participation.real_start else None,
    }


def _get_profile_rating(profile):
    rating = getattr(profile, 'rating', None)
    if rating is not None:
        return rating

    ratings = getattr(profile, 'ratings', None)
    if ratings is None:
        return None

    latest = ratings.order_by('-contest__end_time').first()
    return latest.rating if latest is not None else None


def serialize_user_summary(profile):
    return {
        'username': profile.user.username,
        'display_name': profile.display_name,
        'rating': _get_profile_rating(profile),
        'problem_count': profile.problem_count,
        'is_unlisted': profile.is_unlisted,
        'is_banned': profile.is_banned,
        'display_rank': profile.display_rank,
    }


def serialize_user_detail(profile, viewer):
    rating = _get_profile_rating(profile)
    is_owner = bool(getattr(viewer, 'is_authenticated', False) and profile.user_id == viewer.id)
    data = {
        'username': profile.user.username,
        'display_name': profile.display_name,
        'about': profile.about,
        'rating': rating,
        'performance_points': profile.performance_points,
        'contribution_points': profile.contribution_points,
        'problem_count': profile.problem_count,
        'rank': profile.__class__.objects.filter(
            is_unlisted=False,
            performance_points__gt=profile.performance_points,
        ).exclude(id=profile.id).count() + 1,
        'rating_rank': (
            profile.__class__.objects.filter(
                is_unlisted=False,
                rating__gt=rating,
            ).count() + 1
            if rating is not None else None
        ),
        'timezone': profile.timezone,
        'date_joined': profile.user.date_joined.isoformat() if profile.user.date_joined else None,
        'last_access': profile.last_access.isoformat() if profile.last_access else None,
        'is_unlisted': profile.is_unlisted,
        'is_banned': profile.is_banned,
        'display_rank': profile.display_rank,
        'organizations': [{
            'slug': organization.slug,
            'name': organization.name,
            'short_name': organization.short_name,
        } for organization in profile.organizations.all()],
        'email': profile.user.email if (is_owner or getattr(viewer, 'is_staff', False)) else None,
    }

    badges = getattr(profile, 'badges', None)
    if badges is not None:
        data['badges'] = [{
            'name': badge.name,
            'mini': badge.mini,
            'full_size': badge.full_size,
        } for badge in badges.all()]

    return data
