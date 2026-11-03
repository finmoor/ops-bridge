import os
import posixpath
import sys
import logging
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
load_dotenv()


CHINA_TZ = timezone(timedelta(hours=8))


class ChinaTimeFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created, CHINA_TZ)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat(timespec='seconds')

WS_URL             = os.getenv('WS_URL', '')
# 必须等于中枢「服务器」页的服务器编码；不退回主机名（容器主机名是随机串，中枢不认识，只会一直鉴权失败）
AGENT_ID           = (os.getenv('AGENT_ID') or '').strip()
AGENT_KEY          = os.getenv('AGENT_KEY', '')
# 连接鉴权 key 的传递方式：auto（默认）先走 X-Agent-Key 请求头，被只认 URL 参数的旧中枢拒绝后自动改用 ?key=；
# header / query 固定其一。请求头方式下 key 不出现在 URL 里，不会被网关/nginx 的访问日志记下来。
AGENT_AUTH_MODE    = os.getenv('AGENT_AUTH_MODE', 'auto').strip().lower()
if AGENT_AUTH_MODE not in ('auto', 'header', 'query'):
    AGENT_AUTH_MODE = 'auto'
RECONNECT_DELAY    = int(os.getenv('RECONNECT_DELAY', '5'))
HEARTBEAT_INTERVAL = int(os.getenv('HEARTBEAT_INTERVAL', '30'))
STATUS_REPORT_INTERVAL = int(os.getenv('STATUS_REPORT_INTERVAL', '120'))
# restart/update 结束后短期跟踪采集：worker 未通知 Agent（直连拉清单、旧镜像）时也能及时上报插件同步结果
PLUGIN_FOLLOW_UP_INTERVAL = int(os.getenv('PLUGIN_FOLLOW_UP_INTERVAL', '10'))
PLUGIN_FOLLOW_UP_TIMEOUT = int(os.getenv('PLUGIN_FOLLOW_UP_TIMEOUT', '300'))
HEALTH_HOST        = os.getenv('HEALTH_HOST', '0.0.0.0')
HEALTH_PORT        = int(os.getenv('HEALTH_PORT', '18081'))
# agent 自己也是容器，跟被管的业务容器各在独立的 bridge 网络命名空间，127.0.0.1 谁也连不到谁；
# host.docker.internal 需要 compose 里配 extra_hosts: host-gateway 才解析得到，见 docker-compose.yml
APP_HOST           = os.getenv('APP_HOST', 'host.docker.internal')
# admin(worker) 调本机 agent 各端点用的共享 secret（odk init 每机随机生成、渲染进本 .env 与 admin 请求头）。
# 需要它的本机端点（请求头 X-Agent-Secret）：/queryPlugin、/pluginSyncChanged、/verifyOpsCode、/envConfig、/instanceSnapshot，
# 与 start_health_server 的启动告警列的一致。防同网段其它主机裸调这些端点；未配置（留空）时它们一律回 403，不会放行。
AGENT_LOCAL_SECRET = os.getenv('AGENT_LOCAL_SECRET', '')
# hub 的 HTTP 基址（日志归档上传用）。留空则由 WS_URL 推导：ws→http、wss→https、去掉 /ws/agent 及其后内容。
HUB_HTTP_URL       = os.getenv('HUB_HTTP_URL', '').strip().rstrip('/')
# 被管项目根目录，也是 compose 项目发现的扫描根。docker-compose.yml 把宿主机 MANAGED_PROJECTS_ROOT 按同一路径挂进容器，
# 并把它传成 PROJECTS_ROOT：中枢下发的目录是宿主机路径，业务 compose 的相对挂载也按宿主机路径解析，容器内外必须一致
PROJECTS_ROOT      = (os.getenv('PROJECTS_ROOT') or '').strip() or '/data'
# result 出站队列账本：默认放在被管根目录下（挂载卷内），容器重建、镜像升级不丢未确认回执
OUTBOX_PATH        = os.getenv('OUTBOX_PATH') or posixpath.join(PROJECTS_ROOT, '.service-agent', 'outbox.json')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
for handler in logging.getLogger().handlers:
    handler.setFormatter(ChinaTimeFormatter('%(asctime)s - %(levelname)s - %(message)s'))

if not WS_URL:
    sys.exit("ERROR: WS_URL is not set. Example: ws://192.168.1.100:8080/ws/agent")
if not AGENT_ID:
    sys.exit("ERROR: AGENT_ID is not set. 填交付中枢「服务器」页上这台服务器的服务器编码（新建服务器后弹出的 AGENT_ID）。")
if not AGENT_KEY:
    sys.exit("ERROR: AGENT_KEY is not set.")
