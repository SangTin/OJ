import json
import os

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from judge.models import Language

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'fixtures')
# DMOJ's standard languages, plus the executors only VNOJ's judge-server has (C23, CPPTHEMIS, ...).
FIXTURES = ('language_all.json', 'language_vnoj.json')


class Command(BaseCommand):
    help = ('add languages from the bundled language list without touching existing ones, '
            'and allow them on the problems that allow a reference language')

    def add_arguments(self, parser):
        parser.add_argument('keys', nargs='*', help='language keys to add (the judge executor ids, e.g. GO RUST)')
        parser.add_argument('--list', action='store_true', help='list the keys that can be added and exit')
        parser.add_argument('--like', default='CPP17',
                            help='allow each new language on the problems that allow this language (default: CPP17)')
        parser.add_argument('--no-problems', action='store_true', help="don't allow the new languages on any problem")
        parser.add_argument('--dry-run', action='store_true', help='only show what would be done')

    def handle(self, *args, **options):
        available = {}
        for fixture in FIXTURES:
            with open(os.path.join(FIXTURE_DIR, fixture)) as f:
                available.update((item['fields']['key'], item['fields']) for item in json.load(f)
                                 if item['model'] == 'judge.language')
        existing = set(Language.objects.values_list('key', flat=True))
        addable = sorted(set(available) - existing)

        if options['list']:
            for key in addable:
                self.stdout.write('%-8s %s' % (key, available[key]['name']))
            return

        if not options['keys']:
            raise CommandError('Give the language keys to add, or use --list to see them.')
        unknown = [key for key in options['keys'] if key not in available]
        if unknown:
            raise CommandError('Not in the bundled language list: %s. Add them in the admin instead.'
                               % ', '.join(unknown))

        problems = None
        if not options['no_problems']:
            try:
                problems = list(Language.objects.get(key=options['like']).problem_set.values_list('id', flat=True))
            except Language.DoesNotExist:
                raise CommandError('Reference language %s does not exist; use --like or --no-problems.'
                                   % options['like'])

        with transaction.atomic():
            for key in options['keys']:
                if key in existing:
                    self.stdout.write('%s already exists, skipped' % key)
                    continue
                fields = available[key]
                self.stdout.write('Adding %s (%s)%s' % (
                    key, fields['name'],
                    '' if problems is None else ', allowed on %d problems' % len(problems)))
                if options['dry_run']:
                    continue
                language = Language.objects.create(**fields)
                if problems is not None:
                    language.problem_set.add(*problems)

        if not options['dry_run']:
            self.stdout.write('Done. Restart the judges (or bridged) so they report the new runtimes.')
