
from django.db import models
from graphs.models import HouseholdData

class SyncCategory(models.Model):
    name = models.CharField(max_length=100)  # High-level sync category name (Direct, Program, Member, RHS)
    description = models.CharField(max_length=255, blank=True, null=True)  # Optional description

    class Meta:
        db_table = "sync_category"

    def __str__(self):
        return self.name


class SyncModule(models.Model):
    category = models.ForeignKey(SyncCategory, on_delete=models.CASCADE, related_name="modules")  # Category this module belongs to
    name = models.CharField(max_length=100)  # Module name (Water, Sanitation, Household, Factsheet)
    description = models.CharField(max_length=255, blank=True, null=True)  # Optional description

    class Meta:
        db_table = "sync_module"

    def __str__(self):
        return f"{self.category.name} - {self.name}"


class SyncJob(models.Model):
    class JobStatus(models.TextChoices):
        PENDING = "PENDING"
        RUNNING = "RUNNING"
        SUCCESS = "SUCCESS"
        FAILED = "FAILED"
        PARTIAL = "PARTIAL"

    module = models.ForeignKey(SyncModule, on_delete=models.CASCADE, related_name="jobs")  # Module this job syncs
    triggered_by = models.CharField(max_length=100, blank=True, null=True)  # Cron/user trigger source
    started_at = models.DateTimeField()  # When job started
    ended_at = models.DateTimeField(blank=True, null=True)  # When job ended

    total_records = models.IntegerField(default=0)  # Total processed records
    success_count = models.IntegerField(default=0)  # Successfully processed records
    failed_skipped_count = models.IntegerField(default=0)  # Failed or skipped records

    message = models.CharField(max_length=255, blank=True, null=True)  # Short result message
    description = models.CharField(max_length=255, blank=True, null=True)  # Extended description
    status = models.CharField(max_length=20, choices=JobStatus.choices)  # Job status

    class Meta:
        db_table = "sync_job"

    def __str__(self):
        return f"Job {self.id} - {self.module.name} - {self.status}"


class SyncJobRecord(models.Model):
    class Operation(models.TextChoices):
        CREATE = "CREATE"
        UPDATE = "UPDATE"
        DELETE = "DELETE"

    class RecordStatus(models.TextChoices):
        SUCCESS = "SUCCESS"
        FAILED = "FAILED"
        SKIPPED = "SKIPPED"

    job = models.ForeignKey(SyncJob, on_delete=models.CASCADE, related_name="records")  # Parent job
    avni_uuid = models.CharField(max_length=100, blank=True, null=True, db_index=True)  # Avni UUID
    operation = models.CharField(max_length=10, choices=Operation.choices)  # Operation performed
    status = models.CharField(max_length=10, choices=RecordStatus.choices)  # Record status
     # SET_NULL ensures audit logs are preserved; household may be deleted but record must stay
    household = models.ForeignKey(HouseholdData, on_delete=models.SET_NULL, null=True, blank=True, related_name="sync_records", db_index=True) # Household reference
    error_message = models.TextField(blank=True, null=True)  # Failure reason
    message = models.CharField(max_length=255, blank=True, null=True)  # Short comment

    class Meta:
        db_table = "sync_job_record"

    def __str__(self):
        return f"Record {self.id} - Job {self.job_id} - {self.status}"
