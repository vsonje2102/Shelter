"""Figures for the staff home page. Aggregates only: nothing here names a household or a person.

The page is made of independent parts (PARTS). Each is worked out, cached and fetched on its own, so a slow
part never holds up the others, and parts that do not depend on the period are shared by every period.
"""

import datetime
import json
import threading
import time
from collections import Counter, OrderedDict, defaultdict, namedtuple
from statistics import median

from django.conf import settings
from django.core.cache import caches
from django.db import connection
from django.db.models import Count, Max, Q, Sum
from django.utils import timezone

from graphs.models import (
    DashboardData, FollowupData, HouseholdData, MemberDataETL, MemberEncounterData, MemberProgramData)
from master.models import City, Slum
from master.templatetags.indian_numbers import indian
from mastersheet.models import CommunityMobilization, ToiletConstruction
from sponsor.models import SponsorProjectDetails

SLUM_CITY = "slum__electoral_ward__administrative_ward__city"
COMPLETED, CANCELLED, UNDER_CONSTRUCTION = "6", "2", "5"
FRESH_SECONDS, PAST_FRESH_SECONDS, KEEP_SECONDS = 6 * 3600, 7 * 86400, 30 * 86400
PERIODS_OFFERED = 8
ALL_TIME = 0
CUP_GIVEN = "Have you received a Cup from SA ?"    # enrolment answer on the older form
CUP_PRODUCTS, CUP_PRODUCT = "Have you received any menstrual products from SA?", "Menstrual cup"    # newer form
CUP_USED = "Did you use cup in the previous menstruation?"
OPEN_BUILD_DAYS = 60
LONGEST_BUILD_DAYS = 1500
FRESH_DAYS, AGEING_DAYS = 30, 180
PROGRESS_DAYS = 90
ELDER_AGE = 60
TOP_FUNDERS = 6
TOP_ACTIVITIES = 5

PARTS = ("toilets", "mhm", "mobilisation", "survey", "people", "funders", "synced")
LATER_PARTS = ("synced",)    # every card is on a later slide: fetched when it opens
DATED_PARTS = ("toilets", "mhm", "mobilisation", "people")    # the rest read the same whatever period is chosen

Period = namedtuple("Period", "key slug label start end current")
Span = namedtuple("Span", "label name start end")
Toilet = namedtuple("Toilet", "city status agreed first second third completed used connected factsheet")
TOILET_FIELDS = (
    SLUM_CITY + "_id", "status", "agreement_date", "phase_one_material_date", "phase_two_material_date",
    "phase_three_material_date", "completion_date", "use_of_toilet", "toilet_connected_to", "factsheet_done",
)
STAGES = (
    ("Agreement → 1st material", "between the agreement and the first material delivery", "agreed", "first"),
    ("1st → 2nd material", "between the first and second material delivery", "first", "second"),
    ("2nd → 3rd material", "between the second and third material delivery", "second", "third"),
    ("3rd material → done", "between the third material delivery and completion", "third", "completed"),
)
LADDER = (
    ("Own toilet", "own", "individual_toilet_coverage"),
    ("Shared", "shared", "shared_group_toilet_coverage"),
    ("Community toilet", "community", "ctb_coverage"),
    ("Open defecation", "open", "open_defecation_coverage"),
    ("Other", "other", "other_services_toilet_coverage"),
)
GAPS = OrderedDict([
    ("open_builds", "toilets under construction for over %d days" % OPEN_BUILD_DAYS),
    ("no_factsheet", "completed toilets with no family factsheet"),
])


# ---------- small helpers ----------

def percent(part, whole):
    return int(round(100.0 * part / whole)) if whole else None


def middle(values):
    return int(round(median(values))) if values else None


def day_gap(start, end):
    """Days between two dates, or None when either is missing or the gap is impossible."""
    if not start or not end:
        return None
    days = (end - start).days
    return days if 0 <= days <= LONGEST_BUILD_DAYS else None


def as_date(value):
    if isinstance(value, datetime.datetime):
        return timezone.localtime(value).date() if timezone.is_aware(value) else value.date()
    return value


def day_label(day):
    return "%d %s" % (day.day, day.strftime("%b %Y")) if day else ""


def recency(day, today):
    """How old a date is, as the words and state a pill shows."""
    day = as_date(day)
    if not day:
        return {"date": "", "age": "No data", "state": "none", "days": None}
    days = max((today - day).days, 0)
    if days < 60:
        age = "today" if days == 0 else "%d day%s" % (days, "" if days == 1 else "s")
    elif days < 730:
        age = "%d months" % (days // 30)
    else:
        age = "%d years" % (days // 365)
    state = "ok" if days <= FRESH_DAYS else "warn" if days <= AGEING_DAYS else "bad"
    return {"date": day_label(day), "age": age, "state": state, "days": days}


def a_year_before(day):
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return day.replace(year=day.year - 1, day=28)


def within(day, start, end):
    return bool(day) and start <= day <= end


def cities_phrase(count):
    return "%d cit%s" % (count, "y" if count == 1 else "ies")


# ---------- periods ----------

def period_for(key=None, today=None):
    """A financial year, 1 April to 31 March; `key` is the year it starts in (default: the current one).
    `ALL_TIME` gives everything on record up to today."""
    today = today or timezone.localdate()
    this = today.year if today.month >= 4 else today.year - 1
    if key == ALL_TIME:
        return Period(ALL_TIME, "all", "All time", datetime.date(1990, 4, 1), today, False)
    key = this if key is None else key
    label = "FY %d–%s" % (key, str(key + 1)[-2:])
    return Period(key, str(key), label, datetime.date(key, 4, 1), datetime.date(key + 1, 3, 31), key == this)


def periods(today=None):
    """What the filter offers: the current financial year, the seven before it, then all time."""
    current = period_for(today=today)
    return [period_for(current.key - back, today) for back in range(PERIODS_OFFERED)] + [period_for(ALL_TIME, today)]


def period_words(period):
    return "all years" if period.key == ALL_TIME else period.label


def spans(period, today, first_day=None):
    """The columns a period is drawn in: its months, or one per financial year for all time."""
    if period.key == ALL_TIME:
        first = period_for(today=first_day or today).key
        return [
            Span("%02d–%02d" % (year % 100, (year + 1) % 100), period_for(year, today).label,
                 datetime.date(year, 4, 1), datetime.date(year + 1, 3, 31))
            for year in range(first, period_for(today=today).key + 1)
        ]
    starts = [datetime.date(period.key + (0 if month >= 4 else 1), month, 1) for month in (4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3)]
    return [
        Span(start.strftime("%b"), start.strftime("%B %Y"), start,
             (start.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1))
        for start in starts
    ]


def tally(days, columns):
    """How many of these dates fall in each column."""
    return [sum(1 for day in days if within(day, column.start, column.end)) for column in columns]


def counted(value, previous, this_month):
    """A count for the period, the same stretch a year earlier (None for all time: nothing to compare with),
    and this month's share (current period only)."""
    change = percent(value - previous, previous) if previous is not None else None
    return {"value": value, "previous": previous, "change": change, "month": this_month}


# ---------- selections, cache, parts ----------

def user_city_ids(user):
    """Cities named by the user's city groups (`city:Pune`), the app's existing convention."""
    names = [name.split(":")[-1].strip() for name in user.groups.values_list("name", flat=True)]
    return list(City.objects.filter(name__city_name__in=names).values_list("id", flat=True))


def city_names(city_ids):
    cities = City.objects.select_related("name")
    if city_ids is not None:
        cities = cities.filter(id__in=city_ids)
    return {city.id: city.name.city_name for city in cities}


def cache_key(part, city_ids, period):
    when = period.slug if part in DATED_PARTS else "any"
    return "v24:%s:%s:%s" % (part, when, "all" if city_ids is None else "-".join(str(pk) for pk in sorted(city_ids)))


def cached_part(part, city_ids=None, period=None):
    """A part already worked out, or None. An old entry is still returned, and rebuilt in the background."""
    period = period or period_for()
    entry = caches["home_insights"].get(cache_key(part, city_ids, period))
    if entry is None:
        return None
    fresh_for = PAST_FRESH_SECONDS if part in DATED_PARTS and not period.current and period.key != ALL_TIME else FRESH_SECONDS
    if time.time() - entry["built"] > fresh_for:
        rebuild_later(part, city_ids, period)
    return entry["figures"]


def fresh_part(part, city_ids=None, period=None):
    """Work one part out now and keep it."""
    period = period or period_for()
    figures = build_part(part, city_ids, period.key)
    caches["home_insights"].set(cache_key(part, city_ids, period), {"built": time.time(), "figures": figures}, KEEP_SECONDS)
    return figures


def part_for(part, city_ids=None, period=None):
    found = cached_part(part, city_ids, period)
    return fresh_part(part, city_ids, period) if found is None else found


def rebuild_later(part, city_ids, period):
    """Refresh one entry off the request; one rebuild at a time per entry."""
    store, busy = caches["home_insights"], cache_key(part, city_ids, period) + ":busy"
    if not store.add(busy, 1, 300):
        return

    def work():
        try:
            fresh_part(part, city_ids, period)
        finally:
            store.delete(busy)
            connection.close()

    threading.Thread(target=work, daemon=True).start()


def warm(today=None):
    """Precalculate what most people open: this year, last year and all time, for all cities and for each city
    with toilet records. Parts that ignore the period are worked out once per selection."""
    cities = set(ToiletConstruction.objects.order_by().values_list(SLUM_CITY, flat=True).distinct()) - {None}
    selections = [None] + [[city] for city in sorted(cities)]
    wanted = periods(today)
    built = 0
    for city_ids in selections:
        for part in PARTS:
            for period in (wanted[:2] + wanted[-1:] if part in DATED_PARTS else wanted[:1]):
                fresh_part(part, city_ids, period)
                built += 1
    return built


def build_part(part, city_ids=None, period_key=None, today=None):
    today = today or timezone.localdate()
    period = period_for(period_key, today)
    figures = BUILDERS[part](city_names(city_ids), period, today)
    figures["generated"] = day_label(today)
    figures["period"] = {
        "key": period.key, "slug": period.slug, "label": period.label, "current": period.current,
        "all_time": period.key == ALL_TIME, "upto": day_label(min(today, period.end)),
    }
    return figures


def build_insights(city_ids=None, period_key=None, today=None):
    """Every part at once, keyed by part name."""
    return {part: build_part(part, city_ids, period_key, today) for part in PARTS}


# ---------- shared charts ----------

def momentum_chart(bar_days, line_days, period, upto, first_day, bar_word, line_word):
    """Two series per month of the period (per financial year for all time). Months still to come hold None."""
    columns = spans(period, upto, first_day)
    stretch, kind = period_words(period), "year" if period.key == ALL_TIME else "month"
    counts = tally(bar_days, columns), tally(line_days, columns)
    bars, line = ([None if column.start > upto else count for column, count in zip(columns, series)] for series in counts)
    peak = max(range(len(columns)), key=lambda at: line[at] or 0) if columns else 0
    if columns and line[peak]:
        finding = "%s had %s %s, the most in any %s of %s" % (
            columns[peak].name, indian(line[peak]), line_word, kind, stretch)
    elif any(bars):
        finding = "No %s recorded in %s" % (line_word, stretch)
    else:
        finding = "No %s or %s recorded in %s" % (line_word, bar_word, stretch)
    return {
        "labels": [column.label for column in columns], "months": [column.name for column in columns],
        "bars": bars, "line": line, "peak": peak if columns and line[peak] else None, "finding": finding,
    }


def work_grid(events, working, names, period, today, first_day, work):
    """Events per city per month of the period (per financial year for all time), for every city in `working`.
    Columns still to come hold None."""
    columns = spans(period, today, first_day)
    by_city = defaultdict(list)
    for city, day in events:
        by_city[city].append(day)
    rows = []
    for city in working:
        counts = tally(by_city[city], columns)
        values = [None if column.start > today else count for column, count in zip(columns, counts)]
        rows.append({"label": names[city], "values": values, "total": sum(counts)})
    rows.sort(key=lambda row: (-row["total"], row["label"]))
    finding = (
        "%s in %s in %s" % (work, cities_phrase(len(rows)), period_words(period))
        if rows else "No %s recorded in %s" % (work.lower(), period_words(period))
    )
    return {
        "labels": [column.label for column in columns], "months": [column.name for column in columns],
        "rows": rows, "peak": max([value or 0 for row in rows for value in row["values"]] or [0]), "finding": finding,
    }


# ---------- part: toilets ----------

def toilets_part(names, period, today):
    """Everything drawn from toilet construction records. Period figures run from its start to today or its end."""
    toilets = [
        Toilet(*row)
        for row in ToiletConstruction.objects.filter(**{SLUM_CITY + "__in": list(names)}).values_list(*TOILET_FIELDS)
    ]
    whole = period.key == ALL_TIME
    upto = min(today, period.end)
    earlier = None if whole else (a_year_before(period.start), a_year_before(upto))
    last_year = None if whole else period_for(period.key - 1, today)
    month = today.replace(day=1)
    done = [toilet for toilet in toilets if toilet.status == COMPLETED and toilet.completed]
    done_now = [toilet for toilet in done if within(toilet.completed, period.start, upto)]
    agreed_now = [toilet for toilet in toilets if within(toilet.agreed, period.start, upto)]
    build_now = [gap for gap in (day_gap(t.agreed, t.completed) for t in done_now) if gap is not None]
    build_before = [] if whole else [
        gap for gap in (day_gap(t.agreed, t.completed) for t in done if within(t.completed, last_year.start, last_year.end))
        if gap is not None
    ]
    used = sum(1 for toilet in done_now if toilet.used)
    dates = [day for toilet in toilets for day in (toilet.agreed, toilet.completed) if day]
    first_day = min(dates) if dates else today
    building = {t.city for t in done_now} | {t.city for t in agreed_now}
    if period.current or whole:
        building |= {t.city for t in toilets if t.status == UNDER_CONSTRUCTION}
    return {
        "tiles": {
            "completed": counted(
                len(done_now), None if whole else sum(1 for t in done if within(t.completed, *earlier)),
                sum(1 for t in done_now if t.completed >= month) if period.current else None),
            "agreed": counted(
                len(agreed_now), None if whole else sum(1 for t in toilets if within(t.agreed, *earlier)),
                sum(1 for t in agreed_now if t.agreed >= month) if period.current else None),
            "build_days": build_days(middle(build_now), middle(build_before), last_year.label if last_year else ""),
            "in_use": {"percent": percent(used, len(done_now)), "used": used, "completed": len(done_now)},
            "under_construction": {
                "value": sum(1 for toilet in toilets if toilet.status == UNDER_CONSTRUCTION),
                "overdue": sum(
                    1 for toilet in toilets if toilet.status == UNDER_CONSTRUCTION and toilet.agreed
                    and (today - toilet.agreed).days > OPEN_BUILD_DAYS),
                "overdue_days": OPEN_BUILD_DAYS,
            },
        },
        "progress": progress(toilets, names, today),
        "momentum": momentum_chart(
            [t.completed for t in toilets if t.completed], [t.agreed for t in toilets if t.agreed],
            period, upto, first_day, "completions", "new agreements"),
        "stages": stages(done_now, names),
        "after": after(done_now, agreed_now, names),
        "heat": work_grid([(t.city, t.completed) for t in done_now], building, names, period, today, first_day, "Toilet work"),
        "gaps": gaps(names, today),
        "as_of": day_label(max(dates)) if dates else "",
    }


def build_days(value, last, last_label):
    slower = value - last if value is not None and last is not None else None
    return {"value": value, "last": last, "last_label": last_label, "slower": slower,
            "faster": -slower if slower is not None else None}


def progress(toilets, names, today):
    """Work going on now, per city: builds open today and what moved in the last PROGRESS_DAYS."""
    under, overdue, completed, agreed = Counter(), Counter(), Counter(), Counter()
    for toilet in toilets:
        if toilet.status == UNDER_CONSTRUCTION:
            under[toilet.city] += 1
            if toilet.agreed and (today - toilet.agreed).days > OPEN_BUILD_DAYS:
                overdue[toilet.city] += 1
        if toilet.status == COMPLETED and toilet.completed and 0 <= (today - toilet.completed).days < PROGRESS_DAYS:
            completed[toilet.city] += 1
        if toilet.agreed and 0 <= (today - toilet.agreed).days < PROGRESS_DAYS:
            agreed[toilet.city] += 1
    rows = sorted(
        ({"label": names[city], "under": under[city], "overdue": overdue[city], "completed": completed[city],
          "agreed": agreed[city]}
         for city in set(under) | set(completed) | set(agreed)),
        key=lambda row: (-row["under"], -row["completed"], row["label"]),
    )
    building = sum(1 for row in rows if row["under"])
    if building:
        finding = "%s toilets are under construction across %s" % (indian(sum(under.values())), cities_phrase(building))
    elif rows:
        finding = "No toilets are under construction right now"
    else:
        finding = "No building work recorded in the last %d days" % PROGRESS_DAYS
    late = sum(overdue.values())
    return {"rows": rows, "days": PROGRESS_DAYS, "overdue_days": OPEN_BUILD_DAYS, "finding": finding,
            "within": sum(under.values()) - late, "overdue": late}


def stages(done, names):
    """Typical days per build stage and per city, for the toilets completed in the period."""
    rows, longest = [], None
    for label, phrase, start, end in STAGES:
        days = [gap for gap in (day_gap(getattr(t, start), getattr(t, end)) for t in done) if gap is not None]
        typical = middle(days)
        rows.append({"label": label, "days": typical, "count": len(days)})
        if typical is not None and (longest is None or typical > longest[1]):
            longest = (phrase, typical)
    by_city = defaultdict(list)
    for toilet in done:
        days = day_gap(toilet.agreed, toilet.completed)
        if days is not None:
            by_city[toilet.city].append(days)
    cities = sorted(
        ({"label": names[city], "days": middle(days), "count": len(days)} for city, days in by_city.items()),
        key=lambda row: row["days"],
    )
    finding = "The longest wait is %s, typically %d days" % longest if longest else "No completed toilets with dates to measure"
    return {"rows": rows, "cities": cities, "finding": finding}


def after(done, agreed, names):
    """Follow-through on the toilets completed in the period, and how many of its agreements were cancelled."""
    total = len(done)
    counts = (
        ("Completed", total),
        ("Factsheet done", sum(1 for t in done if t.factsheet)),
        ("Connection recorded", sum(1 for t in done if t.connected)),
        ("Confirmed in use", sum(1 for t in done if t.used)),
    )
    per_city, cancelled = Counter(t.city for t in agreed), Counter(t.city for t in agreed if t.status == CANCELLED)
    rates = sorted(
        ({"label": names[city], "percent": round(100.0 * cancelled[city] / count, 1), "count": cancelled[city]}
         for city, count in per_city.items()),
        key=lambda row: -row["percent"],
    )
    unused = total - counts[3][1]
    finding = (
        "%d%% of completed toilets have no recorded use date" % percent(unused, total)
        if total else "No toilets were completed in this period"
    )
    agreements, dropped = len(agreed), sum(cancelled.values())
    if not agreements:
        cancel_finding = "No agreements were signed in this period"
    elif len(rates) > 1 and dropped:
        cancel_finding = "%d%% of agreements were cancelled; %s has the highest rate" % (
            percent(dropped, agreements), rates[0]["label"])
    else:
        cancel_finding = "%d%% of agreements were cancelled" % percent(dropped, agreements)
    return {
        "rows": [{"label": label, "count": count, "percent": percent(count, total)} for label, count in counts],
        "cancelled": rates, "finding": finding,
        "cancel_finding": cancel_finding, "agreements": agreements, "cancelled_total": dropped,
    }


def gap_queries(city_ids, today):
    """The querysets behind "Worth a look"; the card counts them and the lists page through them."""
    toilets = ToiletConstruction.objects.filter(**{SLUM_CITY + "__in": city_ids})
    overdue = today - datetime.timedelta(days=OPEN_BUILD_DAYS)
    return OrderedDict([
        ("open_builds", toilets.filter(status=UNDER_CONSTRUCTION, agreement_date__lt=overdue)),
        ("no_factsheet", toilets.filter(status=COMPLETED, factsheet_done=None)),
    ])


def gaps(names, today):
    city_ids = list(names)
    toilets = ToiletConstruction.objects.filter(**{SLUM_CITY + "__in": city_ids})
    open_builds = toilets.filter(status=UNDER_CONSTRUCTION).count()
    completed = toilets.filter(status=COMPLETED).count()
    counts = {key: query.count() for key, query in gap_queries(city_ids, today).items()}
    notes = {
        "open_builds": "out of %s currently under construction" % indian(open_builds),
        "no_factsheet": "%d%% of all completed toilets" % (percent(counts["no_factsheet"], completed) or 0),
    }
    waiting = [("%s builds open over %d days" % (indian(counts["open_builds"]), OPEN_BUILD_DAYS), counts["open_builds"]),
               ("%s completed toilets without a factsheet" % indian(counts["no_factsheet"]), counts["no_factsheet"])]
    finding = ", ".join(text for text, count in waiting if count) or "Nothing is waiting right now"
    return {
        "rows": [{"key": key, "count": counts[key], "text": text, "note": notes[key]} for key, text in GAPS.items()],
        "finding": finding[:1].upper() + finding[1:],
    }


def gap_rows(key, city_ids, today=None):
    """Title, column headings and row tuples for one "Worth a look" list. Household-level: gate the caller."""
    query = gap_queries(city_ids, today or timezone.localdate())[key]
    date_field, heading = (
        ("agreement_date", "Agreement date") if key == "open_builds" else ("completion_date", "Completion date")
    )
    rows = query.order_by(date_field, "id").values_list(
        SLUM_CITY + "__name__city_name", "slum__name", "household_number", date_field)
    return {"title": GAPS[key], "columns": ("City", "Slum", "Household number", heading), "rows": rows}


# ---------- part: menstrual hygiene ----------

def mhm_part(names, period, today):
    """Menstrual hygiene programme. Enrolment rows say whether a cup was given; follow-up visits say whether it
    was in use."""
    upto = min(today, period.end)
    whole = period.key == ALL_TIME
    enrolled, cups, members = [], [], set()
    programmes = MemberProgramData.objects.filter(**{SLUM_CITY + "__in": list(names)}).exclude(created_date=None)
    for city, day, member, answers in programmes.values_list(SLUM_CITY + "_id", "created_date", "member_id", "program_data"):
        enrolled.append((city, day))
        if within(day, period.start, upto):
            members.add(member)
        if isinstance(answers, dict) and (answers.get(CUP_GIVEN) == "Yes" or answers.get(CUP_PRODUCTS) == CUP_PRODUCT):
            cups.append((city, day))
    followups, used, unused = [], 0, 0
    visits = MemberEncounterData.objects.filter(**{SLUM_CITY + "__in": list(names)}).exclude(created_date=None)
    for city, day, answers in visits.values_list(SLUM_CITY + "_id", "created_date", "encounter_data"):
        followups.append((city, day))
        if within(day, period.start, upto) and isinstance(answers, dict):
            used += answers.get(CUP_USED) == "Yes"
            unused += answers.get(CUP_USED) == "No"
    month = today.replace(day=1)

    def in_period(rows):
        """The period's count of dated rows, with the same stretch a year earlier and this month's share."""
        now = [day for city, day in rows if within(day, period.start, upto)]
        before = None if whole else sum(
            1 for city, day in rows if within(day, a_year_before(period.start), a_year_before(upto)))
        return counted(len(now), before, sum(1 for day in now if day >= month) if period.current else None)

    enrolled_now = [(city, day) for city, day in enrolled if within(day, period.start, upto)]
    using = percent(used, used + unused)
    menstruating = MemberDataETL.objects.filter(
        gender="Female", menstruation_status="Yes", **{SLUM_CITY + "__in": list(names)})
    if using is not None:
        finding = "%d%% of follow-up visits in %s found the cup in use" % (using, period_words(period))
    elif enrolled_now:
        finding = "%s women joined the menstrual hygiene programme in %s" % (indian(len(enrolled_now)), period_words(period))
    else:
        finding = "No menstrual hygiene records in %s" % period_words(period)
    dates = [day for city, day in enrolled + followups]
    first_day = min(dates) if dates else today
    caring = {city for city, day in enrolled_now} | {city for city, day in followups if within(day, period.start, upto)}
    return {
        "enrolled": in_period(enrolled), "cups": in_period(cups), "followups": in_period(followups),
        "women": len(members),
        "using_percent": using, "waiting": menstruating.exclude(mhm_program_enrollment="Yes").count(),
        "momentum": momentum_chart(
            [day for city, day in cups], [day for city, day in followups], period, upto, first_day,
            "cups given", "follow-up visits"),
        "heat": work_grid(enrolled_now, caring, names, period, today, first_day, "Menstrual hygiene work"),
        "as_of": day_label(max(dates)) if dates else "", "finding": finding,
    }


# ---------- part: mobilisation ----------

def mobilisation_part(names, period, today):
    """Community mobilisation sessions held in the period, and which activities they were."""
    upto = min(today, period.end)
    everything = CommunityMobilization.objects.filter(**{SLUM_CITY + "__in": list(names)})
    sessions = everything.filter(activity_date__range=(period.start, upto))
    activities = sessions.order_by().values_list("activity_type__name").annotate(count=Count("id")).order_by("-count")
    before = None if period.key == ALL_TIME else everything.filter(
        activity_date__range=(a_year_before(period.start), a_year_before(upto))).count()
    return {
        "sessions": counted(
            sessions.count(), before,
            sessions.filter(activity_date__gte=today.replace(day=1)).count() if period.current else None),
        "activities": [{"label": name, "count": count} for name, count in activities[:TOP_ACTIVITIES]],
        "as_of": day_label(everything.aggregate(newest=Max("activity_date"))["newest"]),
    }


# ---------- part: household survey ----------

def survey_part(names, period, today):
    """How households reach a toilet, per city, from the household survey summary; and from that the scope of
    work: households still on a community toilet or open defecation, beside the toilets already built there."""
    steps = [step for label, step, field in LADDER]
    sums = DashboardData.objects.filter(city__in=list(names)).order_by().values("city").annotate(
        refreshed=Max("modified_on"), **{step: Sum(field) for label, step, field in LADDER})
    built = dict(
        ToiletConstruction.objects.filter(status=COMPLETED, **{SLUM_CITY + "__in": list(names)}).order_by()
        .values_list(SLUM_CITY + "_id").annotate(Count("id")))
    rows, scope, totals, refreshed = [], [], Counter(), None
    for row in sums:
        counts = [int(row[step] or 0) for step in steps]
        surveyed = sum(counts)
        if not surveyed:
            continue
        count = dict(zip(steps, counts))
        totals.update(count)
        refreshed = max(refreshed, row["refreshed"]) if refreshed else row["refreshed"]
        rows.append({
            "label": names[row["city"]], "open": count["open"],
            "shares": [round(100.0 * step / surveyed, 1) for step in counts],
        })
        need = count["community"] + count["open"]
        scope.append({
            "label": names[row["city"]], "community": count["community"], "open": count["open"], "need": need,
            "surveyed": surveyed, "percent": percent(need, surveyed), "built": built.get(row["city"], 0),
        })
    rows.sort(key=lambda row: -row["shares"][0])
    scope.sort(key=lambda row: (-row["need"], row["label"]))
    covered = {row["label"] for row in rows}
    households = sum(totals.values())
    community = percent(totals["community"], households)
    finding = (
        "%d%% of surveyed households depend on a community toilet" % community
        if rows else "No household survey summary for this selection"
    )
    need = totals["community"] + totals["open"]
    if not scope:
        scope_finding = "No household survey summary for this selection"
    elif not need:
        scope_finding = "No surveyed household is left on a community toilet or open defecation"
    elif len(scope) == 1:
        scope_finding = "%d%% of surveyed households here have no toilet of their own" % scope[0]["percent"]
    else:
        scope_finding = "%s has the most households without a toilet of their own: %s, %d%% of those surveyed there" % (
            scope[0]["label"], indian(scope[0]["need"]), scope[0]["percent"])
    coverage = {"cities_with_data": len(rows), "cities": len(names)}
    return {
        "labels": [label for label, step, field in LADDER], "rows": rows,
        "no_data": sorted(name for name in names.values() if name not in covered),
        "open": dict(coverage, households=int(totals["open"]), percent=percent(totals["open"], households)),
        "need": dict(coverage, households=int(need), community=int(totals["community"]), open=int(totals["open"]),
                     percent=percent(need, households)),
        "scope": {"rows": scope, "finding": scope_finding},
        "as_of": day_label(as_date(refreshed)), "finding": finding,
    }


# ---------- part: people ----------

VULNERABLE = (
    ("Widows", lambda age, disability, marital: marital == "Widow"),
    ("With a disability", lambda age, disability, marital: disability == "Yes"),
    ("Aged %d or over" % ELDER_AGE, lambda age, disability, marital: (age or 0) >= ELDER_AGE),
)


def house_key(slum, number):
    """One household, however its number was typed (" 012" and "12" are the same house)."""
    return slum, str(number or "").strip().lstrip("0")


def people_part(names, period, today):
    """Vulnerable members (widows, members with a disability, elders) in the member records whom we reached: a
    toilet completed at their home, counted in the year its agreement was signed, or a place in the menstrual
    hygiene programme, counted in the year they joined. Counts only. The household number is read from the
    member row the sync writes, not from the summary table's copy of it."""
    in_cities = {SLUM_CITY + "__in": list(names)}
    whole, upto = period.key == ALL_TIME, min(today, period.end)
    homes = {}
    completed = ToiletConstruction.objects.filter(status=COMPLETED, **in_cities)
    for slum, number, agreed, done in completed.values_list("slum_id", "household_number", "agreement_date", "completion_date"):
        home, day = house_key(slum, number), agreed or done
        if home not in homes or (day and (not homes[home] or day < homes[home])):
            homes[home] = day
    joined = {}
    enrolments = MemberProgramData.objects.filter(**in_cities).exclude(created_date=None)
    for member, day in enrolments.values_list("member_id", "created_date"):
        joined[member] = min(day, joined.get(member, day))
    rows = [{"label": label, "total": 0, "toilet": 0, "programme": 0, "ever": 0} for label, test in VULNERABLE]
    recorded = reached = ever = 0
    members = MemberDataETL.objects.filter(**in_cities)
    vulnerable = members.filter(Q(marital_status="Widow") | Q(disability="Yes") | Q(age__gte=ELDER_AGE))
    for member, slum, number, age, disability, marital in vulnerable.values_list(
            "member_id", "slum_id", "member__household_number", "age", "disability", "marital_status"):
        home = house_key(slum, number)
        if whole:
            by_toilet, by_programme = home in homes, member in joined
        else:
            by_toilet = within(homes.get(home), period.start, upto)
            by_programme = within(joined.get(member), period.start, upto)
        by_programme = by_programme and not by_toilet
        known = home in homes or member in joined
        recorded += 1
        reached += by_toilet or by_programme
        ever += known
        for row, (label, test) in zip(rows, VULNERABLE):
            if test(age, disability, marital):
                row["total"] += 1
                row["toilet"] += by_toilet
                row["programme"] += by_programme
                row["ever"] += known
    for row in rows:
        row["reached"] = row["toilet"] + row["programme"]
    return {
        "rows": rows, "recorded": recorded, "reached": reached, "ever": ever, "whole": whole,
        "as_of": day_label(as_date(members.aggregate(newest=Max("last_updated"))["newest"])),
    }


# ---------- part: funders ----------

def household_codes(codes):
    if isinstance(codes, str):
        try:
            codes = json.loads(codes)
        except ValueError:
            return []
    return codes if isinstance(codes, list) else []


def funders_part(names, period, today):
    """Toilets completed for the households linked to each funder, beside every toilet given, all years.
    Counts only: stored fund amounts mix units."""
    in_cities = {SLUM_CITY + "__in": list(names)}
    given = {
        house_key(slum, number) for slum, number in
        ToiletConstruction.objects.filter(status=COMPLETED, **in_cities).values_list("slum_id", "household_number")
    }
    toilets = defaultdict(set)
    details = SponsorProjectDetails.objects.filter(**in_cities).exclude(
        sponsor__organization_name__in=settings.HOME_EXCLUDED_FUNDERS)
    for name, slum, codes in details.values_list("sponsor__organization_name", "slum_id", "household_code"):
        toilets[name].update(house_key(slum, code) for code in household_codes(codes))
    counts = {name: len(homes & given) for name, homes in toilets.items()}
    ranked = sorted(((name, count) for name, count in counts.items() if count), key=lambda pair: (-pair[1], pair[0]))
    top, every = ranked[:TOP_FUNDERS], sum(count for name, count in ranked)
    rows = [{"label": name, "count": count} for name, count in top]
    if len(ranked) > TOP_FUNDERS:
        rows.append({"label": "All others", "count": every - sum(count for name, count in top)})
    share = percent(sum(count for name, count in top), every)
    if not ranked:
        finding = "No funded toilets recorded for this selection"
    elif len(top) == 1:
        finding = "One funder accounts for all funded toilets"
    else:
        finding = "The top %d funders account for %d%% of funded toilets" % (len(top), share)
    funded = set().union(*toilets.values()) & given
    return {"rows": rows, "total": len(funded), "given": len(given), "funders": len(ranked), "finding": finding}


# ---------- part: last synced ----------

def synced_part(names, period, today):
    """Newest record per city for each dataset. Dates only, never job status."""
    city_ids = list(names)
    newest = {
        "household": dict(HouseholdData.objects.filter(city__in=city_ids).order_by()
                          .values_list("city").annotate(Max("submission_date"))),
        "followup": dict(FollowupData.objects.filter(city__in=city_ids).order_by()
                         .values_list("city").annotate(Max("submission_date"))),
        "toilet": {},
    }
    built = ToiletConstruction.objects.filter(**{SLUM_CITY + "__in": city_ids}).order_by().values_list(SLUM_CITY).annotate(
        Max("agreement_date"), Max("completion_date"))
    for city, agreed, completed in built:
        days = [day for day in (agreed, completed) if day]
        if days:
            newest["toilet"][city] = max(days)
    worked_in = set(
        Slum.objects.filter(associated_with_SA=True, electoral_ward__administrative_ward__city__in=city_ids)
        .values_list("electoral_ward__administrative_ward__city", flat=True))
    rows = []
    for city in city_ids:
        cells = [recency(newest[kind].get(city), today) for kind in ("household", "followup", "toilet")]
        ages = [cell["days"] for cell in cells if cell["days"] is not None]
        if ages or city in worked_in:
            rows.append({"label": names[city], "cells": cells, "newest": min(ages) if ages else None})
    rows.sort(key=lambda row: (row["newest"] is None, row["newest"] or 0, row["label"]))
    current = sum(1 for row in rows if row["newest"] is not None and row["newest"] <= FRESH_DAYS)
    finding = (
        "%d of %d cities received new data in the last %d days" % (current, len(rows), FRESH_DAYS)
        if rows else "No field data recorded for this selection"
    )
    return {"rows": rows, "finding": finding}


BUILDERS = {
    "toilets": toilets_part, "mhm": mhm_part, "mobilisation": mobilisation_part,
    "survey": survey_part, "people": people_part, "funders": funders_part, "synced": synced_part,
}
