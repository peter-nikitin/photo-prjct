"""Exercise the production route tree with local receivers and accelerated timers."""

import json
import queue
import subprocess
import threading
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml

from tests.monitoring.test_alert_reconciliation import ROOT


def test_alertmanager_batches_updates_and_recovery_per_vm(tmp_path):
    received = queue.Queue()

    class Webhook(BaseHTTPRequestHandler):
        def do_POST(self):
            received.put(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", 0), Webhook)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = yaml.safe_load((ROOT / "deploy/monitoring/prometheus/alertmanager.yml").read_text())
    config["route"].update(group_wait="1s", group_interval="2s", repeat_interval="1h")
    # Preserve routes/matchers/inhibition; replace only the production receivers.
    for receiver in config["receivers"]:
        name = receiver["name"]
        receiver.clear()
        receiver.update(
            name=name,
            webhook_configs=[
                {
                    "url": f"http://host.docker.internal:{server.server_port}/",
                    "send_resolved": True,
                }
            ],
        )
    path = tmp_path / "alertmanager.yml"
    path.write_text(yaml.safe_dump(config))
    container = None
    try:
        result = subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "--add-host",
                "host.docker.internal:host-gateway",
                "-p",
                "127.0.0.1::9093",
                "-v",
                f"{path}:/etc/alertmanager/alertmanager.yml:ro",
                "prom/alertmanager:v0.28.1",
                "--config.file=/etc/alertmanager/alertmanager.yml",
                "--cluster.listen-address=",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        container = result.stdout.strip()
        port = (
            subprocess.run(
                ["docker", "port", container, "9093"], capture_output=True, text=True, check=True
            )
            .stdout.strip()
            .rsplit(":", 1)[1]
        )
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 20
        while True:
            try:
                urllib.request.urlopen(base + "/-/ready", timeout=1).close()
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)

        def alert(name, incident, instance="vm-one", resolved=False):
            now = datetime.now(UTC)
            return {
                "labels": {
                    "alertname": name,
                    "incident": incident,
                    "instance": instance,
                    "project": "findme-photo",
                    "notification": "actionable",
                    "severity": "critical",
                },
                "startsAt": (now - timedelta(minutes=1)).isoformat(),
                "endsAt": (now + timedelta(seconds=-1 if resolved else 120)).isoformat(),
            }

        first = alert("ReadyWorkStalled", "queue")
        second = alert("DiskSpaceCritical", "disk")
        other = alert("ReadyWorkStalled", "queue", "vm-two")

        def send(alerts):
            req = urllib.request.Request(
                base + "/api/v2/alerts",
                json.dumps(alerts).encode(),
                {"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=3).close()

        send([first, second, other])
        initial = [received.get(timeout=12), received.get(timeout=12)]
        by_vm = {item["groupLabels"]["instance"]: item for item in initial}
        assert set(by_vm) == {"vm-one", "vm-two"}
        assert {a["labels"]["alertname"] for a in by_vm["vm-one"]["alerts"]} == {
            "ReadyWorkStalled",
            "DiskSpaceCritical",
        }
        assert by_vm["vm-one"]["groupLabels"] == {"project": "findme-photo", "instance": "vm-one"}
        third = alert("ApplicationLatencyHigh", "application")
        send([first, second, other, third])
        update = received.get(timeout=8)
        assert update["groupLabels"]["instance"] == "vm-one"
        assert len(update["alerts"]) == 3
        # Repeated evaluations do not become repeated notifications.
        send([first, second, other, third])
        try:
            duplicate = received.get(timeout=3)
        except queue.Empty:
            pass
        else:
            raise AssertionError(f"unexpected duplicate: {duplicate}")
        ended = [
            {**a, "endsAt": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}
            for a in (first, second, third)
        ]
        send(ended)
        recovery = received.get(timeout=8)
        assert recovery["status"] == "resolved"
        assert recovery["groupLabels"]["instance"] == "vm-one"
        assert len(recovery["alerts"]) == 3
        try:
            duplicate = received.get(timeout=3)
        except queue.Empty:
            pass
        else:
            raise AssertionError(f"unexpected recovery duplicate: {duplicate}")
    finally:
        if container:
            subprocess.run(["docker", "rm", "-f", container], capture_output=True, check=False)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
