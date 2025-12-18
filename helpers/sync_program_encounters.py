"""

This module contains all the functions required for synchronizing Encounter data
from the Avni platform into our system. It focuses specifically on the Encounter
types that were recently separated out from the older sync workflow to provide
better modularity, maintainability, and debugging clarity.

The Encounter types handled in this file include:
    • Water, Waste, Sanitation and Electricity Encounters 
    • Daily Reporting
    • Family Factsheet

These Encounters were previously managed inside `sync_avni_data.py` under the
`graphs/` directory. To streamline the codebase, Encounter-specific logic is now
moved to this dedicated file. Any remaining non-Encounter sync logic continues
to reside in the older `sync_avni_data.py` file.

Configuration:
Some configuration files and Avni-related helper utilities required for API
interaction (authentication, pagination, request building, etc.) are still
located in the original sync structure under the `graphs/` directory. This file
imports and uses those shared configurations.

Purpose of This File:
    • Fetch Encounter records from Avni
    • Normalize and transform the raw Avni data as required by our database model
    • Handle incremental syncing using last updated timestamps
    • Prepare structured encounter objects for downstream storage
    • Separate Encounter syncing from Member/Household syncing for cleaner code

NOTE:
Ensure that any new Encounter types introduced in the future follow the same
structure and are added here to keep all Encounter logic centralized.

+------------------+-----------------------------+------------------+------------------------------+
| sync_category.id | sync_category.name          | sync_module.id   | sync_module.name             |
+------------------+-----------------------------+------------------+------------------------------+
|        1         | Direct Encounters           |        1         | Sanitation                   |
|                  |                             |        2         | Water                        |
|                  |                             |        3         | Waste                        |
|                  |                             |        4         | Electricity                  |
|                  |                             |        5         | Property Tax                 | <- Implemented but no use currently
|                  |                             |        6         | Daily Mobilization Activity  | <- Implemented but no use currently
+------------------+-----------------------------+------------------+------------------------------+
|        2         | Program Encounters          |        7         | Family Factsheet             |
|                  |                             |        8         | Daily Reporting              |
+------------------+-----------------------------+------------------+------------------------------+
|        3         | Member Data                 |        9         | Member Core Data             |
|                  |                             |       10         | Member Program Data          |
|                  |                             |       11         | Member Encounter Data        |
+------------------+-----------------------------+------------------+------------------------------+
|        4         | RHS Data                    |       12         | Structure                    |
|                  |                             |       13         | Household                    |
+------------------+-----------------------------+------------------+------------------------------+


"""
import json
import dateparser
import requests
from datetime import timedelta
from django.utils import timezone

from graphs.models import *
from mastersheet.models import *
from master.models import *
from helpers.models import *
from helpers.sync_direct_encounter import *
from graphs.sync_avni_data import avni_sync
from datetime import datetime
# -------------------- ENCOUNTER DEFINITIONS --------------------

direct_encounters = ['Sanitation', 'Property tax', 'Water', 'Waste', 'Electricity', 'Daily Mobilization Activity']
program_encounters = ['Daily Reporting', 'Family factsheet']

mapping_encounter_type = {
	'Sanitation': 1,
	'Water': 2,
	'Waste': 3,
	'Electricity': 4,
	'Property tax': 5,
	'Daily Mobilization Activity': 6,
	'Family factsheet': 7,
	'Daily Reporting': 8,
	'member core data': 9,
	'member program data': 10,
	'member encounter data': 11,
	'Structure': 12,
	'Household': 13,
}

operation_type_mapping = {'1': 'CREATE', '2': 'UPDATE', '3': 'DELETE'}
status_type_mapping = {'1': 'SUCCESS', '2': 'FAILED', '3': 'SKIPPED'}

# -------------------- MAIN SYNC CLASS --------------------
class AvniProgramEncounterSync(avni_sync):
	"""
	Encounter sync handler for Avni
	dry_run = True → preview only, no DB writes
	debug = True → print debug logs
	"""

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.debug = False
		self.dry_run = False  

	# ---------- DEBUG HELPER ----------

	def print_debug(self, message):
		if self.debug:
			if self.dry_run:
				message = "[DRY RUN] " + message
			print(message)

	# ---------- SYNC JOB HELPERS ----------

	def get_latest_modified_date(self, module_id):
		self.print_debug(f"[SYNC] Fetching last modified date for module {module_id}")
		try:
			obj = SyncJob.objects.filter(module__id=module_id,status= "SUCCESS").order_by('-ended_at').first()
			self.print_debug(f"[SYNC] Last modified date fetched: {obj.ended_at if obj else 'No previous sync found'}")
			if obj and obj.ended_at:
				return obj.ended_at.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
		except Exception as e:
			self.print_debug(f"[ERROR] Failed to fetch last modified date: {e}")
		return (timezone.now() - timedelta(days=30)).strftime('%Y-%m-%dT%H:%M:%S.%fZ')

	def start_sync_job(self, module_id, triggered_by="Admin_User"):
		self.print_debug(f"[SYNC] Starting sync job for module {module_id}")
		status = "DRY_RUN" if self.dry_run else "RUNNING"
		return SyncJob.objects.create(module_id=module_id, status=status, triggered_by=triggered_by, started_at=timezone.now())

	def finish_sync_job(self, sync_job, status, total, success, failed, message):
		self.print_debug(f"[SYNC] Finishing job {sync_job.id} | Status={status}")
		sync_job.ended_at = timezone.now()
		sync_job.status = status
		sync_job.total_records = total
		sync_job.success_count = success
		sync_job.failed_skipped_count = failed
		sync_job.message = message
		sync_job.save()

	def log_sync_record(self, sync_job_id, household_details, operation_type, status_code, message=None, error_message=None):
		self.print_debug(f"[RECORD] Job={sync_job_id} HH={household_details.get('household_number')} Status={status_code}")
		SyncJobRecord.objects.create(
			job_id=sync_job_id,
			avni_uuid=household_details.get("avni_uuid"),
			operation=operation_type_mapping.get(str(operation_type)),
			status=status_type_mapping.get(str(status_code)),
			household=household_details.get("household_record"),
			message=message,
			error_message=error_message
		)

	# ---------- AVNI API HELPERS FOR ENCOUNTER  ----------

	def fetch_encounter_pages(self, encounter_name, latest_date):
		self.print_debug(f"[API] Fetching pages for {encounter_name} since {latest_date}")
		programEncounters_path = 'api/programEncounters?lastModifiedDateTime=' + latest_date + '&encounterType=' + encounter_name
		directEncounter_path = 'api/encounters?lastModifiedDateTime=' + latest_date + '&encounterType=' + encounter_name
		if encounter_name in program_encounters:
			path = programEncounters_path
		else:
			path = directEncounter_path
		resp = requests.get(self.base_url + path, headers={'AUTH-TOKEN': self.get_cognito_token()})
		pages = json.loads(resp.text)['totalPages']
		return pages, path

	# ---------- HOUSEHOLD RESOLUTION ----------

	def get_household_details(self, subject_id):
		self.print_debug(f"[HH] Resolving household for subject {subject_id}")
		resp = requests.get(self.base_url + 'api/subject/' + subject_id, headers={'AUTH-TOKEN': self.get_cognito_token()})
		self.get_HH_data = json.loads(resp.text)

		details = {
			"city": self.get_HH_data['location']['City'],
			"slum": self.get_HH_data['location']['Slum'],
			"household_number": str(int(self.get_HH_data['observations']['First name'])),
			"submission_date": self.get_HH_data['audit']['Last modified at'],
			"avni_uuid": subject_id,
			"api_data": self.get_HH_data['observations'],
			"household_record": None
   
		}

		# First filter by slum + household number
		base_qs = HouseholdData.objects.filter(
			slum_id__name=details["slum"],
			household_number=details["household_number"]
		)

		# Extra safety: also match RHS UUID if available
		if base_qs:
			details["household_record"] = next(
				(o for o in base_qs if o.rhs_data and o.rhs_data.get("rhs_uuid") == str(subject_id)),
				None
			)

		self.print_debug(f"[HH] Household resolved: {bool(details['household_record'])}")
		return details

	# ---------- FACTSHEET DATA MAPPING FUNCTION ----------
	def map_ff_keys(self, factsheet_data):
		change_keys = {
            "Note": 'Note',
            "group_im2th52/Number_of_Children_under_5_years_of_age": "Number of Children under 5 years of age",
            "group_ne3ao98/Cost_of_upgradation_in_Rs": 'Cost of upgradation',
            "group_oh4zf84/Duration_of_stay_in_settlement_in_Years": "Duration of stay in this current settlement (in Years)",
            "group_oh4zf84/Ownership_status": "Ownership status of the house_1",
            "group_im2th52/Number_of_earning_members": "Number of earning members",
            "group_im2th52/Occupation_s_of_earning_membe": "Occupation(s) of earning members",
            "Family_Photo": "Family Photo",
            "group_ne3ao98/Who_has_built_your_toilet": "Who has built your toilet ?",
            "group_im2th52/Approximate_monthly_family_income_in_Rs": "Approximate monthly family income (in Rs.)",
            "group_vq77l17/Household_number": "Househhold number",
            "group_im2th52/Total_family_members": "Total family members",
            "group_oh4zf84/Type_of_house": "Type of house*",
            "group_im2th52Number_of_members_over_60_years_of_age": "Number of members over 60 years of age",
            "group_ne3ao98/Where_the_individual_ilet_is_connected_to": "Where the individual toilet is connected ?",
            "group_vq77l17/Settlement_address": "Settlement address",
            "group_ne3ao98/Have_you_upgraded_yo_ng_individual_toilet": "Have you upgraded your toilet/bathroom/house while constructing individual toilet?",
            "group_im2th52/Number_of_disabled_members": "Number of Disabled members",
            "group_oh4zf84/Duration_of_stay_in_the_city_in_Years": "Duration of stay in the city (in Years)",
            "group_im2th52/Number_of_Male_members": "Number of Male members",
            "group_oh4zf84/Name_of_the_family_head": "Name of the family head",
            "Toilet_Photo": "Toilet Photo",
            "group_ne3ao98/Use_of_toilet": "Use of toilet",
            "group_im2th52/Number_of_Girl_children_between_0_18_yrs": "Number of Girl children between 0-18 yrs",
            "group_im2th52/Number_of_Female_members": "Number of Female members",
            "group_oh4zf84/Name_of_Native_villa_district_and_state" : 'What is your native place (village, town, city) ?'
        }
        
		multiselect_keys = ["Occupation(s) of earning members", "Use of toilet"]
		change_keys = {v : k for k, v in change_keys.items()}
		updated_factsheet_data = {}
		for k, v in factsheet_data.items():
			try:
				if k in change_keys.keys():
					updated_factsheet_data[change_keys[k]] = v 
				else:
					updated_factsheet_data[k] = v
				if k in multiselect_keys:
					if type(v) == list:
						updated_factsheet_data[change_keys[k]] = ",".join(v)
					elif type(v) == str:
						updated_factsheet_data[change_keys[k]] = v
					else:
						pass
			except Exception as e:
				print(e)
		return updated_factsheet_data

		# --- Config ---
	
	def FamilyFactsheetData(self, data, household_details, sync_job_id, encounter_name):
		self.print_debug(f"[SYNC] Starting Family Factsheet data processing")
		# ---- define early so except is always safe ----
		city_name = household_details.get("city")
		slum_name = household_details.get("slum")
		household_number = household_details.get("household_number")
		self.print_debug(f"[SYNC] HH Details - City: {city_name}, Slum: {slum_name}, HH Number: {household_number}")

		try:
			self.print_debug(f"[SYNC] Processing Family Factsheet for City: {city_name}, Slum: {slum_name}")

			slum = Slum.objects.filter(
				name=slum_name,
				electoral_ward__administrative_ward__city__name__city_name=city_name
			).values_list('id', 'electoral_ward__administrative_ward__city__id').first()

			if not slum:
				self.print_debug(f"[ERROR] Slum {slum_name} in City {city_name} not found.")
				return

			slum_id, city_id = slum
			self.print_debug(f"[SYNC] {city_name}:{city_id}, {slum_name}:{slum_id}")
			self.print_debug(f"[SYNC] Processing Family Factsheet for HH {household_number} in {slum_name}")

			household_record = HouseholdData.objects.filter(
				household_number=household_number,
				slum_id=slum_id,
				city_id=city_id
			).first()

			factsheetData = ToiletConstruction.objects.filter(
				household_number=household_number,
				slum_id=slum_id
			)

			if factsheetData.exists():
				self.print_debug(f"[SYNC] Found existing Factsheet record for HH {household_number} in {slum_name}")

			# ---- safe datetime parsing ----
			factsheet_done_date = datetime.strptime(
				data['audit']['Last modified at'][:19],
				'%Y-%m-%dT%H:%M:%S'
			).date()

			final_ff_data = self.map_ff_keys(data['observations'])
			final_ff_data.update({'ff_uuid': data['ID']})

			toilet_connected_to = None
			use_of_toilet = None

			if data['observations'].get('Where the individual toilet is connected ?') and data['observations']['Where the individual toilet is connected ?'] != 'Not connected':
				toilet_connected_to = factsheet_done_date

			if data['observations'].get('Use of toilet'):
				use_of_toilet = factsheet_done_date

			# ---- UPDATE ----
			if household_record:
				self.print_debug(f"[SYNC] Found existing Household record for HH {household_number} in {slum_name}")
				household_record.ff_data = final_ff_data

				if not self.dry_run:
					household_record.save()
					factsheetData.update(
						factsheet_done=factsheet_done_date,
						toilet_connected_to=toilet_connected_to,
						use_of_toilet=use_of_toilet
					)

				self.log_sync_record(
					sync_job_id,
					household_record,
					operation_type=2,
					status_code=1,
					message=f"Family Factsheet updated for HH {household_number} in {slum_name}."
				)

			# ---- CREATE ----
			else:
				self.registrtation_data(self.get_HH_data)
				household_record = HouseholdData.objects.filter(
					household_number=household_number,
					slum_id=slum_id,
					city_id=city_id
				).first()

				household_record.ff_data = final_ff_data

				if not self.dry_run:
					household_record.save()
					factsheetData.update(
						factsheet_done=factsheet_done_date,
						toilet_connected_to=toilet_connected_to,
						use_of_toilet=use_of_toilet
					)

				self.log_sync_record(
					sync_job_id,
					household_record,
					operation_type=1,
					status_code=1,
					message=f"Family Factsheet created for HH {household_number} in {slum_name}."
				)

		except Exception as e:
			self.log_sync_record(
				sync_job_id,
				household_details,
				operation_type=2,
				status_code=2,
				error_message=f"Failed to update Family Factsheet for HH {household_number} in {slum_name}: {e}"
			)
			self.print_debug(f"[ERROR] Family Factsheet failed: {e}")


	def DailyReportingData(self, data, household_details, sync_job_id):  # checked
		try:
			self.print_debug(f"[DAILY_REPORTING] Starting Daily Reporting data processing")
			city_name = household_details.get("city")
			slum_name = household_details.get("slum")
			self.print_debug(f"[DAILY_REPORTING] City: {city_name}, Slum: {slum_name}")
			
			slum = Slum.objects.filter(name=slum_name,electoral_ward_id__administrative_ward__city__name=city_name).values_list('id', 'electoral_ward_id__administrative_ward__city__id')[0]
			city_id = slum[1]
			slum_id = slum[0]
			household_number = household_details.get("household_number")
			self.print_debug(f"[DAILY_REPORTING] HH: {household_number}, slum_id: {slum_id}, city_id: {city_id}")

			phase_one_materials = ['Date on which bricks are given', 'Date on which sand is given',
								'Date on which crush sand is given', 'Date on which river sand is given',
								'Date on which cement is given', 'Date on which Pre mix plaster are given ?', 'Date on which Sanala are given ?']
			phase_two_materials = ['Date on which other hardware items are given', 'Date on which pan is given',
								'Date on which Tiles are given']

			phase_one_material_dates = [data[i] for i in phase_one_materials if i in data]
			phase_two_material_dates = [data[i] for i in phase_two_materials if i in data]
			self.print_debug(f"[DAILY_REPORTING] Phase 1 materials found: {len(phase_one_material_dates)}, Phase 2 materials found: {len(phase_two_material_dates)}")

			try:
				if 'Date on which agreement is cancelled' in data:
					agreement_cancelled = True
					self.print_debug(f"[DAILY_REPORTING] Agreement cancelled found in data")
				else:
					agreement_cancelled = False
					
				if 'Is the material is shifted ?' in data and data['Is the material is shifted ?'] == 'Yes, Within the Slum':
					materialIsShifted = True
					self.print_debug(f"[DAILY_REPORTING] Material shifted within slum")
				elif 'Is the material is shifted ?' in data and data['Is the material is shifted ?'] == 'Yes, Outside the Slum':
					self.print_debug(f"[DAILY_REPORTING] Material shifted outside slum - handled by office")
					return "Done"
				else:
					materialIsShifted = False

				if 'Date of agreement' in data:
					agreement_date = dateparser.parse(data['Date of agreement']).date()
					self.print_debug(f"[DAILY_REPORTING] Agreement date: {agreement_date}")
				else:
					agreement_date = None
					
				if 'Date on which septic tank is given' in data and not (agreement_cancelled or (materialIsShifted and 'House numbers of houses where Septic Tank is given' in data)):
					septic_tank_date = dateparser.parse(data['Date on which septic tank is given']).date()
					self.print_debug(f"[DAILY_REPORTING] Septic tank date: {septic_tank_date}")
				else:
					septic_tank_date = None

				if len(phase_one_material_dates) > 0 and not(agreement_cancelled or (materialIsShifted and 'House numbers of houses where PHASE 1 material bricks, sand and cement is given' in data)):
					phase_one_material_dates.sort(reverse=True)
					phase_one_material_date = dateparser.parse(phase_one_material_dates[0]).replace(tzinfo=None)
					self.print_debug(f"[DAILY_REPORTING] Phase 1 material date: {phase_one_material_date}")
				else:
					phase_one_material_date = None

				if len(phase_two_material_dates) > 0 and not (agreement_cancelled or (materialIsShifted and 'House numbers of houses where PHASE 2 material Hardware is given' in data)):
					phase_two_material_dates.sort(reverse=True)
					phase_two_material_date = dateparser.parse(phase_two_material_dates[0]).replace(tzinfo=None)
					self.print_debug(f"[DAILY_REPORTING] Phase 2 material date: {phase_two_material_date}")
				else:
					phase_two_material_date = None

				if 'Date on which door is given' in data and not (agreement_cancelled or (materialIsShifted and 'House numbers where material is shifted - 3rd Phase' in data)):
					phase_three_material_date = dateparser.parse(data['Date on which door is given']).date()
					self.print_debug(f"[DAILY_REPORTING] Phase 3 material date: {phase_three_material_date}")
				else:
					phase_three_material_date = None

				if 'Date on which toilet construction is complete' in data:
					completion_date = dateparser.parse(data['Date on which toilet construction is complete']).date()
					self.print_debug(f"[DAILY_REPORTING] Completion date: {completion_date}")
				else:
					completion_date = None

				if agreement_cancelled or materialIsShifted:
					if 'House numbers of houses where PHASE 1 material bricks, sand and cement is given' in data:
						p1_material_shifted_to = int(data['House numbers of houses where PHASE 1 material bricks, sand and cement is given'])
						self.print_debug(f"[DAILY_REPORTING] Phase 1 material shifted to HH: {p1_material_shifted_to}")
					else:
						p1_material_shifted_to = None

					if 'House numbers of houses where PHASE 2 material Hardware is given' in data:
						p2_material_shifted_to = int(data['House numbers of houses where PHASE 2 material Hardware is given'])
						self.print_debug(f"[DAILY_REPORTING] Phase 2 material shifted to HH: {p2_material_shifted_to}")
					else:
						p2_material_shifted_to = None

					if 'House numbers where material is shifted - 3rd Phase' in data:
						p3_material_shifted_to = int(data['House numbers where material is shifted - 3rd Phase'])
						self.print_debug(f"[DAILY_REPORTING] Phase 3 material shifted to HH: {p3_material_shifted_to}")
					else:
						p3_material_shifted_to = None

					if 'House numbers of houses where Septic Tank is given' in data:
						st_material_shifted_to = int(data['House numbers of houses where Septic Tank is given'])
						self.print_debug(f"[DAILY_REPORTING] Septic tank shifted to HH: {st_material_shifted_to}")
					else:
						st_material_shifted_to = None
				else:
					p1_material_shifted_to = p2_material_shifted_to = p3_material_shifted_to = st_material_shifted_to = None

				if 'Comment if any ?' in data:
					comment_ = data['Comment if any ?']
					self.print_debug(f"[DAILY_REPORTING] Comment: {comment_}")
				else:
					comment_ = None

				if completion_date is not None:
					status = 6
					self.print_debug(f"[DAILY_REPORTING] Status set to 6 (Completed)")
				elif (phase_one_material_date != None or phase_two_material_date != None or phase_three_material_date != None) and not(agreement_cancelled):
					status = 5
					self.print_debug(f"[DAILY_REPORTING] Status set to 5 (In Progress)")
				elif (agreement_date is not None and (phase_one_material_date == None and phase_two_material_dates == None and phase_three_material_date == None) and not(agreement_cancelled)):
					status = 3
					self.print_debug(f"[DAILY_REPORTING] Status set to 3 (Agreement Signed)")
				elif agreement_cancelled == True:
					status = 2
					self.print_debug(f"[DAILY_REPORTING] Status set to 2 (Cancelled)")
				else:
					status = None
					self.print_debug(f"[DAILY_REPORTING] Status set to None")

				check_record = ToiletConstruction.objects.filter(household_number=HH, slum_id=slum_id)
				self.print_debug(f"[DAILY_REPORTING] Checking existing records for HH {HH}")
				
				if len(data) > 1:
					if not check_record:
						self.print_debug(f"[DAILY_REPORTING] Creating new ToiletConstruction record for HH {HH}")
						create = ToiletConstruction.objects.create(
							household_number=HH,
							slum_id=slum_id,
							agreement_date=agreement_date,
							agreement_cancelled=agreement_cancelled,
							septic_tank_date=septic_tank_date,
							phase_one_material_date=phase_one_material_date,
							phase_two_material_date=phase_two_material_date,
							phase_three_material_date=phase_three_material_date,
							completion_date=completion_date,
							status=status,
							p1_material_shifted_to=p1_material_shifted_to,
							p2_material_shifted_to=p2_material_shifted_to,
							p3_material_shifted_to=p3_material_shifted_to,
							st_material_shifted_to=st_material_shifted_to,
							comment=comment_
						)
						self.log_sync_record(
							sync_job_id,
							household_details,
							operation_type=1,
							status_code=1,
							message=f"[DAILY_REPORTING] Construction status created for HH {HH} in slum {slum_id}"
						)
						self.print_debug(f'[DAILY_REPORTING] Construction status created for HH {HH} in slum {slum_id}')
					else:
						self.print_debug(f"[DAILY_REPORTING] Updating existing ToiletConstruction record for HH {HH}")
						check_record.update(
							agreement_date=agreement_date,
							agreement_cancelled=agreement_cancelled,
							septic_tank_date=septic_tank_date,
							phase_one_material_date=phase_one_material_date,
							phase_two_material_date=phase_two_material_date,
							phase_three_material_date=phase_three_material_date,
							completion_date=completion_date,
							status=status,
							p1_material_shifted_to=p1_material_shifted_to,
							p2_material_shifted_to=p2_material_shifted_to,
							p3_material_shifted_to=p3_material_shifted_to,
							st_material_shifted_to=st_material_shifted_to,
							comment=comment_
						)
						self.log_sync_record(
							sync_job_id,
							household_details,
							operation_type=2,
							status_code=1,
							message=f"[DAILY_REPORTING] Construction status updated for HH {HH} in slum {slum_id}"
						)
						self.print_debug(f'[DAILY_REPORTING] Construction status updated for HH {HH} in slum {slum_id}')
			except Exception as e:
				self.log_sync_record(
					sync_job_id,
					household_details,
					operation_type=2,
					status_code=2,
					error_message=f"[DAILY_REPORTING] Failed to process Daily Reporting data for HH {HH} in {slum_name}: {e}"
				)
				self.print_debug(f"[ERROR] Failed to process Daily Reporting data for HH {HH} in {slum_name}: {e}")
				raise
		except Exception as e:
			self.print_debug(f"[ERROR] Failed to get household details for Daily Reporting: {e}")
			self.log_sync_record(
				sync_job_id,
				household_details,
				operation_type=2,
				status_code=2,
				error_message=f"[DAILY_REPORTING] Failed to get household details for Daily Reporting: {e}"
			)
			raise

	# ---------- ENCOUNTER SAVE FUNCTIONS ----------
 
	def SaveProgramEncounterData(self, encounter_name,date=None,triggered_by="Admin_User"): 
		self.print_debug(f"[SYNC] Starting program encounter sync for {encounter_name}")
		module_id = mapping_encounter_type.get(encounter_name)
		sync_job = self.start_sync_job(module_id=mapping_encounter_type[encounter_name], triggered_by=triggered_by)
		if not date:
			date = self.get_latest_modified_date(mapping_encounter_type[encounter_name])
		pages, path = self.fetch_encounter_pages(encounter_name, date)
		self.print_debug(f"Saving {pages} pages of {encounter_name} encounters")
		total = success = failed = skipped = 0

		try:
			for i in range(pages):
				self.print_debug(f"[API] Fetching page {i+1}/{pages} for {encounter_name}")
				resp = requests.get(self.base_url + path + '&page=' + str(i), headers={'AUTH-TOKEN': self.get_cognito_token()})
				data = json.loads(resp.text)['content']
				for record in data:
					total += 1
					if record['Voided'] or not record['observations']:
						skipped+=1
						SyncJobRecord.objects.create(job_id=sync_job.id,avni_uuid=record['Subject ID'],operation='SKIPPED',status='SKIPPED',message=f"{encounter_name} encounter is voided or has no observations.")
						continue
					if encounter_name == 'Family factsheet':
						self.FamilyFactsheetData(
           					record,
							self.get_household_details(record['Subject ID']),
                            sync_job.id,
                            encounter_name
                        )
					elif encounter_name == 'Daily Reporting':
						self.DailyReportingData(
							record['observations'],
							self.get_household_details(record['Subject ID']),
							sync_job.id
						)
						success += 1
					else:
						self.print_debug(f"[WARNING] No handler implemented for encounter type: {encounter_name}")
						skipped += 1
			self.print_debug(f"[{encounter_name}] {encounter_name} encounter sync completed")
			self.print_debug(f"[{encounter_name}] Total: {total}, Success: {success}, Failed: {failed}, Skipped: {skipped}")
			self.finish_sync_job(sync_job, "SUCCESS", total, success, failed, f"{encounter_name} Encounter Sync completed successfully")
 
  		# -------- MANUAL ABORT (Ctrl+C) --------
		except KeyboardInterrupt:
			self.print_debug("[ABORTED] Job aborted manually (Ctrl+C)")
			self.finish_sync_job(sync_job, "ABORTED", total, success, failed, "Job aborted manually by user (Ctrl+C)")
			raise
		
		# -------- ABNORMAL TERMINATION --------
		except BaseException as e:
			self.print_debug(f"[ABORTED] Job aborted abnormally: {e}")
			self.finish_sync_job(sync_job,"ABORTED",total,success,failed,f"Job aborted abnormally: {str(e)}")
			raise
		
		# -------- APPLICATION ERROR --------
		except Exception as e:
			self.print_debug(f"[ERROR] Water sync failed: {e}")
			self.finish_sync_job(sync_job, "FAILED", total, success, failed, str(e))



	def DailyReportingData(self, data, household_details, sync_job_id):  # checked
		try:
			self.print_debug(f"[DAILY_REPORTING] Starting Daily Reporting data processing")
			city_name = household_details[0]
			slum_name = household_details[1]
			self.print_debug(f"[DAILY_REPORTING] City: {city_name}, Slum: {slum_name}")
			
			slum = Slum.objects.filter(name=slum_name,electoral_ward_id__administrative_ward__city__name=city_name).values_list('id', 'electoral_ward_id__administrative_ward__city__id')[0]
			city_id = slum[1]
			slum_id = slum[0]
			HH = household_details[2]
			self.print_debug(f"[DAILY_REPORTING] HH: {HH}, slum_id: {slum_id}, city_id: {city_id}")

			phase_one_materials = ['Date on which bricks are given', 'Date on which sand is given',
								'Date on which crush sand is given', 'Date on which river sand is given',
								'Date on which cement is given', 'Date on which Pre mix plaster are given ?', 'Date on which Sanala are given ?']
			phase_two_materials = ['Date on which other hardware items are given', 'Date on which pan is given',
								'Date on which Tiles are given']

			phase_one_material_dates = [data[i] for i in phase_one_materials if i in data]
			phase_two_material_dates = [data[i] for i in phase_two_materials if i in data]
			self.print_debug(f"[DAILY_REPORTING] Phase 1 materials found: {len(phase_one_material_dates)}, Phase 2 materials found: {len(phase_two_material_dates)}")

			try:
				if 'Date on which agreement is cancelled' in data:
					agreement_cancelled = True
					self.print_debug(f"[DAILY_REPORTING] Agreement cancelled found in data")
				else:
					agreement_cancelled = False
					
				if 'Is the material is shifted ?' in data and data['Is the material is shifted ?'] == 'Yes, Within the Slum':
					materialIsShifted = True
					self.print_debug(f"[DAILY_REPORTING] Material shifted within slum")
				elif 'Is the material is shifted ?' in data and data['Is the material is shifted ?'] == 'Yes, Outside the Slum':
					self.print_debug(f"[DAILY_REPORTING] Material shifted outside slum - handled by office")
					return "Done"
				else:
					materialIsShifted = False

				if 'Date of agreement' in data:
					agreement_date = dateparser.parse(data['Date of agreement']).date()
					self.print_debug(f"[DAILY_REPORTING] Agreement date: {agreement_date}")
				else:
					agreement_date = None
					
				if 'Date on which septic tank is given' in data and not (agreement_cancelled or (materialIsShifted and 'House numbers of houses where Septic Tank is given' in data)):
					septic_tank_date = dateparser.parse(data['Date on which septic tank is given']).date()
					self.print_debug(f"[DAILY_REPORTING] Septic tank date: {septic_tank_date}")
				else:
					septic_tank_date = None

				if len(phase_one_material_dates) > 0 and not(agreement_cancelled or (materialIsShifted and 'House numbers of houses where PHASE 1 material bricks, sand and cement is given' in data)):
					phase_one_material_dates.sort(reverse=True)
					phase_one_material_date = dateparser.parse(phase_one_material_dates[0]).replace(tzinfo=None)
					self.print_debug(f"[DAILY_REPORTING] Phase 1 material date: {phase_one_material_date}")
				else:
					phase_one_material_date = None

				if len(phase_two_material_dates) > 0 and not (agreement_cancelled or (materialIsShifted and 'House numbers of houses where PHASE 2 material Hardware is given' in data)):
					phase_two_material_dates.sort(reverse=True)
					phase_two_material_date = dateparser.parse(phase_two_material_dates[0]).replace(tzinfo=None)
					self.print_debug(f"[DAILY_REPORTING] Phase 2 material date: {phase_two_material_date}")
				else:
					phase_two_material_date = None

				if 'Date on which door is given' in data and not (agreement_cancelled or (materialIsShifted and 'House numbers where material is shifted - 3rd Phase' in data)):
					phase_three_material_date = dateparser.parse(data['Date on which door is given']).date()
					self.print_debug(f"[DAILY_REPORTING] Phase 3 material date: {phase_three_material_date}")
				else:
					phase_three_material_date = None

				if 'Date on which toilet construction is complete' in data:
					completion_date = dateparser.parse(data['Date on which toilet construction is complete']).date()
					self.print_debug(f"[DAILY_REPORTING] Completion date: {completion_date}")
				else:
					completion_date = None

				if agreement_cancelled or materialIsShifted:
					if 'House numbers of houses where PHASE 1 material bricks, sand and cement is given' in data:
						p1_material_shifted_to = int(data['House numbers of houses where PHASE 1 material bricks, sand and cement is given'])
						self.print_debug(f"[DAILY_REPORTING] Phase 1 material shifted to HH: {p1_material_shifted_to}")
					else:
						p1_material_shifted_to = None

					if 'House numbers of houses where PHASE 2 material Hardware is given' in data:
						p2_material_shifted_to = int(data['House numbers of houses where PHASE 2 material Hardware is given'])
						self.print_debug(f"[DAILY_REPORTING] Phase 2 material shifted to HH: {p2_material_shifted_to}")
					else:
						p2_material_shifted_to = None

					if 'House numbers where material is shifted - 3rd Phase' in data:
						p3_material_shifted_to = int(data['House numbers where material is shifted - 3rd Phase'])
						self.print_debug(f"[DAILY_REPORTING] Phase 3 material shifted to HH: {p3_material_shifted_to}")
					else:
						p3_material_shifted_to = None

					if 'House numbers of houses where Septic Tank is given' in data:
						st_material_shifted_to = int(data['House numbers of houses where Septic Tank is given'])
						self.print_debug(f"[DAILY_REPORTING] Septic tank shifted to HH: {st_material_shifted_to}")
					else:
						st_material_shifted_to = None
				else:
					p1_material_shifted_to = p2_material_shifted_to = p3_material_shifted_to = st_material_shifted_to = None

				if 'Comment if any ?' in data:
					comment_ = data['Comment if any ?']
					self.print_debug(f"[DAILY_REPORTING] Comment: {comment_}")
				else:
					comment_ = None

				if completion_date is not None:
					status = 6
					self.print_debug(f"[DAILY_REPORTING] Status set to 6 (Completed)")
				elif (phase_one_material_date != None or phase_two_material_date != None or phase_three_material_date != None) and not(agreement_cancelled):
					status = 5
					self.print_debug(f"[DAILY_REPORTING] Status set to 5 (In Progress)")
				elif (agreement_date is not None and (phase_one_material_date == None and phase_two_material_dates == None and phase_three_material_date == None) and not(agreement_cancelled)):
					status = 3
					self.print_debug(f"[DAILY_REPORTING] Status set to 3 (Agreement Signed)")
				elif agreement_cancelled == True:
					status = 2
					self.print_debug(f"[DAILY_REPORTING] Status set to 2 (Cancelled)")
				else:
					status = None
					self.print_debug(f"[DAILY_REPORTING] Status set to None")

				check_record = ToiletConstruction.objects.filter(household_number=HH, slum_id=slum_id)
				self.print_debug(f"[DAILY_REPORTING] Checking existing records for HH {HH}")
				
				if len(data) > 1:
					if not check_record:
						self.print_debug(f"[DAILY_REPORTING] Creating new ToiletConstruction record for HH {HH}")
						create = ToiletConstruction.objects.create(
							household_number=HH,
							slum_id=slum_id,
							agreement_date=agreement_date,
							agreement_cancelled=agreement_cancelled,
							septic_tank_date=septic_tank_date,
							phase_one_material_date=phase_one_material_date,
							phase_two_material_date=phase_two_material_date,
							phase_three_material_date=phase_three_material_date,
							completion_date=completion_date,
							status=status,
							p1_material_shifted_to=p1_material_shifted_to,
							p2_material_shifted_to=p2_material_shifted_to,
							p3_material_shifted_to=p3_material_shifted_to,
							st_material_shifted_to=st_material_shifted_to,
							comment=comment_
						)
						self.log_sync_record(
							sync_job_id,
							household_details,
							operation_type=1,
							status_code=1,
							message=f"[DAILY_REPORTING] Construction status created for HH {HH} in slum {slum_id}"
						)
						self.print_debug(f'[DAILY_REPORTING] Construction status created for HH {HH} in slum {slum_id}')
					else:
						self.print_debug(f"[DAILY_REPORTING] Updating existing ToiletConstruction record for HH {HH}")
						check_record.update(
							agreement_date=agreement_date,
							agreement_cancelled=agreement_cancelled,
							septic_tank_date=septic_tank_date,
							phase_one_material_date=phase_one_material_date,
							phase_two_material_date=phase_two_material_date,
							phase_three_material_date=phase_three_material_date,
							completion_date=completion_date,
							status=status,
							p1_material_shifted_to=p1_material_shifted_to,
							p2_material_shifted_to=p2_material_shifted_to,
							p3_material_shifted_to=p3_material_shifted_to,
							st_material_shifted_to=st_material_shifted_to,
							comment=comment_
						)
						self.log_sync_record(
							sync_job_id,
							household_details,
							operation_type=2,
							status_code=1,
							message=f"[DAILY_REPORTING] Construction status updated for HH {HH} in slum {slum_id}"
						)
						self.print_debug(f'[DAILY_REPORTING] Construction status updated for HH {HH} in slum {slum_id}')
			except Exception as e:
				self.log_sync_record(
					sync_job_id,
					household_details,
					operation_type=2,
					status_code=2,
					error_message=f"[DAILY_REPORTING] Failed to process Daily Reporting data for HH {HH} in {slum_name}: {e}"
				)
				self.print_debug(f"[ERROR] Failed to process Daily Reporting data for HH {HH} in {slum_name}: {e}")
				raise
		except Exception as e:
			self.print_debug(f"[ERROR] Failed to get household details for Daily Reporting: {e}")
			self.log_sync_record(
				sync_job_id,
				household_details,
				operation_type=2,
				status_code=2,
				error_message=f"[DAILY_REPORTING] Failed to get household details for Daily Reporting: {e}"
			)
			raise