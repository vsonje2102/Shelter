from django.core.management.base import BaseCommand

from master import home_insights


class Command(BaseCommand):
    help = "Precalculate the staff home page figures (this financial year and last, all cities and each city)."

    def handle(self, *args, **options):
        self.stdout.write("Worked out %d sets of figures." % home_insights.warm())
