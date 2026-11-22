import logging
import os
import signal
import time
from multiprocessing import get_context
from multiprocessing.connection import wait
from multiprocessing.process import BaseProcess

import psycopg
from config.queue_notifications import MATCHING_CHANNEL, listen, wait_for_notifications
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection, connections

from selfie_search.services.face_search_queue import (
    next_wakeup_delay,
    recover_missing_payloads,
    recover_processing,
    run_once,
)

logger = logging.getLogger(__name__)

MATCHING_WATCHDOG_SECONDS = 150
IDLE_POLL_SECONDS = 0.5
CHILD_SHUTDOWN_SECONDS = 2


def attempt_timeout(signum, frame):
    os._exit(124)


def terminate_immediately(signum, frame):
    os._exit(0)


def recover_at_startup() -> None:
    for recover in (recover_processing, recover_missing_payloads):
        while True:
            try:
                signal.alarm(MATCHING_WATCHDOG_SECONDS)
                recover()
                break
            except DatabaseError:
                logger.error("Face-search database unavailable during startup")
                connection.close()
            finally:
                signal.alarm(0)
            time.sleep(IDLE_POLL_SECONDS)


def run_worker() -> None:
    signal.signal(signal.SIGTERM, terminate_immediately)
    signal.signal(signal.SIGALRM, attempt_timeout)
    try:
        with listen(MATCHING_CHANNEL, application_name="findme-matching-worker") as listener:
            while True:
                try:
                    signal.alarm(MATCHING_WATCHDOG_SECONDS)
                    worked = run_once()
                    delay = None if worked else next_wakeup_delay()
                finally:
                    signal.alarm(0)
                if not worked:
                    wait_for_notifications(listener, timeout=delay)
    except (DatabaseError, psycopg.Error, OSError):
        logger.error("Face-search database or notification connection unavailable")
        connection.close()
        # A fresh generation subscribes and recovers interrupted processing.
        raise SystemExit(1) from None


def stop_workers(workers: list[BaseProcess]) -> None:
    for worker in workers:
        if worker.is_alive():
            worker.terminate()
    deadline = time.monotonic() + CHILD_SHUTDOWN_SECONDS
    for worker in workers:
        worker.join(timeout=max(0, deadline - time.monotonic()))
    for worker in workers:
        if worker.is_alive():
            worker.kill()
    for worker in workers:
        worker.join()
        worker.close()


def supervise_workers(concurrency: int) -> None:
    stopping = False

    def request_stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    # Children inherit configured Django settings and open their own database connections.
    connections.close_all()
    context = get_context("fork")
    workers: list[BaseProcess] = []
    try:
        for _ in range(concurrency):
            if stopping:
                return
            worker = context.Process(target=run_worker)
            worker.start()
            workers.append(worker)
        sentinels = [worker.sentinel for worker in workers]
        while not stopping:
            if wait(sentinels, timeout=IDLE_POLL_SECONDS):
                logger.error("Face-search executor exited. Restarting the worker generation.")
                raise SystemExit(1)
    finally:
        stop_workers(workers)


class Command(BaseCommand):
    help = "Recover interrupted face searches on startup and run the face-search executor."

    def add_arguments(self, parser):
        parser.add_argument("--concurrency", type=int, default=1)

    def handle(self, *args, **options):
        concurrency = options["concurrency"]
        if concurrency < 1:
            raise CommandError("--concurrency must be positive.")

        signal.signal(signal.SIGTERM, terminate_immediately)
        signal.signal(signal.SIGALRM, attempt_timeout)
        connection.settings_dict.setdefault("OPTIONS", {}).update(
            connect_timeout=5,
            options="-c statement_timeout=30000 -c lock_timeout=1000",
        )
        recover_at_startup()
        if concurrency == 1:
            run_worker()
        else:
            supervise_workers(concurrency)
