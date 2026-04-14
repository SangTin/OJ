import base64
import logging
import os
import zipfile

import requests as http_requests
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.http import JsonResponse
from django.views.generic import View

from judge.models import Language, Problem
from judge.models.problem_data import ProblemData, ProblemTestCase, problem_data_storage

logger = logging.getLogger(__name__)

MAX_SAMPLE_SIZE = 64 * 1024  # 64KB limit for sample file reads


def check_rate_limit(user_id):
    """Allow up to RUN_CODE_MAX_REQUESTS per RUN_CODE_WINDOW seconds."""
    key = 'run_code:%d' % user_id
    try:
        count = cache.incr(key)
    except ValueError:
        # Key doesn't exist yet — initialize it
        cache.set(key, 1, settings.RUN_CODE_WINDOW)
        return True
    return count <= settings.RUN_CODE_MAX_REQUESTS


def call_judge0(source_code, language, stdin='', expected_output=None):
    """Run code via Judge0. Returns parsed JSON response."""
    if not settings.JUDGE0_API_URL:
        raise RuntimeError('Judge0 is not configured')

    safe_source = base64.b64encode(source_code.encode('utf-8')).decode('utf-8')
    safe_stdin = base64.b64encode(stdin.encode('utf-8')).decode('utf-8') if stdin else ''

    payload = {
        'source_code': safe_source,
        'language_id': language.judge0.id,
        'stdin': safe_stdin,
    }
    if expected_output is not None:
        payload['expected_output'] = base64.b64encode(expected_output.encode('utf-8')).decode('utf-8')

    resp = http_requests.post(
        '%s/submissions?base64_encoded=true&wait=true' % settings.JUDGE0_API_URL,
        headers={
            'X-Auth-Token': settings.JUDGE0_AUTH_TOKEN,
            'Content-Type': 'application/json',
        },
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    def decode(field):
        val = data.get(field)
        if val:
            return base64.b64decode(val).decode('utf-8', errors='replace')
        return ''

    return {
        'status': data.get('status', {}),
        'stdout': decode('stdout'),
        'compile_output': decode('compile_output'),
        'time': data.get('time'),
        'memory': data.get('memory'),
        'exit_code': data.get('exit_code'),
    }


# --- Sample data reading ---

def _find_zipfile(problem_code):
    """Find the zip archive for a problem's test data."""
    try:
        data = ProblemData.objects.get(problem__code=problem_code)
        if data.zipfile:
            zf_path = problem_data_storage.path(data.zipfile.name)
            if os.path.exists(zf_path):
                return zf_path
    except ProblemData.DoesNotExist:
        pass
    return None


def _read_file(problem_code, filename, zf=None):
    """Read a test file from direct storage or an already-opened zip."""
    try:
        file_path = '%s/%s' % (problem_code, filename)
        if problem_data_storage.exists(file_path):
            with problem_data_storage.open(file_path) as f:
                return f.read(MAX_SAMPLE_SIZE).decode('utf-8', errors='replace')

        if zf and filename in zf.namelist():
            with zf.open(filename) as f:
                return f.read(MAX_SAMPLE_SIZE).decode('utf-8', errors='replace')
    except Exception as e:
        logger.warning('Failed to read sample file %s/%s: %s', problem_code, filename, e)
    return None


def get_sample_data(problem):
    """Get sample input/output for a problem.

    Priority:
    1. Test cases marked as is_sample=True
    2. Inline sample_input/sample_output on ProblemData

    Returns list of {'order': int, 'input': str, 'output': str|None}
    Output is kept server-side only — never sent to the browser.
    """
    sample_cases = ProblemTestCase.objects.filter(
        dataset=problem, is_sample=True,
    ).order_by('order')[:settings.DMOJ_PROBLEM_MAX_SAMPLE_CASES]

    if sample_cases.exists():
        zf_path = _find_zipfile(problem.code)
        zf = zipfile.ZipFile(zf_path) if zf_path else None
        try:
            samples = []
            for case in sample_cases:
                sample = {'order': case.order}
                if case.input_file:
                    sample['input'] = _read_file(problem.code, case.input_file, zf)
                if case.output_file:
                    sample['output'] = _read_file(problem.code, case.output_file, zf)
                if sample.get('input') is not None:
                    samples.append(sample)
        finally:
            if zf:
                zf.close()
        if samples:
            return samples

    # Fallback to inline sample fields
    try:
        data = ProblemData.objects.get(problem=problem)
        if data.sample_input:
            return [{
                'order': 1,
                'input': data.sample_input,
                'output': data.sample_output or None,
            }]
    except ProblemData.DoesNotExist:
        pass

    return []


# --- Views ---

class ProblemRunCodeView(LoginRequiredMixin, View):
    """Run code against sample tests or custom input via Judge0.

    POST params:
        source: source code
        language: language ID
        input: custom input (used when mode=custom)
        mode: 'sample' (run all samples) or 'custom' (run with custom input)
    """

    def post(self, request, problem):
        if not check_rate_limit(request.user.id):
            return JsonResponse({'error': 'Too many requests. Please try again later.'}, status=429)

        try:
            prob = Problem.objects.get(code=problem)
        except Problem.DoesNotExist:
            return JsonResponse({'error': 'Problem not found'}, status=404)

        if not prob.is_accessible_by(request.user):
            return JsonResponse({'error': 'Access denied'}, status=403)

        source_code = request.POST.get('source', '')
        language_id = request.POST.get('language')
        mode = request.POST.get('mode', 'custom')

        if not source_code:
            return JsonResponse({'error': 'No source code provided'}, status=400)
        if not language_id:
            return JsonResponse({'error': 'No language selected'}, status=400)

        try:
            language = Language.objects.get(id=language_id)
        except Language.DoesNotExist:
            return JsonResponse({'error': 'Invalid language'}, status=400)

        if not hasattr(language, 'judge0') or not language.judge0:
            return JsonResponse({'error': 'This language is not supported for Run Code'}, status=400)

        try:
            if mode == 'sample':
                return self._run_samples(source_code, language, prob)
            else:
                custom_input = request.POST.get('input', '')
                result = call_judge0(source_code, language, custom_input)
                return JsonResponse({'results': [result]})
        except Exception as e:
            logger.error('Run code failed for problem %s: %s', problem, e)
            return JsonResponse({'error': 'Execution failed. Please try again.'}, status=500)

    def _run_samples(self, source_code, language, problem):
        samples = get_sample_data(problem)
        if not samples:
            return JsonResponse({'results': [], 'no_samples': True})

        results = []
        for sample in samples:
            stdin = sample.get('input', '')
            expected = sample.get('output')
            result = call_judge0(source_code, language, stdin, expected)
            result['input'] = stdin
            results.append(result)

        return JsonResponse({'results': results})
