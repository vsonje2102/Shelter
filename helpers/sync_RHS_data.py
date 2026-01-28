from helpers.sync_base import AvniBaseSync
from graphs.models import *
from helpers.models import *
import requests
import json
from datetime import timedelta


class AvniSyncRHSData(AvniBaseSync):

    def SaveRhsData(self, subject_type,date=None,triggered_by="Admin_User"):  # checked
        self.print_debug(f"[RHS SYNC] Starting RHS data sync for subject type: {subject_type}")
        module_id = AvniBaseSync.mapping_module_type[subject_type]
        sync_job = self.start_sync_job(module_id=module_id, triggered_by=triggered_by)  
        if not date:
            date = self.get_latest_modified_date(module_id=module_id)
        else:
            date = date.strftime('%Y-%m-%dT00:00:00.000Z')
        pages, path = self.create_registrationdata_url(subject_type,date)
        total = success = failed = skipped = 0
        try:
            for ij in range(pages):
                self.print_debug(f"[RHS SYNC] Processing page {ij+1} of {pages} for subject type: {subject_type}")
                send_request = requests.get(self.base_url + path + '&page=' + str(ij), headers={'AUTH-TOKEN': self.get_cognito_token()})
                get_HH_data = json.loads(send_request.text)['content']
                for i in get_HH_data:
                    total += 1
                    if not (i['Voided']):
                        
                        self.registration_data(i,sync_job.id)
                    else:
                        skipped += 1
                        SyncJobRecord.objects.create(
							job_id=sync_job.id,
							avni_uuid=i['ID'],
							operation='SKIPPED',
							status='SKIPPED',
							message='Record is voided'
						)
                        self.print_debug("Record is voided")
                self.print_debug(f"Page {ij+1} of {pages} processed.")
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
                   
    # This function is used to create rhs data url when we sync data page wise.
    def create_registrationdata_url(self,subject_type,latest_date):  # checked
        self.print_debug(f"[RHS SYNC] Creating RHS data URL for subject type: {subject_type} from date: {latest_date}")
        latest_date = latest_date - timedelta(days=1)
        latest_date = latest_date.strftime('%Y-%m-%dT00:00:00.000Z')
        self.print_debug(f"[RHS SYNC] Creating RHS data URL for subject type: {subject_type} from date: {latest_date}")
        household_path = 'api/subjects?lastModifiedDateTime=' + latest_date + '&subjectType=' + subject_type
        result = requests.get(self.base_url + household_path, headers={'AUTH-TOKEN': self.get_cognito_token()})
        get_text = json.loads(result.text)['content']
        pages = json.loads(result.text)['totalPages']
        self.print_debug(f"[RHS SYNC] Created RHS data URL for subject type: {subject_type} with {pages} pages to fetch.")
        return pages, household_path

    def registration_data(self, HH_data,sync_job_id):  # checked
        final_rhs_data = {}
        rhs_from_avni = HH_data['observations']
        if rhs_from_avni.get('Functioning of the structure') == "Shop":
            rhs_from_avni['Type_of_structure_occupancy'] = rhs_from_avni.pop('Functioning of the structure')
        elif rhs_from_avni.get('Functioning of the structure') == "Individual home + shop":
            rhs_from_avni['Type_of_structure_occupancy'] = rhs_from_avni.pop('Functioning of the structure')
        # household_number = str(int(rhs_from_avni['First name']))
        value = str(rhs_from_avni.get('First name', '')).strip()

        try:
            household_number = str(int(value))  # works if it's pure digits
        except ValueError:
            household_number = value           # fallback for things like "123A"
        self.print_debug(f"Household number is {household_number}")
        
        created_date = HH_data['Registration date']
        submission_date = (HH_data['audit']['Last modified at'])  # use last modf date
        try:
            slum_name = HH_data['location']['Slum']
            slum_id, city_id = self.get_city_slum_ids(slum_name)
            check_record = HouseholdData.objects.filter(household_number=household_number, city_id=city_id,
                                                        slum_id=slum_id)
            if not check_record:
                self.log_sync_record()
                rhs_data = {}
                final_rhs_data = self.map_rhs_key(rhs_data, rhs_from_avni)
                if final_rhs_data.get('Do you have a toilet at home?') == "Yes":
                    final_rhs_data['Current place of defecation'] = "Own toilet"
                if final_rhs_data.get('Ownership status of the house_1') == "Own house/Shop":
                    final_rhs_data['Ownership status of the house_1'] = "Own house"
                final_rhs_data.update({'rhs_uuid': HH_data['ID']})
                if 'group_og5bx85/Type_of_survey' not in final_rhs_data:
                    final_rhs_data['group_og5bx85/Type_of_survey'] = 'RHS'
                print(final_rhs_data)
                update_record = HouseholdData.objects.create(household_number=household_number, slum_id=slum_id, city_id=city_id, submission_date=submission_date, rhs_data=final_rhs_data, created_date=created_date)
                print('Household record created for', slum_name, household_number)
            else:
                rhs_data = check_record.values_list('rhs_data', flat=True)[0]
                if rhs_data.get('Functioning of the structure') == "Shop":
                    rhs_data['Type_of_structure_occupancy'] = rhs_data.pop('Functioning of the structure')
                elif rhs_data.get('Functioning of the structure') == "Individual home + shop":
                    rhs_data['Type_of_structure_occupancy'] = rhs_data.pop('Functioning of the structure')
                if rhs_data is None or len(rhs_data) == 0:
                    rhs_data = {}
                if rhs_from_avni.get('Ownership status of the house_1') == "Own house/Shop":
                    rhs_from_avni['Ownership status of the house_1'] = "Own house"
                if rhs_from_avni.get('Do you have a toilet at home?') == "Yes":
                    rhs_from_avni['Current place of defecation'] = "Own toilet" 
                final_rhs_data = self.map_rhs_key(rhs_data, rhs_from_avni)
               
                final_rhs_data.update({'rhs_uuid': HH_data['ID']})
                final_rhs_data['group_og5bx85/Type_of_survey'] = 'RHS'
                print(final_rhs_data)
                check_record.update(submission_date=submission_date, rhs_data=final_rhs_data, created_date=created_date)
                print('Household record updated for', slum_name, household_number)
        except Exception as e:
            print('second exception', slum_name, e)
