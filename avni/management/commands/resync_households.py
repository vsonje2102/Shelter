"""Save a city's households again from AVNI, unchanged records included (repair after a writer fix)."""

from django.core.management.base import BaseCommand

from avni.sync import household_resync


class Command(BaseCommand):
    help = "Re-save every household of a city (registration and visits) from AVNI. Dry run unless --apply."

    def add_arguments(self, parser):
        parser.add_argument("--city", type=int, required=True, help="master City id")
        parser.add_argument("--slum", type=int, action="append", help="only this slum id (repeatable)")
        parser.add_argument("--apply", action="store_true", help="Write the changes (default is a dry run).")

    def handle(self, *args, **options):
        household_resync.run(options["city"], slum_ids=options["slum"], apply=options["apply"], out=self.stdout.write)
