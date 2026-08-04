import os

from prometheus_client import multiprocess


def child_exit(server, worker) -> None:  # noqa: ARG001
    metrics_directory = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if metrics_directory:
        multiprocess.mark_process_dead(worker.pid, path=metrics_directory)
