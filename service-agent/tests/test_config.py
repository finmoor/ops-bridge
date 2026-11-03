import importlib
import sys
from datetime import datetime

import pytest


def _import_config(monkeypatch: pytest.MonkeyPatch, **overrides: str):
    defaults = {
        "WS_URL": "ws://localhost:8080/ws/agent",
        "AGENT_KEY": "agent-key-1",
        "AGENT_ID": "agent-test",
        "RECONNECT_DELAY": "7",
        "HEARTBEAT_INTERVAL": "11",
        "HEALTH_HOST": "127.0.0.1",
        "HEALTH_PORT": "18081",
    }
    for key, value in defaults.items():
        monkeypatch.setenv(key, value)
    for key, value in overrides.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    sys.modules.pop("config", None)
    return importlib.import_module("config")


def test_config_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _import_config(monkeypatch)

    assert config.WS_URL == "ws://localhost:8080/ws/agent"
    assert config.AGENT_KEY == "agent-key-1"
    assert config.AGENT_ID == "agent-test"
    assert config.RECONNECT_DELAY == 7
    assert config.HEARTBEAT_INTERVAL == 11
    assert config.HEALTH_HOST == "127.0.0.1"
    assert config.HEALTH_PORT == 18081


def test_config_requires_ws_url(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit, match="WS_URL is not set"):
        _import_config(monkeypatch, WS_URL="")


def test_config_requires_agent_key(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit, match="AGENT_KEY is not set"):
        _import_config(monkeypatch, AGENT_KEY="")


@pytest.mark.parametrize("value", [None, "", "   "])
def test_config_requires_agent_id(monkeypatch: pytest.MonkeyPatch, value) -> None:
    """不能退回主机名：容器主机名是随机串，中枢不认识，只会一直鉴权失败。"""
    with pytest.raises(SystemExit, match="AGENT_ID is not set.*服务器编码"):
        _import_config(monkeypatch, AGENT_ID=value)


def test_config_strips_agent_id(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _import_config(monkeypatch, AGENT_ID="  srv-01 \n").AGENT_ID == "srv-01"


@pytest.mark.parametrize("outbox", [None, ""])
def test_outbox_path_defaults_under_projects_root(monkeypatch: pytest.MonkeyPatch, outbox) -> None:
    """账本跟着被管根目录走：自定义 MANAGED_PROJECTS_ROOT 后 /data 不再是挂载卷，写在那里容器重建就丢。"""
    config = _import_config(monkeypatch, PROJECTS_ROOT="/opt/apps", OUTBOX_PATH=outbox)
    assert config.OUTBOX_PATH == "/opt/apps/.service-agent/outbox.json"


def test_outbox_path_and_projects_root_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _import_config(monkeypatch, PROJECTS_ROOT=" ", OUTBOX_PATH="/state/outbox.json")
    assert config.PROJECTS_ROOT == "/data"
    assert config.OUTBOX_PATH == "/state/outbox.json"


def test_china_time_formatter_respects_datefmt(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _import_config(monkeypatch)
    formatter = config.ChinaTimeFormatter("%(asctime)s", "%Y-%m-%d %H:%M:%S")
    record = pytest.importorskip("logging").LogRecord(
        name="test",
        level=20,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    record.created = datetime(2026, 3, 13, 7, 30, 0, tzinfo=config.timezone.utc).timestamp()

    assert formatter.formatTime(record, "%Y-%m-%d %H:%M:%S") == "2026-03-13 15:30:00"


def test_hub_http_url_and_projects_root_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib
    import sys

    monkeypatch.setenv("WS_URL", "ws://hub.example/ws/agent")
    monkeypatch.setenv("AGENT_KEY", "k")
    monkeypatch.delenv("HUB_HTTP_URL", raising=False)
    monkeypatch.delenv("PROJECTS_ROOT", raising=False)
    sys.modules.pop("config", None)
    cfg = importlib.import_module("config")
    assert cfg.HUB_HTTP_URL == ""
    assert cfg.PROJECTS_ROOT == "/data"

    monkeypatch.setenv("HUB_HTTP_URL", "https://hub.example")
    monkeypatch.setenv("PROJECTS_ROOT", "/srv")
    sys.modules.pop("config", None)
    cfg2 = importlib.import_module("config")
    assert cfg2.HUB_HTTP_URL == "https://hub.example"
    assert cfg2.PROJECTS_ROOT == "/srv"
