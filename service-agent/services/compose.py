import copy
import json
import logging
import os
import re
import subprocess
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def get_compose_cmd():
    """
    仅使用 Docker Compose v2 插件。
    """
    try:
        result = subprocess.run(['docker', 'compose', 'version'], capture_output=True, timeout=5)
        if result.returncode == 0:
            logger.info("Using 'docker compose' (v2 plugin).")
            return ['docker', 'compose']
    except Exception:
        pass

    raise RuntimeError("'docker compose' (v2 plugin) is required but unavailable.")


_compose_cmd = None


def _get_compose_cmd():
    global _compose_cmd
    if _compose_cmd is None:
        _compose_cmd = get_compose_cmd()
    return _compose_cmd


def find_compose_file(project_dir):
    """按常见文件名在 project_dir 下查找 compose 文件，找不到返回 None。"""
    for name in ('docker-compose.yaml', 'docker-compose.yml'):
        path = os.path.join(project_dir, name)
        if os.path.isfile(path):
            return path
    return None


def read_compose_file(compose_file):
    """按原样读取（不转换换行符），改写和回滚都能保持文件原来的 CRLF / LF。"""
    with open(compose_file, encoding='utf-8', newline='') as f:
        return f.read()


def restore_compose_file(compose_file, original_content):
    with open(compose_file, 'w', encoding='utf-8', newline='') as f:
        f.write(original_content)


SERVICE_KEY = re.compile(r'(["\']?)([^"\':#\s][^"\':#]*)\1\s*:\s*(#.*)?')


def replace_service_image(text, service, image):
    """只替换 services.<service>.image 这一行，保留其余内容、注释、引号和换行符。

    整份 YAML 重新序列化会丢注释，并按 YAML 1.1 改写未加引号的值（如端口 22:22、on/off、0022）。
    仅支持块样式 Compose，image 须以单行键值直接写在该服务自己的映射里；定位不到时抛 ValueError。
    """
    lines = text.splitlines(keepends=True)
    in_services = False
    service_indent = None
    child_indent = None
    in_service = False
    for index, line in enumerate(lines):
        body = line.rstrip('\r\n')
        stripped = body.strip()
        if not stripped or stripped.startswith('#'):
            continue
        indent = len(body) - len(body.lstrip(' '))
        if indent == 0:
            in_services = re.fullmatch(r'services\s*:\s*(#.*)?', stripped) is not None
            service_indent = None
            in_service = False
            continue
        if not in_services:
            continue
        if service_indent is None or indent <= service_indent:
            match = SERVICE_KEY.fullmatch(stripped)
            if not match:
                continue
            service_indent = indent
            in_service = match.group(2).strip() == service
            child_indent = None
            continue
        if not in_service:
            continue
        if child_indent is None:
            child_indent = indent
        if indent == child_indent and re.match(r'image\s*:', stripped):
            comment = re.search(r'\s+#.*$', re.sub(r'"[^"]*"|\'[^\']*\'', lambda m: '_' * len(m.group(0)), body))
            tail = body[comment.start():] if comment else ''
            lines[index] = ' ' * indent + 'image: ' + json.dumps(image) + tail + line[len(body):]
            result = ''.join(lines)
            document = yaml.safe_load(result) or {}
            if ((document.get('services') or {}).get(service) or {}).get('image') == image:
                return result
            break
    raise ValueError(f"无法在 Compose 文件中定位服务 {service} 的 image 行，请改为块样式配置")


def update_image_in_compose(compose_file, new_image):
    """
    将 compose 文件中与 new_image 同仓库（忽略 tag）的服务镜像更新为 new_image。
    返回被更新的服务名列表。

    只改匹配服务的 image 这一行，其余内容一个字节不动；有一个匹配服务定位不到 image 行
    （流样式、合并键继承等）就整体拒绝、不写文件，不退回整份重新序列化。
    """
    text = read_compose_file(compose_file)
    content = yaml.safe_load(text) or {}

    new_repo = new_image.rsplit(':', 1)[0]
    updated = []
    expected = copy.deepcopy(content)

    for svc_name, svc_cfg in (content.get('services') or {}).items():
        if not isinstance(svc_cfg, dict):
            continue
        current_image = svc_cfg.get('image', '')
        if isinstance(current_image, str) and current_image.rsplit(':', 1)[0] == new_repo:
            text = replace_service_image(text, svc_name, new_image)
            expected['services'][svc_name]['image'] = new_image
            updated.append(svc_name)
            logger.info(f"Updated service '{svc_name}': {current_image} -> {new_image}")

    if updated:
        # 逐行替换后整份再解析一遍：除了这几个 image，解析结果必须和原文件一致
        if (yaml.safe_load(text) or {}) != expected:
            raise ValueError('改写 Compose 镜像后其他配置发生了变化，已拒绝写入')
        restore_compose_file(compose_file, text)

    return updated


ONE_OFF_LABEL = 'com.docker.compose.oneoff'
# docker inspect 一次取启动时间和一次性容器标签；label 不存在时 index 返回空串
_INSPECT_FORMAT = '{{.State.StartedAt}}|{{index .Config.Labels "' + ONE_OFF_LABEL + '"}}'


def _one_off_from_labels(entry):
    """按 compose ps 输出里的 Labels 判断是否 `docker compose run` 起的一次性容器（label oneoff=True）。
    没有 Labels 字段（compose 2.21 之前的 JSON 数组输出就没有）返回 None，由 docker inspect 的结果补判。"""
    labels = entry.get('Labels')
    if isinstance(labels, dict):
        return str(labels.get(ONE_OFF_LABEL, '')).lower() == 'true'
    if isinstance(labels, str):
        return any(part.strip().lower() == f'{ONE_OFF_LABEL}=true' for part in labels.split(','))
    return None


def _inspect_container(container_id):
    """返回 (startedAt, oneOff)。从未启动过的容器（created）StartedAt 是 Go 零值 0001-01-01，
    当成没有启动时间上报，否则 hub 会把它当作「比上次更旧的上报」丢掉。inspect 失败两项都是 None。"""
    try:
        result = subprocess.run(
            ['docker', 'inspect', '--format', _INSPECT_FORMAT, container_id],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None, None
    if result.returncode != 0:
        return None, None
    started_at, _, one_off = result.stdout.strip().partition('|')
    started_at = started_at.strip()
    if not started_at or started_at.startswith('0001-01-01'):
        started_at = None
    return started_at, one_off.strip().lower() == 'true'


def collect_service_statuses(compose_dir):
    """
    在 compose_dir 下采集所有 service 的真实运行状态：
    docker compose ps --all --format json（每行一个 service 的 JSON，NDJSON）+
    对每个拿到的容器 ID 补一次 docker inspect 拿精确 StartedAt（compose ps 本身不给机器可比较的时间戳，
    只有 RunningFor/Status 这类人话字符串）。
    必须带 --all：compose v2 的 ps 默认只列运行中的容器，崩溃退出的实例会整个消失，
    hub 既收不到 exited 也就发不出劣化告警，只会在 6 分钟后误报「agent 可能离线」。
    compose ps 本身失败（目录没有 compose 文件/容器未创建）返回空列表，调用方按"这轮跳过"处理。
    单个容器 inspect 失败只影响该 service 的 startedAt（置 None），不影响其它字段、不影响其它 service。
    运行中的容器排在前面：旧版 hub 直接拿第一个当主容器，--all 带出的已退出初始化容器排到前面会被误认。
    """
    cmd = _get_compose_cmd() + ['ps', '--all', '--format', 'json']
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=compose_dir)
    except Exception:
        return []
    if result.returncode != 0:
        return []

    entries = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        # compose 2.21 之前把整份结果输出成一行 JSON 数组，之后是 NDJSON；两种都认
        entries.extend(parsed if isinstance(parsed, list) else [parsed])

    services = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        one_off = _one_off_from_labels(entry)
        if one_off:
            continue
        container_id = entry.get('ID')
        started_at = None
        if container_id:
            started_at, inspected_one_off = _inspect_container(container_id)
            if one_off is None and inspected_one_off:
                continue
        services.append(
            {
                'name': entry.get('Service'),
                'image': entry.get('Image'),
                'state': entry.get('State'),
                'startedAt': started_at,
                'containerName': entry.get('Name'),
                'containerId': container_id,
                'raw': entry,
            }
        )
    services.sort(key=lambda service: 0 if service['state'] == 'running' else 1)  # 稳定排序，同类保持原顺序
    return services


def resolve_container_port_mapping(compose_file, container_port='80'):
    """
    在 compose 文件里查找映射到指定容器端口的宿主机端口。
    只支持短语法字符串（'host:container'、'ip:host:container'，可带 '/tcp' 后缀），
    找不到匹配、格式不支持（如只写容器端口没写宿主机端口）时返回 None——
    调用方必须把 None 当失败处理，不允许静默降级。
    """
    content = yaml.safe_load(read_compose_file(compose_file)) or {}
    for svc_cfg in (content.get('services') or {}).values():
        if not isinstance(svc_cfg, dict):
            continue
        for entry in (svc_cfg.get('ports') or []):
            if not isinstance(entry, str):
                continue
            parts = entry.split(':')
            if len(parts) < 2:
                continue
            container_part = parts[-1].split('/')[0]
            if container_part != str(container_port):
                continue
            try:
                return int(parts[-2])
            except ValueError:
                continue
    return None


def run_compose(project_dir, args):
    """在 project_dir 下执行 compose 子命令，返回 (success: bool, output: str)。"""
    cmd = _get_compose_cmd() + args
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=project_dir)
    return result.returncode == 0, result.stdout + result.stderr


def open_compose_process(project_dir, args):
    """在 project_dir 下启动 compose 子进程，适合持续输出场景。"""
    cmd = _get_compose_cmd() + args
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=project_dir,
        bufsize=1,
    )
