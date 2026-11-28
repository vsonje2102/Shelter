"""Save a city's households again from AVNI - registration and visits - unchanged records included.

The nightly sync only re-saves records AVNI changed, so after a writer is fixed the stored rows keep
the old result until this runs. `manage.py resync_households --city ID [--slum ID ...] [--apply]`.
"""

from collections import Counter

from django.db import transaction

from avni.provider import sync_context
from graphs.models import HouseholdData
from notification.services import reporting
from survey import connector

REPORTED_KEYS = (
    "group_oi8ts04/Current_place_of_defecation",
    "group_el9cl08/Type_of_water_connection",
    "group_el9cl08/Facility_of_solid_waste_collection",
    "Do you have a toilet at home?",
    "Do you have pipe water connection at home?",
    "How do you dispose your solid waste",
)


class DryRun(Exception):
    pass


def household_rows(city_id, slum_ids=None):
    rows = HouseholdData.objects.filter(city_id=city_id)
    return rows.filter(slum_id__in=slum_ids) if slum_ids else rows


def subject_ids(rows):
    seen = []
    for data in rows.values_list("rhs_data", flat=True).iterator():
        uuid = (data or {}).get("rhs_uuid")
        if uuid and uuid not in seen:
            seen.append(uuid)
    return seen


def filled(rows):
    """Rows holding each REPORTED_KEYS answer."""
    counts = Counter()
    for data in rows.values_list("rhs_data", flat=True).iterator():
        for key in REPORTED_KEYS:
            if (data or {}).get(key) not in (None, ""):
                counts[key] += 1
    return counts


def run(city_id, slum_ids=None, apply=False, api=None, out=print):
    rows = household_rows(city_id, slum_ids)
    ids = subject_ids(rows)
    result = {"households": len(ids), "before": filled(rows)}
    out("{} households, {}".format(len(ids), "writing" if apply else "dry run (rolled back)"))
    recorder = reporting.start("resync_households", trigger="manual")
    context = sync_context(api)
    context.save_unchanged = True
    try:
        with transaction.atomic():
            with recorder.step("households"):
                for done, uuid in enumerate(ids, 1):
                    connector.sync_subject(uuid, context)
                    if done % 100 == 0:
                        out("{} of {}".format(done, len(ids)))
            result["after"] = filled(household_rows(city_id, slum_ids))
            if not apply:
                raise DryRun
    except DryRun:
        pass
    recorder.finish()
    result["counts"] = context.summary()
    for key in REPORTED_KEYS:
        out("{}: {} -> {}".format(key, result["before"][key], result["after"][key]))
    out("records: {}".format(dict(result["counts"])))
    return result
