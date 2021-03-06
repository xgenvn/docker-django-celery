from django.db import models


class Job(models.Model):
    TYPES = (
        ('fibonacci', 'fibonacci'),
        ('power', 'power'),
        ('sleepwake', 'sleepwake'),
        ('syncsleepwake', 'syncsleepwake'),
    )

    STATUSES = (
        ('pending', 'pending'),
        ('started', 'started'),
        ('finished', 'finished'),
        ('failed', 'failed'),
    )

    type = models.CharField(choices=TYPES, max_length=20)
    status = models.CharField(choices=STATUSES, max_length=20)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    argument = models.PositiveIntegerField()
    result = models.IntegerField(null=True)

    user_id = models.IntegerField()

    def save(self, *args, **kwargs):
        super(Job, self).save(*args, **kwargs)
        if self.status == 'pending':
            if self.type == 'syncsleepwake':
                from .tasks import syncsleepwake
                syncsleepwake(job_id=self.id, n=self.argument)
                return
                
            from .tasks import TASK_MAPPING
            task = TASK_MAPPING[self.type]
            task.delay(job_id=self.id, n=self.argument)