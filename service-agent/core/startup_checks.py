"""启动自检：被管项目根目录必须在容器内外是同一个绝对路径。

中枢下发的 dir 是宿主机路径，agent 以它为 cwd 执行 docker compose；业务 compose 里 ./logs 这类相对挂载
也由 Docker 按宿主机路径解析。容器内外路径不一致时，填宿主机路径找不到目录，用容器内路径又会让相对挂载
指到宿主机上错误的位置。这里只记错误日志、不退出：agent 照常连中枢，方便在服务器页看到它在线后再改配置。
"""
import logging
import os
from pathlib import PurePosixPath

from config import PROJECTS_ROOT
from services.agent_upgrade import own_container

logger = logging.getLogger(__name__)

FIX_HINT = ('请把 .env 里的 MANAGED_PROJECTS_ROOT 设成宿主机上的被管根目录，并按同一路径挂进容器'
            '（本仓 docker-compose.yml 的写法：${MANAGED_PROJECTS_ROOT}:${MANAGED_PROJECTS_ROOT}，'
            '同时传 PROJECTS_ROOT=${MANAGED_PROJECTS_ROOT}）')


def check_projects_root(root=None):
    """返回 ok / missing / unmounted / mismatch / unknown（不在容器里或查不到自己的容器）。"""
    root = PROJECTS_ROOT if root is None else root
    if not os.path.isdir(root):
        logger.error('PROJECTS_ROOT=%s 在容器里不存在：被管项目目录没有挂进来，发现项目和执行命令都会失败。%s',
                     root, FIX_HINT)
        return 'missing'
    try:
        info = own_container()
    except Exception:
        return 'unknown'
    target = PurePosixPath(root)
    best = None
    for mount in info.get('Mounts') or []:
        if mount.get('Type') != 'bind' or not mount.get('Destination') or not mount.get('Source'):
            continue
        destination = PurePosixPath(mount['Destination'])
        if target.is_relative_to(destination) and (best is None or len(destination.parts) > len(best[0].parts)):
            best = (destination, PurePosixPath(mount['Source']))
    if best is None:
        logger.error('PROJECTS_ROOT=%s 不在任何宿主机挂载里，被管项目目录没有挂进来。%s', root, FIX_HINT)
        return 'unmounted'
    host = best[1] / target.relative_to(best[0])
    if host != target:
        logger.error('PROJECTS_ROOT=%s 在宿主机上是 %s，容器内外路径不一致：中枢下发的宿主机路径在容器里找不到，'
                     '业务 compose 的相对挂载也会指到宿主机上错误的位置。%s', root, host, FIX_HINT)
        return 'mismatch'
    return 'ok'
