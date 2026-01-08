from helpers.models import SyncCategory, SyncModule


def run():
	# ---- Categories ----
	direct = SyncCategory.objects.get_or_create(
		name="Direct Encounters",
		description="Sync of direct household encounter types such as Water, Sanitation, Waste, Electricity, etc."
	)[0]

	program = SyncCategory.objects.get_or_create(
		name="Program Encounters",
		description="Program-level encounter sync including Family Factsheet and Daily Reporting."
	)[0]

	member = SyncCategory.objects.get_or_create(
		name="Member Data",
		description="Sync of member core data, program enrollments and member encounters."
	)[0]

	rhs = SyncCategory.objects.get_or_create(
		name="RHS Data",
		description="RHS (Rapid Household Survey) sync for households and structures."
	)[0]

	# ---- Direct Encounter Modules ----
	SyncModule.objects.get_or_create(category=direct, name="Sanitation", description="Sanitation encounter sync.")
	SyncModule.objects.get_or_create(category=direct, name="Water", description="Water encounter sync.")
	SyncModule.objects.get_or_create(category=direct, name="Waste", description="Waste management encounter sync.")
	SyncModule.objects.get_or_create(category=direct, name="Electricity", description="Electricity encounter sync.")
	SyncModule.objects.get_or_create(category=direct, name="Property Tax", description="Property tax encounter sync.")
	SyncModule.objects.get_or_create(category=direct, name="Daily Mobilization Activity", description="DMA sync.")

	# ---- Program Encounters ----
	SyncModule.objects.get_or_create(category=program, name="Family Factsheet", description="Family Factsheet sync.")
	SyncModule.objects.get_or_create(category=program, name="Daily Reporting", description="Daily Reporting sync.")

	# ---- Member Data ----
	SyncModule.objects.get_or_create(category=member, name="Member Core Data", description="Member core info sync.")
	SyncModule.objects.get_or_create(category=member, name="Member Program Data", description="Member program sync.")
	SyncModule.objects.get_or_create(category=member, name="Member Encounter Data", description="Member encounter sync.")

	# ---- RHS Data ----
	SyncModule.objects.get_or_create(category=rhs, name="Structure", description="Structure RHS sync.")
	SyncModule.objects.get_or_create(category=rhs, name="Household", description="Household RHS sync.")

	print("✔ Sync categories & modules inserted successfully with descriptions.")


# 🔥 IMPORTANT → Call the function so it executes when file is piped
run()
