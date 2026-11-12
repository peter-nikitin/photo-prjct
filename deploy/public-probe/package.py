"""Build the source bundle that travels with the approved Terraform plan."""

from importlib.metadata import distribution
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[2]
FILES = {
    "index.py": ROOT / "deploy/public-probe/index.py",
    "requirements.txt": ROOT / "deploy/public-probe/requirements.txt",
    "monitor_public_health.py": ROOT / "scripts/monitor_public_health.py",
}


def build(output: Path) -> None:
    prometheus = distribution("prometheus-prometheus-protocolbuffers-python")
    gogo = distribution("gogo-protobuf-protocolbuffers-python")
    sources = {
        **FILES,
        "remote_pb2.py": Path(prometheus.locate_file("remote_pb2.py")),
        "types_pb2.py": Path(prometheus.locate_file("types_pb2.py")),
        "gogoproto/gogo_pb2.py": Path(gogo.locate_file("gogoproto/gogo_pb2.py")),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name, source in sources.items():
            entry = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, source.read_bytes())


if __name__ == "__main__":
    build(ROOT / "infra/terraform/public-probe.zip")
