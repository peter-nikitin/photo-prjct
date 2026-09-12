from import_worker.contracts import Config


def test_contract_limits_keep_one_request_well_inside_lease():
    config = Config("http://web:8000/internal/photo-import/v1/", "secret")
    assert config.version == 1
    assert config.max_bytes == 50 * 1024 * 1024
    assert config.json_bytes == 1024 * 1024
    assert config.page_size == 100
    assert config.request_seconds == 45
    assert config.heartbeat_seconds == 20
    assert config.lease_seconds == 120
    assert config.request_seconds + config.heartbeat_seconds < config.lease_seconds
