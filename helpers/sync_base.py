import json
import requests
from datetime import timedelta
from django.utils import timezone

from graphs.models import SyncJob, SyncJobRecord
from graphs.sync_avni_data import avni_sync

operation_type_mapping = {
	'1': 'CREATE',
	'2': 'UPDATE',
	'3': 'DELETE'
}

status_type_mapping = {
	'1': 'SUCCESS',
	'2': 'FAILED',
	'3': 'SKIPPED'
}



class AvniBaseSync(avni_sync):
	"""
	Common base class for all Avni sync jobs
	Handles:
	- debug logging
	- sync job lifecycle
	- sync record logging
	dry_run = True → preview only, no DB writes
	debug = True → print debug logs
	"""
	mapping_module_type = {
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

	# ---------- DEBUG ----------

	def print_debug(self, message):
		if self.debug:
			if self.dry_run:
				message = "[DRY RUN] " + message
			print(message)

	# ---------- SYNC JOB HELPERS ----------

	def get_latest_modified_date(self, module_id):
		self.print_debug(f"[SYNC] Fetching last modified date for module {module_id}")
		try:
			obj = SyncJob.objects.filter(
				module__id=module_id,
				status="SUCCESS"
			).order_by('-ended_at').first()

			if obj and obj.ended_at:
				return obj.ended_at.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
		except Exception as e:
			self.print_debug(f"[ERROR] {e}")

		return (timezone.now() - timedelta(days=30)).strftime('%Y-%m-%dT%H:%M:%S.%fZ')

	def start_sync_job(self, module_id, triggered_by="Admin_User"):
		status = "DRY_RUN" if self.dry_run else "RUNNING"
		self.print_debug(f"[SYNC] Starting job for module {module_id}")
		return SyncJob.objects.create(
			module_id=module_id,
			status=status,
			triggered_by=triggered_by,
			started_at=timezone.now()
		)

	def finish_sync_job(self, sync_job, status, total, success, failed, message):
		self.print_debug(f"[SYNC] Finishing job {sync_job.id}")
		sync_job.ended_at = timezone.now()
		sync_job.status = status
		sync_job.total_records = total
		sync_job.success_count = success
		sync_job.failed_skipped_count = failed
		sync_job.message = message
		sync_job.save()

	def log_sync_record(
		self,
		sync_job_id,
		household_details,
		operation_type,
		status_code,
		message=None,
		error_message=None
	):
		self.print_debug(
			f"[RECORD] Job={sync_job_id} HH={household_details.get('household_number')}"
		)

		SyncJobRecord.objects.create(
			job_id=sync_job_id,
			avni_uuid=household_details.get("avni_uuid"),
			operation=operation_type_mapping.get(str(operation_type)),
			status=status_type_mapping.get(str(status_code)),
			household=household_details.get("household_record"),
			message=message,
			error_message=error_message
		)
