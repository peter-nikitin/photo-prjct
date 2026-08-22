from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError

from commerce.worker import commerce_worker_health, commerce_worker_is_alive


class Command(BaseCommand):
    help = "Read Commerce worker liveness and the age of its oldest ready work."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--max-ready-age-seconds", type=int, required=True)

    def handle(self, *args, **options) -> None:
        threshold_seconds = options["max_ready_age_seconds"]
        if not isinstance(threshold_seconds, int) or threshold_seconds < 0:
            raise CommandError("--max-ready-age-seconds must be non-negative.")
        health = commerce_worker_health(
            max_ready_age=timedelta(seconds=threshold_seconds),
            worker_is_alive=commerce_worker_is_alive,
        )
        age_seconds = (
            int(health.oldest_ready_age.total_seconds())
            if health.oldest_ready_age is not None
            else None
        )
        self.stdout.write(
            "commerce worker health: "
            f"worker_alive={health.worker_alive} "
            f"oldest_ready_work_type={health.oldest_ready_work_type or 'none'} "
            f"oldest_ready_age_seconds={age_seconds if age_seconds is not None else 'none'}"
        )
        if health.healthy:
            return
        if not health.worker_alive:
            raise CommandError("Commerce worker health check failed: worker is not live.")
        raise CommandError("Commerce worker health check failed: ready work is overdue.")
