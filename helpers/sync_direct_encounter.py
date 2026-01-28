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
import requests
from datetime import timedelta
from django.utils import timezone

from graphs.models import *
from mastersheet.models import *
from master.models import *
from helpers.models import *
from helpers.sync_base import AvniBaseSync


# -------------------- ENCOUNTER DEFINITIONS --------------------

direct_encounters = ['Sanitation', 'Property tax', 'Water', 'Waste', 'Electricity', 'Daily Mobilization Activity']
program_encounters = ['Daily Reporting', 'Family factsheet']



# -------------------- MAIN SYNC CLASS --------------------

class AvniDirectEncounterSync(AvniBaseSync):
	"""
	Encounter sync handler for Avni
	dry_run = True → preview only, no DB writes
	debug = True → print debug logs
	"""

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

	# ---------- RHS UPDATE ----------

	def update_rhs_data(self, encounter_data, household_details, sync_job_id, encounter_type):
		message = ""
		try:
			household_record = household_details.get("household_record")

			# ---- Household missing case ----
			if not household_record:
				self.print_debug("[RHS] Household missing")

				if self.dry_run:
					message = "Household missing → would be registered"
					self.log_sync_record(sync_job_id, household_details, 1, 3, message=message)
					return

				self.registrtation_data(self.get_HH_data)

				household_record = HouseholdData.objects.filter(
					slum_id__name=household_details["slum"],
					household_number=household_details["household_number"]
				).first()

				if not household_record:
					self.log_sync_record(sync_job_id,household_details,1,3,message="Household registration failed",error_message="Failed to register missing household")
					return

				message = "Household was missing, so it has been registered successfully."
				household_details["household_record"] = household_record

			# ---- RHS update ----
			self.print_debug(f"[RHS] Updating RHS for HH {household_details['household_number']}")
			data = household_record.rhs_data or {}
			data.update(encounter_data)
   
			if self.dry_run:
				self.print_debug(f"[RHS] update data for {household_details['household_number']} is {data}")
				message = message + f" RHS would be updated with {encounter_type} encounter."
				self.log_sync_record(sync_job_id, household_details, 2, 3, message=message)
				return

			household_record.rhs_data = data
			household_record.save(update_fields=["rhs_data"])

			message = message + f" RHS data updated with {encounter_type} encounter."
			self.log_sync_record(sync_job_id, household_details, 2, 1, message=message)

		except Exception as e:
			self.print_debug(f"[ERROR] RHS update failed: {e}")
			self.log_sync_record(sync_job_id, household_details, 2, 2, error_message=str(e))

  
	# --- Config ---
	def map_sanitation_keys(self, s):
		map_toilet_keys = {
			'group_oi8ts04/Have_you_applied_for_individua': 'Have you applied for an individual toilet under SBM?_1',
			'group_oi8ts04/Status_of_toilet_under_SBM': 'Status of toilet under SBM ?',
			'group_oi8ts04/What_is_the_toilet_connected_to': 'Where the individual toilet is connected to ?',
			'group_oi8ts04/Who_all_use_toilets_in_the_hou': 'Who all use toilets in the household ?',
			'group_oi8ts04/What_was_the_cost_in_to_build_the_toilet': 'What was the cost incurred to build the toilet?',
			'group_oi8ts04/Type_of_SBM_toilets': 'Type of SBM toilets ?',
			'group_oi8ts04/Reason_for_not_using_toilet': 'Reason for not using toilet ?',
			'group_oi8ts04/How_many_installments_have_you': 'How many installments have you received ?',
			'group_oi8ts04/When_did_you_receive_ur_first_installment': 'When did you receive your first SBM installment?',
			'group_oi8ts04/When_did_you_receive_r_second_installment': 'When did you receive your second SBM installment?',
			'group_oi8ts04/When_did_you_receive_ur_third_installment': 'When did you receive your third SBM installment?',
			'group_oi8ts04/If_built_by_contract_ow_satisfied_are_you': 'If built by contractor, how satisfied are you?',
			'group_oi8ts04/OD1': 'Does any member of the household go for open defecation ?',
			'group_oi8ts04/Current_place_of_defecation': 'Final current place of defecation',
			'group_oi8ts04/Is_there_availabilit_onnect_to_the_toilets': 'Is there availability of drainage to connect it to the toilet?',
			'group_oi8ts04/Are_you_interested_in_an_indiv': 'Are you interested in an individual toilet ?',
			'group_oi8ts04/What_kind_of_toilet_would_you_like': 'What kind of toilet would you like ?',
			'group_oi8ts04/Under_what_scheme_wo_r_toilet_to_be_built': 'Under what scheme would you like your toilet to be built ?',
			'group_oi8ts04/If_yes_why': 'If yes for individual toilet , why?',
			'group_oi8ts04/If_no_why': 'If no for individual toilet , why?',
			'group_oi8ts04/Which_Community_Toil_r_family_members_use': 'Which CTB do your family members use ?',
			'group_el9cl08/Does_any_household_m_n_skills_given_below': 'Does any household member have any of the construction skills given below ?'}
		a = {}
		a.update(s)
		for k, v in map_toilet_keys.items():
			try:
				if k in a.keys() or v in s.keys():
					a[k] = s[v]
					a.pop(v)
			except Exception as e:
				self.print_debug(f"Error mapping sanitation keys: {e}")
		return a
	
	def configData(self, encounter_name, data):
		if encounter_name == "Water":
			if 'Type of water connection ?' in data:
				data['group_el9cl08/Type_of_water_connection'] = data.pop('Type of water connection ?')
		elif encounter_name == "Waste":
			if 'How do you dispose your solid waste ?' in data:
				data['group_el9cl08/Facility_of_solid_waste_collection'] = data.pop('How do you dispose your solid waste ?')
		elif encounter_name == "Sanitation":
			data = self.map_sanitation_keys(data)
		elif encounter_name in ['Property tax', 'Electricity','Daily Mobilization Activity']:
			data = data # No normalization needed currently
	
		return data   


   # --- ENCOUNTER GENERIC SAVE FUNCTION ---
   
	def Save_Direct_Encounter_Data(self,encounter_name,date=None, triggered_by="Admin_User"):
		self.print_debug(f"[ENCOUNTER] {encounter_name} encounter sync started")
		module_id = AvniBaseSync.mapping_module_type[encounter_name]
		sync_job = self.start_sync_job(module_id, triggered_by)	
		if not date: 
			date = self.get_latest_modified_date(AvniBaseSync.mapping_module_type[encounter_name])
		pages, path = self.fetch_encounter_pages(encounter_name, date)
		self.print_debug(f"Saving {encounter_name} data for {pages} pages.")
		total = success = failed = skipped = 0
    
		try:		
			for i in range(pages):
				self.print_debug(f"[{encounter_name}] Processing page {i+1}/{pages}")
				send_request = requests.get(self.base_url + path + '&page=' + str(i), headers={'AUTH-TOKEN': self.get_cognito_token()})
				data = json.loads(send_request.text)['content']
				for j in data:
					total+=1
					if j['Voided'] or not j['observations']:
						skipped+=1
						SyncJobRecord.objects.create(job_id=sync_job.id,avni_uuid=j['Subject ID'],operation='SKIPPED',status='SKIPPED',message=f"{encounter_name} encounter is voided or has no observations.")
						continue
					encounter_data = j['observations']
	
					encounter_data = self.configData(encounter_name, encounter_data)
					self.print_debug(f"Normalized encounter data: {encounter_data}")
					encounter_data.update({'Last_modified_date': j['audit']['Last modified at']})
					self.update_rhs_data(
		 				encounter_data,
			 			self.get_household_details(j['Subject ID']),
			 			sync_job.id,
						encounter_name
		 			)
					success+=1
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
