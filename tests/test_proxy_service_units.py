import plistlib
from harness.proxy_service import service_launchd, service_systemd

LABEL = "com.quiubo.done.proxy"


def test_launchd_plist_passes_config_and_mgmt_env():
    raw = service_launchd.build_plist("/bin/cli-proxy-api", "/cfg/config.yaml",
                                      "secret123", LABEL)
    doc = plistlib.loads(raw)
    assert doc["Label"] == LABEL
    assert "--config" in doc["ProgramArguments"]
    assert "/cfg/config.yaml" in doc["ProgramArguments"]
    assert doc["EnvironmentVariables"]["MANAGEMENT_PASSWORD"] == "secret123"
    assert doc["KeepAlive"] is True
    assert doc["ThrottleInterval"] == 10


def test_systemd_unit_has_config_restart_and_mgmt_env():
    unit = service_systemd.build_unit("/bin/cli-proxy-api", "/cfg/config.yaml",
                                      "secret123", LABEL)
    assert "ExecStart=/bin/cli-proxy-api --config /cfg/config.yaml" in unit
    assert "Environment=MANAGEMENT_PASSWORD=secret123" in unit
    assert "Restart=always" in unit
    assert "RestartSec=5" in unit
