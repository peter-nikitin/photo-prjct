"""Check Docker-enforced quotas and privacy without exposing container environment."""

import json
import subprocess

PROJECT = "findme-image-origin-contract"
SERVICES = ("nginx", "imgproxy", "minio", "seed", "acceptance")


def inspect() -> None:
    containers = json.loads(
        subprocess.check_output(
            ["docker", "inspect", *[f"{PROJECT}-{service}-1" for service in SERVICES]], text=True
        )
    )
    cpus = 0.0
    memory = 0
    for container in containers:
        host = container["HostConfig"]
        cpus += host["NanoCpus"] / 1e9
        memory += host["Memory"]
        assert not container["State"]["OOMKilled"], "A fixture service exceeded its memory quota"
        if container["Config"]["Labels"]["com.docker.compose.service"] == "nginx":
            assert container["Config"]["User"] == "101:101"
            assert host["ReadonlyRootfs"] is True
            assert any(
                mount["Destination"] == "/var/www" and not mount["RW"]
                for mount in container["Mounts"]
            ), "Nginx must not be able to modify the ACME webroot"
    assert cpus <= 2, cpus
    assert memory <= 4 * 1024**3, memory
    for service in ["nginx", "imgproxy"]:
        logs = subprocess.check_output(
            ["docker", "logs", f"{PROJECT}-{service}-1"], stderr=subprocess.STDOUT, text=True
        )
        for private in [
            "private-query-value-must-never-be-logged",
            "123456789",
            "contract-event",
            "derivatives/previews",
            "00000000-0000-4000-8000",
            "contract-origin-secret",
            "contract-access",
            "contract-secret",
            "11111111111111111111111111111111",
            "22222222222222222222222222222222",
            "contract-private-photo-description",
            "/gallery-v1/",
            "X-FindMe-Origin-Auth",
        ]:
            assert private not in logs, f"Private request data leaked from {service}"
    print(
        f"RUNTIME_CONTRACT=green cpu_limit={cpus} memory_limit_bytes={memory} private_logs=absent"
    )


if __name__ == "__main__":
    inspect()
