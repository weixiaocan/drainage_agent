# 公开演示部署

公开 Demo 使用 `docker-compose.yml` 加 `docker-compose.demo.yml` 覆盖：开启演示模式，不映射主机端口，只接入反向代理所在的 Docker 网络，状态保存在 `drainage-demo-state` 卷。演示模式的限制见 ADR 0006。

## 前提

- 服务器已安装 Docker 和 Compose v2.24 以上（覆盖文件使用 `!reset` / `!override`）。
- 反向代理（如 Caddy）与应用位于同一 Docker 网络，并把域名转发到 `drainage-agent-demo:8000`。
- 项目目录下的 `.env` 只能由 root 读取（`chmod 600`），使用 LF 换行。

## `.env`

除模型密钥外，需要以下变量。`.env` 同时用于 Compose 变量替换和容器环境变量：

```env
COMPOSE_PROJECT_NAME=drainage-agent
DEMO_PROXY_NETWORK=<反向代理所在的网络名>
SANDBOX_IMAGE_DIGEST=<沙箱镜像 ID，见下文>
SANDBOX_CONTROLLER_TOKEN=<至少 32 位随机字符串>
DOCKER_GID=<宿主 Docker socket 的组 ID>
DRAINAGE_DEMO_REQUESTS_PER_MINUTE=3
DRAINAGE_DEMO_MAX_CONCURRENT_CHATS=2
DRAINAGE_DEMO_DAILY_CHATS_PER_VISITOR=20
DRAINAGE_DEMO_DAILY_CHATS_TOTAL=300
# 可选：构建时使用的 pip 镜像（国内服务器访问 PyPI 很慢时设置）
PIP_INDEX_URL=http://mirrors.tencentyun.com/pypi/simple
PIP_TRUSTED_HOST=mirrors.tencentyun.com
```

`COMPOSE_PROJECT_NAME` 必须与沙箱任务卷的前缀一致，控制器按 `<项目名>_sandbox-jobs` 挂载任务卷。

## 部署或升级

```bash
cd /opt/apps/drainage-agent
git pull --ff-only   # 服务器连不上 GitHub 时，可在本机 git bundle 后用 scp 传过去再 git fetch
set -a; . ./.env; set +a
docker build --build-arg PIP_INDEX_URL --build-arg PIP_TRUSTED_HOST \n  -f Dockerfile.sandbox -t drainage-python-sandbox:local .
# 镜像 ID 变化时更新 .env 中的 SANDBOX_IMAGE_DIGEST
docker image inspect drainage-python-sandbox:local --format '{{.Id}}'
docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d --build
```

首次生成令牌和组 ID：

```bash
openssl rand -hex 32
stat -c %g /var/run/docker.sock
```

## 验证

1. `docker compose -f docker-compose.yml -f docker-compose.demo.yml ps`：两个服务均为运行状态，应用为 healthy。
2. 通过域名访问 `/healthz`，返回 `{"status":"ok","demo_mode":true}`。
3. 在网页上提问一次分析问题；再请求一次需要 `run_python` 的统计（如“W1 流量的 95% 分位数”），审批后应在沙箱中执行并返回结果。

## 备份与回滚

升级前备份状态卷：

```bash
docker run --rm -v drainage-agent_drainage-demo-state:/data -v /opt/backups:/backup alpine:3.22 \
  tar -czf /backup/drainage-demo-state-$(date +%Y%m%d).tar.gz -C /data .
```

回滚时 `git checkout <上一版本提交>` 后重新执行 `up -d --build`；数据不兼容时停止服务，清空卷后解压备份。
