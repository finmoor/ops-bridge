import logging

import pytest

from core import startup_checks as s


def _mounts(*pairs):
    return {'Mounts': [{'Type': 'bind', 'Source': src, 'Destination': dst, 'RW': True} for src, dst in pairs]
            + [{'Type': 'volume', 'Source': '/var/lib/docker/volumes/x', 'Destination': '/opt'}]}


@pytest.fixture
def exists(monkeypatch):
    monkeypatch.setattr(s.os.path, 'isdir', lambda path: True)


def test_same_path_mount_is_ok(exists, monkeypatch, caplog):
    monkeypatch.setattr(s, 'own_container', lambda: _mounts(('/opt/apps', '/opt/apps'), ('/var/run/docker.sock', '/var/run/docker.sock')))
    with caplog.at_level(logging.ERROR):
        assert s.check_projects_root('/opt/apps') == 'ok'
    assert not caplog.records


def test_root_below_a_same_path_mount_is_ok(exists, monkeypatch):
    monkeypatch.setattr(s, 'own_container', lambda: _mounts(('/srv', '/srv')))
    assert s.check_projects_root('/srv/apps/') == 'ok'


def test_old_compose_mapping_to_data_is_reported(exists, monkeypatch, caplog):
    """旧写法 ${MANAGED_PROJECTS_ROOT}:/data：中枢下发的宿主机路径在容器里找不到，相对挂载也指错位置。"""
    monkeypatch.setattr(s, 'own_container', lambda: _mounts(('/opt/apps', '/data')))
    with caplog.at_level(logging.ERROR):
        assert s.check_projects_root('/data') == 'mismatch'
    assert '/opt/apps' in caplog.text and '同一路径' in caplog.text


def test_longest_mount_wins(exists, monkeypatch):
    monkeypatch.setattr(s, 'own_container', lambda: _mounts(('/', '/host'), ('/data', '/data')))
    assert s.check_projects_root('/data/projects') == 'ok'


def test_root_not_under_any_bind_mount(exists, monkeypatch, caplog):
    monkeypatch.setattr(s, 'own_container', lambda: _mounts(('/var/run/docker.sock', '/var/run/docker.sock')))
    with caplog.at_level(logging.ERROR):
        assert s.check_projects_root('/data') == 'unmounted'
    assert 'MANAGED_PROJECTS_ROOT' in caplog.text


def test_missing_root(monkeypatch, caplog):
    monkeypatch.setattr(s.os.path, 'isdir', lambda path: False)
    monkeypatch.setattr(s, 'own_container', lambda: pytest.fail('目录都不存在，不必再查挂载'))
    with caplog.at_level(logging.ERROR):
        assert s.check_projects_root('/data') == 'missing'
    assert 'PROJECTS_ROOT=/data' in caplog.text


def test_not_in_a_container_skips_mount_check(exists, monkeypatch, caplog):
    """本地开发直接 python agent.py：查不到自己的容器，不报错。"""
    monkeypatch.setattr(s, 'own_container', lambda: (_ for _ in ()).throw(RuntimeError('no such container')))
    with caplog.at_level(logging.ERROR):
        assert s.check_projects_root('/data') == 'unknown'
    assert not caplog.records


def test_defaults_to_configured_root(exists, monkeypatch):
    monkeypatch.setattr(s, 'PROJECTS_ROOT', '/opt/apps')
    monkeypatch.setattr(s, 'own_container', lambda: _mounts(('/opt/apps', '/opt/apps')))
    assert s.check_projects_root() == 'ok'
