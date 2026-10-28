from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase

from judge.bridge.judge_handler import JudgeHandler
from judge.models import Judge, Language
from judge.models.tests.util import create_problem


class AddLanguagesCommandTestCase(TestCase):
    fixtures = ['judge0_languages.json', 'language_small.json']

    @classmethod
    def setUpTestData(cls):
        cls.normal = create_problem(code='normal', allowed_languages=('CPP17', 'PY3'))
        cls.output_only = create_problem(code='output', allowed_languages=('OUTPUT',))

    def run_command(self, *args):
        out = StringIO()
        call_command('add_languages', *args, stdout=out)
        return out.getvalue()

    def test_adds_languages_to_problems_like_reference(self):
        self.run_command('GO', 'RUST')
        go = Language.objects.get(key='GO')
        self.assertEqual(go.name, 'Go')
        self.assertEqual(set(go.problem_set.values_list('code', flat=True)), {'normal'})
        self.assertTrue(Language.objects.filter(key='RUST').exists())
        self.assertNotIn('GO', self.output_only.allowed_languages.values_list('key', flat=True))

    def test_adds_vnoj_judge_languages(self):
        self.run_command('CPPTHEMIS', 'PASTHEMIS', 'CPP23')
        self.assertEqual(Language.objects.get(key='CPPTHEMIS').common_name, 'C++')
        self.assertEqual(Language.objects.get(key='PASTHEMIS').extension, 'pas')
        self.assertIn('normal', Language.objects.get(key='CPP23').problem_set.values_list('code', flat=True))

    def test_existing_languages_are_untouched(self):
        Language.objects.filter(key='PY3').update(name='Custom Python')
        output = self.run_command('PY3')
        self.assertIn('already exists', output)
        self.assertEqual(Language.objects.get(key='PY3').name, 'Custom Python')

    def test_list_dry_run_and_errors(self):
        listed = self.run_command('--list')
        self.assertIn('GO', listed)
        self.assertNotIn('CPP17 ', listed)

        self.run_command('GO', '--dry-run')
        self.assertFalse(Language.objects.filter(key='GO').exists())

        self.run_command('GO', '--no-problems')
        self.assertFalse(Language.objects.get(key='GO').problem_set.exists())

        with self.assertRaises(CommandError):
            self.run_command('NOPE')
        with self.assertRaises(CommandError):
            self.run_command()


class UpdateRuntimesTestCase(TestCase):
    fixtures = ['judge0_languages.json', 'language_small.json']

    def test_logs_executors_without_language(self):
        handler = JudgeHandler.__new__(JudgeHandler)
        handler.name = 'judge'
        handler.judge = Judge.objects.create(name='judge', auth_key='key')
        handler.executors = {'PY3': [('python3', (3, 12))], 'GO': [('go', (1, 22))], 'RUST': [('rustc', (1, 80))]}

        with self.assertLogs('judge.bridge', 'INFO') as logs:
            handler.update_runtimes()

        self.assertEqual(list(handler.judge.runtimes.values_list('key', flat=True)), ['PY3'])
        self.assertIn('GO, RUST', '\n'.join(logs.output))
