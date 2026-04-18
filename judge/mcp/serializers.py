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
