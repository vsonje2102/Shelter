"""resync_households: a city's households saved again from AVNI, unchanged records included."""

from django.test import TestCase

from avni import paths
from avni.provider import sync_context
from avni.sync import household_resync
from avni.tests.support import FakeApi, encounter_record, make_city, make_slum, subject_record
from graphs.models import HouseholdData
from survey import connector

WASTE_KEY = "group_el9cl08/Facility_of_solid_waste_collection"


class HouseholdResyncTests(TestCase):
    def setUp(self):
        self.city = make_city()
        self.slum = make_slum(self.city)
        subject = subject_record(subject_type="Detailed Socio Economic Survey")
        visit = encounter_record(encounter_type="Waste INP", observations={"How do you dispose your solid waste": "Open space"})
        visit["Subject type"] = "Detailed Socio Economic Survey"
        subject["encounters"] = [visit]
        self.api = FakeApi({paths.subject("sub-1"): subject})
        connector.sync_subject("sub-1", sync_context(self.api))
        row = HouseholdData.objects.get(household_number="42")
        row.rhs_data.pop(WASTE_KEY)
        row.save()

    def waste(self):
        return HouseholdData.objects.get(household_number="42").rhs_data.get(WASTE_KEY)

    def test_dry_run_reports_and_writes_nothing(self):
        result = household_resync.run(self.city.id, api=self.api, out=lambda *a: None)
        self.assertEqual(result["after"][WASTE_KEY], 1)
        self.assertIsNone(self.waste())

    def test_apply_saves_unchanged_visits_again(self):
        household_resync.run(self.city.id, apply=True, api=self.api, out=lambda *a: None)
        self.assertEqual(self.waste(), "Open space")

    def test_other_slums_are_left_alone(self):
        result = household_resync.run(self.city.id, slum_ids=[self.slum.id + 1], api=self.api, out=lambda *a: None)
        self.assertEqual(result["households"], 0)
