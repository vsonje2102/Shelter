from graphs.sync_avni_data import avni_sync
from graphs.models import *
import requests
import json
from datetime import timedelta

class AvniSyncRHSData(avni_sync):
    def SaveRhsData(self,subject_type):  # checked
        pages, path = self.create_registrationdata_url(subject_type)    
        all_records = []
        for ij in range(pages):
            send_request = requests.get(self.base_url + path + '&page=' + str(ij), headers={'AUTH-TOKEN': self.get_cognito_token()})
            get_HH_data = json.loads(send_request.text)['content']
            for i in get_HH_data:
                if not (i['Voided']):
                    self.registration_data(i)
                else:
                    print("Record is voided")
            print(f"Page {ij+1} of {pages} processed.")

    # This function is used to create rhs data url when we sync data page wise.
    def create_registrationdata_url(self,subject_type):  # checked
        names = ["Banthara Town", "Mohanlalganj City"]
        if subject_type == "Structure":
            obj = HouseholdData.objects.filter(city__name__city_name__in=names).order_by('-submission_date').first()
        elif subject_type == "Household":
            obj = HouseholdData.objects.exclude(city__name__city_name__in=names).order_by('-submission_date').first()

        latest_date = obj.submission_date + timedelta(days=1)
        latest_date = latest_date.strftime('%Y-%m-%dT00:00:00.000Z')

        household_path = 'api/subjects?lastModifiedDateTime=' + latest_date + '&subjectType=' + subject_type
        result = requests.get(self.base_url + household_path, headers={'AUTH-TOKEN': self.get_cognito_token()})
        get_text = json.loads(result.text)['content']
        pages = json.loads(result.text)['totalPages']
        return pages, household_path

    def registration_data(self, HH_data):  # checked
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
        print(f"Household number is {household_number}")
        
        created_date = HH_data['Registration date']
        submission_date = (HH_data['audit']['Last modified at'])  # use last modf date
        try:
            slum_name = HH_data['location']['Slum']
            slum_id, city_id = self.get_city_slum_ids(slum_name)
            check_record = HouseholdData.objects.filter(household_number=household_number, city_id=city_id,
                                                        slum_id=slum_id)
            if not check_record:
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
