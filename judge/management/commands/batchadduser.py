import csv

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from judge.models import Organization
from judge.utils.user_import import ImportFileError, STATUS_CREATE, STATUS_ERROR, STATUS_SKIP, create_accounts, \
    credentials_workbook, read_file, resolve_rows


class Command(BaseCommand):
    help = ('batch create users from an .xlsx or .csv file with the columns email, username, fullname and password '
            '(each row needs at least one of the first three); emails that already have an account are skipped; '
            'taken usernames get a number added')

    def add_arguments(self, parser):
        parser.add_argument('input', help='.xlsx or .csv file to read')
        parser.add_argument('output', help='where to write the new accounts and their passwords (.xlsx or .csv)')
        parser.add_argument('--organization', help='slug of an organization the new accounts join')

    def handle(self, *args, **options):
        organization = None
        if options['organization']:
            try:
                organization = Organization.objects.get(slug=options['organization'])
            except Organization.DoesNotExist:
                raise CommandError('No organization with slug %s' % options['organization'])

        with open(options['input'], 'rb') as f:
            try:
                rows = resolve_rows(read_file(File(f, name=options['input'])))
            except ImportFileError as e:
                raise CommandError(str(e))

        for row in rows:
            if row.error:
                row.status = STATUS_ERROR
                self.stderr.write('Line %d: %s' % (row.line, row.error))
            elif row.profile is not None:
                row.status = STATUS_SKIP
                self.stdout.write('Line %d: %s already has an account (%s), skipped'
                                  % (row.line, row.email, row.profile.user.username))
            else:
                row.status = STATUS_CREATE

        created = create_accounts(rows, organization=organization)
        if options['output'].lower().endswith('.xlsx'):
            with open(options['output'], 'wb') as f:
                f.write(credentials_workbook(created))
        else:
            with open(options['output'], 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['username', 'fullname', 'email', 'password'])
                writer.writerows([row.username, row.fullname, row.email, row.password] for row in created)
        self.stdout.write('Created %d accounts, written to %s' % (len(created), options['output']))
