from photo_worker.bib_recognition import BIB_CONFIGURATION_SHA256
from photo_worker.contracts import (
    BIB_INFERENCE_CONFIGURATION_SHA256 as WORKER_BIB_INFERENCE_CONFIGURATION_SHA256,
)
from processing.services.bibs import BIB_INFERENCE_CONFIGURATION_SHA256, bib_configuration

LINUX_BIB_CONFIGURATION_SHA256 = "32b3f2c94202df7c90e5c799c9ca21b760d5d2ac9c376fe578f6f330e415edf9"


def test_backend_claim_uses_the_packaged_linux_bib_configuration_identity() -> None:
    bib = bib_configuration()["bib_recognition"]
    assert isinstance(bib, dict)
    assert BIB_INFERENCE_CONFIGURATION_SHA256 == LINUX_BIB_CONFIGURATION_SHA256
    assert bib["inference_configuration_sha256"] == LINUX_BIB_CONFIGURATION_SHA256
    assert WORKER_BIB_INFERENCE_CONFIGURATION_SHA256 == LINUX_BIB_CONFIGURATION_SHA256
    assert BIB_CONFIGURATION_SHA256 == LINUX_BIB_CONFIGURATION_SHA256
