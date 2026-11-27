"""Staff home page and the shared shell: who may open what, what must never show, and that the figures are right."""

import datetime
import re

from django.contrib.auth.models import Group, Permission, User
from django.core.cache import caches
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from avni.tests.support import make_city, make_slum
from graphs.models import (
    DashboardData, HouseholdData, MemberData, MemberDataETL, MemberEncounterData, MemberProgramData)
from master import home_insights
from master.templatetags.indian_numbers import indian
from mastersheet.models import ActivityType, CommunityMobilization, SBMUpload, ToiletConstruction
from sponsor.models import Sponsor, SponsorProject, SponsorProjectDetails

LOGIN = "/accounts/login/"
PART = "/admin/insights/part/%s/"
TODAY = datetime.date(2026, 9, 18)
MEMORY_CACHE = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    "auth_throttle": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"},
    "design_preview": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"},
    "home_insights": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "home-insights-tests"},
}


def day(text):
    return datetime.datetime.strptime(text, "%Y-%m-%d").date()


def toilet(slum, number, status="6", agreed=None, completed=None, **dates):
    """A toilet row with the given status; a pre_save signal would otherwise derive it from the dates."""
    row = ToiletConstruction.objects.create(
        slum=slum, household_number=number,
        agreement_date=day(agreed) if agreed else None,
        completion_date=day(completed) if completed else None, **dates)
    ToiletConstruction.objects.filter(pk=row.pk).update(status=status)
    return row


class HomeCase(TestCase):
    def setUp(self):
        self.thane = make_city("Thane")
        self.pune = make_city("Pune")
        self.slum = make_slum(self.thane, "Lokmanya Nagar", "LN1")
        self.other_slum = make_slum(self.pune, "Kasba Peth", "KP1")
        for slum in (self.slum, self.other_slum):
            slum.associated_with_SA = True
            slum.odf_status = "ODF"
            slum.save()
        self.staff = User.objects.create_user("staffer", password="x", is_staff=True)
        self.plain = User.objects.create_user("sunita", password="x")

    def give_mastersheet_access(self, user):
        user.user_permissions.add(Permission.objects.get(codename="can_view_mastersheet"))

    def join_city(self, user, name):
        user.groups.add(Group.objects.get_or_create(name="city:" + name)[0])


class AccessTests(HomeCase):
    def test_signed_out_visitor_is_sent_to_login(self):
        for address in ("/admin/", PART % "toilets", "/admin/insights/no_factsheet/"):
            response = self.client.get(address)
            self.assertEqual(response.status_code, 302, address)
            self.assertTrue(response["Location"].startswith(LOGIN), address)

    def test_non_staff_user_cannot_open_the_page_or_its_figures(self):
        self.client.force_login(self.plain)
        self.assertEqual(self.client.get("/admin/").status_code, 302)
        for part in home_insights.PARTS:
            self.assertEqual(self.client.get(PART % part).status_code, 302, part)

    def test_staff_user_sees_the_page_and_every_part(self):
        self.client.force_login(self.staff)
        self.assertContains(self.client.get("/admin/"), "Programme pulse")
        for part in home_insights.PARTS:
            self.assertContains(self.client.get(PART % part), "data-slot=", msg_prefix=part)

    def test_unknown_part_is_not_found(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(PART % "everything").status_code, 404)

    def test_no_link_to_the_lists_without_mastersheet_access(self):
        toilet(self.slum, "8842", completed="2026-05-01")
        self.client.force_login(self.staff)
        response = self.client.get(PART % "toilets")
        self.assertContains(response, "completed toilets with no family factsheet")
        self.assertNotContains(response, "View list")
        self.assertNotContains(response, "/admin/insights/")

    def test_list_is_refused_without_mastersheet_access(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get("/admin/insights/no_factsheet/").status_code, 403)

    def test_mastersheet_user_gets_the_link_and_the_list(self):
        toilet(self.slum, "8842", completed="2026-05-01")
        self.give_mastersheet_access(self.staff)
        self.join_city(self.staff, "Thane")
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(PART % "toilets"), "/admin/insights/no_factsheet/")
        response = self.client.get("/admin/insights/no_factsheet/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "8842")

    def test_list_shows_only_the_users_own_cities(self):
        toilet(self.slum, "8842", completed="2026-05-01")
        toilet(self.other_slum, "7731", completed="2026-05-01")
        self.give_mastersheet_access(self.staff)
        self.join_city(self.staff, "Thane")
        self.client.force_login(self.staff)
        response = self.client.get("/admin/insights/no_factsheet/?city=%d" % self.pune.id)
        self.assertContains(response, "8842")
        self.assertNotContains(response, "7731")

    def test_unknown_list_is_not_found(self):
        self.give_mastersheet_access(self.staff)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get("/admin/insights/nope/").status_code, 404)
        self.assertEqual(self.client.get("/admin/insights/no_rim/").status_code, 404)


class LoadingTests(HomeCase):
    """Parts already worked out arrive with the page; the rest show placeholders and are fetched one by one."""

    def empty_cache(self):
        caches["home_insights"].clear()

    def test_every_placeholder_is_filled_by_the_part_it_names(self):
        self.client.force_login(self.staff)
        frame = self.client.get("/admin/").content.decode()
        slots = re.findall(r'data-slot="([a-z-]+)" data-part="([a-z]+)"', frame)
        self.assertEqual(len(slots), 22)
        cards = {part: self.client.get(PART % part).content.decode() for part in home_insights.PARTS}
        for slot, part in slots:
            self.assertIn('data-slot="%s"' % slot, cards[part], slot)

    def test_page_opens_with_a_placeholder_for_every_card_when_nothing_is_ready(self):
        self.client.force_login(self.staff)
        response = self.client.get("/admin/?period=2024")
        self.assertContains(response, 'data-part-url="/admin/insights/part/PART/?city=all&amp;period=2024"')
        self.assertContains(response, 'data-part="', count=22)
        self.assertContains(response, 'class="home-card home-bone"', count=7)
        self.assertNotContains(response, "data-ready=")

    @override_settings(CACHES=MEMORY_CACHE)
    def test_page_carries_the_first_screen_parts_that_are_ready_and_never_the_later_ones(self):
        self.empty_cache()
        period = home_insights.period_for()
        for part in ("toilets", "survey", "synced"):
            home_insights.fresh_part(part, None, period)
        self.client.force_login(self.staff)
        response = self.client.get("/admin/")
        self.assertContains(response, "data-ready=", count=2)
        self.assertContains(response, 'data-ready="toilets"')
        self.assertContains(response, 'data-ready="survey"')
        self.assertContains(response, 'id="home-data-toilets"')
        self.assertNotContains(response, 'id="home-data-synced"')

    def test_script_fetches_a_part_only_when_a_slide_that_shows_it_opens(self):
        script = open("static/js/home_insights.js").read()
        self.assertIn("want(pages[current]);", script)
        self.assertNotIn("data-missing", script)

    @override_settings(CACHES=MEMORY_CACHE)
    def test_fetching_a_part_keeps_it_for_the_next_visit(self):
        self.empty_cache()
        self.client.force_login(self.staff)
        period = home_insights.periods()[1]
        self.assertIsNone(home_insights.cached_part("toilets", None, period))
        self.assertContains(self.client.get(PART % "toilets", {"period": period.slug}), period.label)
        self.assertIsNotNone(home_insights.cached_part("toilets", None, period))

    @override_settings(CACHES=MEMORY_CACHE)
    def test_parts_that_ignore_the_period_are_shared_by_every_period(self):
        self.empty_cache()
        current, earlier = home_insights.periods()[:2]
        home_insights.fresh_part("survey", None, current)
        home_insights.fresh_part("toilets", None, current)
        self.assertIsNotNone(home_insights.cached_part("survey", None, earlier))
        self.assertIsNone(home_insights.cached_part("toilets", None, earlier))

    @override_settings(CACHES=MEMORY_CACHE)
    def test_warm_command_precalculates_this_year_last_year_and_all_time(self):
        self.empty_cache()
        toilet(self.slum, "1", completed="2026-05-01")
        call_command("warm_home_insights", verbosity=0)
        offered = home_insights.periods()
        for selection in (None, [self.thane.id]):
            for period in (offered[0], offered[1], offered[-1]):
                self.assertIsNotNone(home_insights.cached_part("toilets", selection, period))
            self.assertIsNotNone(home_insights.cached_part("synced", selection, offered[0]))

    def test_dashboard_script_leaves_the_address_alone(self):
        script = open("static/js/home_insights.js").read()
        for word in ("location.hash", "replaceState", "pushState", "hashchange"):
            self.assertNotIn(word, script)


class PrivacyTests(HomeCase):
    def test_dashboard_shows_no_personal_detail(self):
        toilet(self.slum, "8842", agreed="2026-04-01", completed="2026-05-01")
        SBMUpload.objects.create(slum=self.slum, household_number="8842", name="Sunita Pawar",
                                 phone_number="9876543210", aadhar_number="123412341234")
        HouseholdData.objects.create(household_number="8842", slum=self.slum, city=self.thane,
                                     submission_date=timezone.now(), rhs_data={"name": "Sunita Pawar"})
        member = MemberData.objects.create(slum=self.slum, household_number="8842", member_first_name="Sunita Pawar")
        MemberProgramData.objects.create(slum=self.slum, member=member, created_date=day("2026-05-02"),
                                         program_data={"Name of the surveyor": "Sunita Pawar"})
        self.give_mastersheet_access(self.staff)
        self.client.force_login(self.staff)
        for address in ["/admin/?city=all"] + [PART % part + "?city=all" for part in home_insights.PARTS]:
            response = self.client.get(address)
            for secret in ("Sunita Pawar", "9876543210", "123412341234", "8842"):
                self.assertNotContains(response, secret, msg_prefix=address)


class SelectionTests(HomeCase):
    def test_opens_on_my_cities_for_a_user_in_a_city_group(self):
        self.join_city(self.staff, "Thane")
        self.client.force_login(self.staff)
        response = self.client.get(PART % "survey")
        self.assertEqual(response.context["chosen"], "mine")
        self.assertEqual(response.context["figures"]["open"]["cities"], 1)

    def test_opens_on_all_cities_without_a_city_group(self):
        self.client.force_login(self.staff)
        response = self.client.get(PART % "survey")
        self.assertEqual(response.context["chosen"], "all")
        self.assertEqual(response.context["figures"]["open"]["cities"], 2)

    def test_one_city_can_be_chosen(self):
        self.client.force_login(self.staff)
        response = self.client.get("/admin/?city=%d" % self.pune.id)
        self.assertEqual(response.context["single_city"], self.pune.id)

    def test_bad_city_falls_back_to_the_default(self):
        self.client.force_login(self.staff)
        for bad in ("abc", "999999", "99999999999999999999", "mine"):
            response = self.client.get("/admin/", {"city": bad})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["chosen"], "all")

    def test_period_defaults_to_the_current_financial_year_and_ignores_nonsense(self):
        self.client.force_login(self.staff)
        current = home_insights.period_for()
        for asked in ("", "abc", "1800"):
            self.assertEqual(self.client.get("/admin/", {"period": asked}).context["period"], current)
        earlier = home_insights.periods()[2]
        self.assertEqual(self.client.get("/admin/", {"period": earlier.slug}).context["period"], earlier)
        self.assertEqual(self.client.get("/admin/", {"period": "all"}).context["period"].label, "All time")


class ShellTests(HomeCase):
    """The left menu, the title-and-breadcrumb row and the tab icon, shared by every signed-in page."""

    def test_staff_user_gets_the_dashboard_entry_and_no_mastersheet_entries(self):
        self.client.force_login(self.staff)
        response = self.client.get("/admin/")
        self.assertContains(response, 'id="shelter-nav"')
        self.assertContains(response, ">Dashboard<")
        self.assertNotContains(response, "/mastersheet/show/mastersheet/")
        self.assertNotContains(response, "/slum-versions/")

    def test_mastersheet_user_gets_the_analytics_entries(self):
        self.give_mastersheet_access(self.staff)
        self.client.force_login(self.staff)
        self.assertContains(self.client.get("/admin/"), "/mastersheet/show/mastersheet/")

    def test_log_out_stays_in_the_top_bar(self):
        self.client.force_login(self.staff)
        response = self.client.get("/admin/")
        self.assertContains(response, 'id="shelter-top"')
        self.assertContains(response, "Log out")

    def test_older_pages_sit_under_archived_and_the_vulnerability_report_is_gone(self):
        self.give_mastersheet_access(self.staff)
        self.staff.user_permissions.add(Permission.objects.get(codename="view_survey"))
        self.client.force_login(self.staff)
        page = self.client.get("/admin/").content.decode()
        archived = page[page.index('id="side-archived"'):page.index("</aside>")]
        for address in ("/admin/surveymapping/", "/graphs/covid_data", "/helpers/photo-upload/", "/admin/report/"):
            self.assertIn(address, archived)
            self.assertNotIn(address, page[:page.index('id="side-archived"')])
        self.assertNotIn("vulnerabilityReport", page)

    def test_pages_get_a_title_and_a_breadcrumb_that_starts_at_home(self):
        self.client.force_login(self.staff)
        response = self.client.get("/admin/")
        self.assertContains(response, '<span class="page-head-title">Programme pulse</span>')
        self.assertContains(response, '<div class="breadcrumbs" data-auto><a href="/home">Home</a></div>')

    def test_tab_icon_shows_which_server_this_is(self):
        self.client.force_login(self.staff)
        for environment, icon in (("local", "sa-icon-local.png"), ("uat", "sa-icon-uat.png"), ("prod", "sa-icon.png")):
            with override_settings(SITE_ENVIRONMENT=environment):
                self.assertContains(self.client.get("/admin/"), 'rel="icon" type="image/png" href="/static/images/%s"' % icon)

    def test_reports_page_offers_the_three_reports(self):
        self.client.force_login(self.staff)
        response = self.client.get("/reports/")
        for name in ("Slum Factsheet", "Family Factsheet", "Donor Report"):
            self.assertContains(response, name)
        self.assertContains(response, "/admin/familyrportgenerate/")
        self.assertNotContains(response, "Upcoming")


class ToiletFigureTests(HomeCase):
    """The toilets part for FY 2026-27 as seen on 18 Sep 2026, from eight toilet rows in Thane."""

    def setUp(self):
        super().setUp()
        toilet(self.slum, "1", agreed="2026-01-01", completed="2026-01-11", use_of_toilet=day("2026-02-01"))
        toilet(self.slum, "2", agreed="2026-02-01", completed="2026-02-21", factsheet_done=day("2026-03-01"))
        toilet(self.slum, "3", agreed="2026-03-01", completed="2026-04-10")
        toilet(self.slum, "4", agreed="2025-03-01", completed="2025-03-31")
        toilet(self.slum, "5", agreed="2025-10-01", completed="2025-10-31")
        toilet(self.slum, "6", status="2", agreed="2026-05-01")
        toilet(self.slum, "7", status="5", agreed="2026-06-01")
        toilet(self.slum, "8", status="5", agreed="2026-09-10")
        self.figures = home_insights.build_part("toilets", today=TODAY)

    def part(self, **options):
        return home_insights.build_part("toilets", today=TODAY, **options)

    def test_period_is_the_financial_year(self):
        self.assertEqual(self.figures["period"], {
            "key": 2026, "slug": "2026", "label": "FY 2026–27", "current": True, "all_time": False, "upto": "18 Sep 2026"})
        self.assertEqual(home_insights.period_for(today=day("2026-03-31")).key, 2025)
        offered = home_insights.periods(TODAY)
        self.assertEqual([period.slug for period in offered][:3] + [offered[-1].slug], ["2026", "2025", "2024", "all"])

    def test_completed_and_agreed_count_the_period_with_this_month_alongside(self):
        tiles = self.figures["tiles"]
        self.assertEqual(tiles["completed"], {"value": 1, "previous": 0, "change": None, "month": 0})
        self.assertEqual(tiles["agreed"], {"value": 3, "previous": 0, "change": None, "month": 1})

    def test_an_earlier_year_compares_with_the_whole_year_before_and_has_no_this_month(self):
        tiles = self.part(period_key=2025)["tiles"]
        self.assertEqual(tiles["completed"], {"value": 3, "previous": 1, "change": 200, "month": None})

    def test_all_time_counts_everything_and_compares_with_nothing(self):
        figures = self.part(period_key=home_insights.ALL_TIME)
        self.assertEqual(figures["tiles"]["completed"], {"value": 5, "previous": None, "change": None, "month": None})
        self.assertEqual(figures["tiles"]["agreed"], {"value": 8, "previous": None, "change": None, "month": None})
        self.assertEqual((figures["tiles"]["build_days"]["value"], figures["tiles"]["build_days"]["slower"]), (30, None))
        self.assertEqual(figures["period"]["label"], "All time")
        self.assertEqual(figures["heat"]["labels"], ["24–25", "25–26", "26–27"])
        self.assertEqual(figures["heat"]["rows"], [{"label": "Thane", "values": [1, 3, 1], "total": 5}])
        self.assertEqual((figures["momentum"]["labels"], figures["momentum"]["bars"], figures["momentum"]["line"]),
                         (["24–25", "25–26", "26–27"], [1, 3, 1], [1, 4, 3]))

    def test_build_days_is_the_median_against_last_year(self):
        tile = self.figures["tiles"]["build_days"]
        self.assertEqual((tile["value"], tile["last"], tile["slower"], tile["last_label"]), (40, 20, 20, "FY 2025–26"))

    def test_current_work_figures_are_as_of_today(self):
        self.assertEqual(self.figures["tiles"]["under_construction"], {"value": 2, "overdue": 1, "overdue_days": 60})
        self.assertEqual(self.figures["progress"]["rows"], [{"label": "Thane", "under": 2, "overdue": 1, "completed": 0, "agreed": 1}])

    def test_in_use_counts_recorded_use_dates_in_the_period(self):
        self.assertEqual(self.figures["tiles"]["in_use"], {"percent": 0, "used": 0, "completed": 1})
        self.assertEqual(self.part(period_key=2025)["tiles"]["in_use"], {"percent": 33, "used": 1, "completed": 3})

    def test_momentum_counts_each_month_and_names_its_peak(self):
        momentum = self.figures["momentum"]
        self.assertEqual(momentum["labels"], ["Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"])
        self.assertEqual(momentum["bars"], [1, 0, 0, 0, 0, 0] + [None] * 6)
        self.assertEqual(momentum["line"], [0, 1, 1, 0, 0, 1] + [None] * 6)
        self.assertEqual(momentum["finding"], "May 2026 had 1 new agreements, the most in any month of FY 2026–27")
        earlier = self.part(period_key=2025)["momentum"]
        self.assertEqual((earlier["months"][0], earlier["bars"]), ("April 2025", [0, 0, 0, 0, 0, 0, 1, 0, 0, 1, 1, 0]))

    def test_where_work_is_happening_runs_april_to_march_and_leaves_future_months_empty(self):
        heat = self.figures["heat"]
        self.assertEqual(heat["labels"][0], "Apr")
        self.assertEqual(heat["rows"], [{"label": "Thane", "values": [1, 0, 0, 0, 0, 0] + [None] * 6, "total": 1}])
        self.assertEqual(heat["finding"], "Toilet work in 1 city in FY 2026–27")

    def test_where_work_is_happening_lists_only_cities_with_toilet_work(self):
        toilet(self.other_slum, "9", status="5", agreed="2025-01-10")
        rows = self.part()["heat"]["rows"]
        self.assertEqual([(row["label"], row["total"]) for row in rows], [("Thane", 1), ("Pune", 0)])
        earlier = self.part(period_key=2023)["heat"]
        self.assertEqual((earlier["rows"], earlier["finding"]), ([], "No toilet work recorded in FY 2023–24"))

    def test_cancellation_rate_is_of_the_agreements_signed_in_the_period(self):
        self.assertEqual(self.figures["after"]["cancelled"], [{"label": "Thane", "percent": 33.3, "count": 1}])

    def test_follow_through_rows(self):
        rows = {row["label"]: row["count"] for row in self.figures["after"]["rows"]}
        self.assertEqual(rows, {"Completed": 1, "Factsheet done": 0, "Connection recorded": 0, "Confirmed in use": 0})

    def test_gap_counts_match_the_lists_behind_them_and_the_heading_is_worked_out(self):
        city_ids = [self.thane.id, self.pune.id]
        gaps = self.figures["gaps"]
        for gap in gaps["rows"]:
            self.assertEqual(gap["count"], len(home_insights.gap_rows(gap["key"], city_ids, TODAY)["rows"]), gap["key"])
        self.assertEqual({gap["key"]: gap["count"] for gap in gaps["rows"]}, {"open_builds": 1, "no_factsheet": 4})
        self.assertEqual(gaps["finding"], "1 builds open over 60 days, 4 completed toilets without a factsheet")
        self.assertEqual(home_insights.build_part("toilets", [self.pune.id], today=TODAY)["gaps"]["finding"], "Nothing is waiting right now")

    def test_one_city_selection_leaves_the_other_out(self):
        tiles = home_insights.build_part("toilets", [self.pune.id], today=TODAY)["tiles"]
        self.assertEqual(tiles["completed"]["value"], 0)
        self.assertIsNone(tiles["build_days"]["value"])


class OtherFigureTests(HomeCase):
    def test_a_cup_counts_as_given_on_either_enrolment_form(self):
        for number, answers in enumerate((
                {home_insights.CUP_GIVEN: "Yes"},                                           # older form
                {home_insights.CUP_PRODUCTS: home_insights.CUP_PRODUCT},                    # newer form, no cup question
                {home_insights.CUP_GIVEN: "No"}, {})):
            member = MemberData.objects.create(slum=self.slum, household_number=str(700 + number))
            MemberProgramData.objects.create(slum=self.slum, member=member, created_date=day("2026-05-02"), program_data=answers)
        figures = home_insights.build_part("mhm", today=TODAY)
        self.assertEqual((figures["enrolled"]["value"], figures["cups"]["value"]), (4, 2))

    def test_menstrual_hygiene_counts_enrolments_cups_and_follow_ups_in_the_period(self):
        asked = home_insights
        for number, (joined, cup) in enumerate((("2026-04-20", "Yes"), ("2026-09-03", "No"), ("2025-05-01", "Yes"))):
            member = MemberData.objects.create(slum=self.slum, household_number=str(number))
            programme = MemberProgramData.objects.create(
                slum=self.slum, member=member, created_date=day(joined), program_data={asked.CUP_GIVEN: cup})
            MemberEncounterData.objects.create(
                slum=self.slum, member=member, program=programme, created_date=day(joined) + datetime.timedelta(days=10),
                encounter_data={asked.CUP_USED: cup})
        figures = home_insights.build_part("mhm", today=TODAY)
        self.assertEqual(figures["enrolled"], {"value": 2, "previous": 1, "change": 100, "month": 1})
        self.assertEqual((figures["cups"]["value"], figures["followups"]["value"], figures["using_percent"]), (1, 2, 50))
        self.assertEqual(figures["finding"], "50% of follow-up visits in FY 2026–27 found the cup in use")
        self.assertEqual(figures["heat"]["rows"], [{"label": "Thane", "values": [1, 0, 0, 0, 0, 1] + [None] * 6, "total": 2}])
        self.assertEqual(sum(count or 0 for count in figures["momentum"]["bars"]), 1)
        whole = home_insights.build_part("mhm", period_key=home_insights.ALL_TIME, today=TODAY)
        self.assertEqual(whole["enrolled"], {"value": 3, "previous": None, "change": None, "month": None})
        self.assertEqual(whole["heat"]["labels"], ["25–26", "26–27"])

    def test_mobilisation_counts_the_period_and_this_month(self):
        kind = ActivityType.objects.create(name="Corner Meeting", key="cm", display_order=1)
        for held in ("2026-04-05", "2026-09-02", "2025-06-01", "2025-12-01"):
            CommunityMobilization.objects.create(slum=self.slum, activity_type=kind, activity_date=day(held))
        figures = home_insights.build_part("mobilisation", today=TODAY)
        self.assertEqual(figures["sessions"], {"value": 2, "previous": 1, "change": 100, "month": 1})
        self.assertEqual(figures["activities"], [{"label": "Corner Meeting", "count": 2}])

    def test_city_with_no_survey_summary_reads_no_data_not_zero(self):
        DashboardData.objects.create(slum=self.slum, city=self.thane, individual_toilet_coverage=60,
                                     ctb_coverage=30, open_defecation_coverage=10)
        ladder = home_insights.build_part("survey", today=TODAY)
        self.assertEqual([row["label"] for row in ladder["rows"]], ["Thane"])
        self.assertEqual(ladder["rows"][0]["shares"], [60.0, 0.0, 30.0, 10.0, 0.0])
        self.assertEqual(ladder["no_data"], ["Pune"])
        self.assertEqual(ladder["open"], {"households": 10, "percent": 10, "cities_with_data": 1, "cities": 2})

    def test_scope_of_work_counts_households_on_a_community_toilet_or_open_defecation(self):
        DashboardData.objects.create(slum=self.slum, city=self.thane, individual_toilet_coverage=60,
                                     shared_group_toilet_coverage=5, ctb_coverage=30, open_defecation_coverage=5)
        toilet(self.slum, "21", status="6", agreed="2025-01-01", completed="2025-02-01")
        survey = home_insights.build_part("survey", today=TODAY)
        self.assertEqual(survey["need"], {"households": 35, "community": 30, "open": 5, "percent": 35,
                                          "cities_with_data": 1, "cities": 2})
        self.assertEqual(survey["scope"]["rows"], [
            {"label": "Thane", "community": 30, "open": 5, "need": 35, "surveyed": 100, "percent": 35, "built": 1}])
        self.assertEqual(survey["scope"]["finding"], "35% of surveyed households here have no toilet of their own")

    def test_vulnerable_members_count_in_the_year_the_toilet_was_agreed_or_they_joined(self):
        toilet(self.slum, "12", status="6", agreed="2026-03-20", completed="2026-05-02")   # agreed last year
        toilet(self.slum, "40", status="6", agreed="2026-04-10", completed="2026-06-01")
        people = (
            ("012", dict(marital_status="Widow", age=64, gender="Female"), None),            # toilet, FY 2025-26
            ("40", dict(disability="Yes", age=30, gender="Male"), None),                     # toilet, FY 2026-27
            ("30", dict(disability="Yes", age=30, gender="Female"), "2026-05-05"),           # cup programme only
            ("31", dict(age=70, gender="Male"), None),                                       # not reached
            ("32", dict(age=25, gender="Female", menstruation_status="Yes"), None),          # not vulnerable
        )
        for number, facts, joined in people:
            member = MemberData.objects.create(slum=self.slum, household_number=number, member_first_name="Kamalabai")
            MemberDataETL.objects.create(slum=self.slum, member=member, household_number=None,
                                         member_first_name="Kamalabai", mhm_program_enrollment="No", **facts)
            if joined:
                MemberProgramData.objects.create(slum=self.slum, member=member, created_date=day(joined), program_data={})
        figures = home_insights.build_part("people", today=TODAY)
        self.assertEqual(figures["rows"], [
            {"label": "Widows", "total": 1, "toilet": 0, "programme": 0, "ever": 1, "reached": 0},
            {"label": "With a disability", "total": 2, "toilet": 1, "programme": 1, "ever": 2, "reached": 2},
            {"label": "Aged 60 or over", "total": 2, "toilet": 0, "programme": 0, "ever": 1, "reached": 0},
        ])
        self.assertEqual((figures["recorded"], figures["reached"], figures["ever"]), (4, 2, 3))
        earlier = home_insights.build_part("people", period_key=2025, today=TODAY)
        self.assertEqual((earlier["reached"], earlier["rows"][0]["toilet"]), (1, 1))
        whole = home_insights.build_part("people", period_key=home_insights.ALL_TIME, today=TODAY)
        self.assertEqual((whole["reached"], whole["whole"]), (3, True))
        self.assertEqual(home_insights.build_part("mhm", today=TODAY)["waiting"], 1)
        self.client.force_login(self.staff)
        self.assertNotContains(self.client.get(PART % "people"), "Kamalabai")

    def test_funders_are_ranked_by_toilets_completed_and_schemes_are_left_out(self):
        for number in ("3", "4", "9"):
            toilet(self.slum, number, status="6", agreed="2025-01-01", completed="2025-02-01")
        for name, households in (("SBM Toilets", ["1", "2", "3"]), ("Sunvac", ["4", "5"])):
            sponsor = Sponsor.objects.create(organization_name=name, address="x", user=self.staff)
            project = SponsorProject.objects.create(
                name=name + " project", project_type="1", funds_sponsored=0, status="2",
                created_by=self.staff, sponsor=sponsor)
            SponsorProjectDetails.objects.create(
                sponsor=sponsor, sponsor_project=project, slum=self.slum, household_code=households)
        funders = home_insights.build_part("funders", today=TODAY)
        self.assertEqual(funders["rows"], [{"label": "Sunvac", "count": 1}])
        self.assertEqual((funders["total"], funders["given"], funders["funders"]), (1, 3, 1))

    def test_last_synced_gives_dates_and_recency_only(self):
        toilet(self.slum, "8", status="5", agreed="2026-09-10")
        row = home_insights.build_part("synced", today=TODAY)["rows"][0]
        self.assertEqual(row["label"], "Thane")
        self.assertEqual(row["cells"][2], {"date": "10 Sep 2026", "age": "8 days", "state": "ok", "days": 8})
        self.assertEqual(row["cells"][0]["age"], "No data")


class HelperTests(SimpleTestCase):
    def test_day_gap_drops_missing_and_impossible_gaps(self):
        self.assertEqual(home_insights.day_gap(day("2026-01-01"), day("2026-01-11")), 10)
        self.assertIsNone(home_insights.day_gap(None, day("2026-01-11")))
        self.assertIsNone(home_insights.day_gap(day("2026-01-11"), day("2026-01-01")))
        self.assertIsNone(home_insights.day_gap(day("2010-01-01"), day("2026-01-01")))

    def test_recency_words_and_state(self):
        self.assertEqual(home_insights.recency(day("2026-09-18"), TODAY)["age"], "today")
        self.assertEqual(home_insights.recency(day("2026-08-01"), TODAY)["state"], "warn")
        self.assertEqual(home_insights.recency(day("2024-09-01"), TODAY)["age"], "2 years")
        self.assertEqual(home_insights.recency(day("2024-09-01"), TODAY)["state"], "bad")

    def test_a_year_before_copes_with_29_february(self):
        self.assertEqual(home_insights.a_year_before(day("2024-02-29")), day("2023-02-28"))

    def test_indian_grouping(self):
        self.assertEqual([indian(value) for value in (999, 27335, 112374, None)], ["999", "27,335", "1,12,374", "–"])
