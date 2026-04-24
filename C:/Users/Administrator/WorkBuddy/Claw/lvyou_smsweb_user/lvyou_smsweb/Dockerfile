# ========================================
# 绿邮X系列内网群控系统 Docker 镜像
# ========================================
#
# 重要说明：
# 本应用需要扫描局域网设备，必须使用 host 网络模式运行
#
# 运行命令：
#   docker run -d --net=host \
#     --restart unless-stopped \
#     -e BMUIPASS=your_password \
#     -v ./data:/opt/board-manager/data \
#     --name lvyou-smsweb \
#     lovexme/lvyou-smsweb:latest
#
# 自定义端口：
#   docker run -d --net=host \
#     --restart unless-stopped \
#     -e BMUIPASS=your_password \
#     -e SERVER_PORT=9000 \
#     -v ./data:/opt/board-manager/data \
#     --name lvyou-smsweb \
#     lovexme/lvyou-smsweb:latest
#
# Docker Compose 示例：
#   services:
#     lvyou-smsweb:
#       image: lovexme/lvyou-smsweb:latest
#       network_mode: host
#       environment:
#         - BMUIPASS=your_password
#         - SERVER_PORT=9000
#         - BMSCANCONCURRENCY=64
#         - BMSMSRATELIMIT=10
#         - BMDIALRATELIMIT=5
#         - BMLOGINRATELIMIT=5
#       volumes:
#         - ./data:/opt/board-manager/data
#
# 环境变量说明：
#   SERVER_PORT        - 服务端口 (默认: 8000)
#   BMUIUSER           - UI 登录用户名 (默认: admin)
#   BMUIPASS           - UI 登录密码 (默认: admin)
#   BMDEVUSER          - 设备扫描用户名 (默认: admin)
#   BMDEVPASS          - 设备扫描密码 (默认: admin)
#   BMHTTPTIMEOUT      - HTTP 超时秒数 (默认: 5.0)
#   BMSCANCONCURRENCY  - 扫描并发数 (默认: 64)
#   BMTCPCONCURRENCY   - TCP 端口探测并发数 (默认: 128)
#   BMSCANRETRIES      - 扫描重试次数 (默认: 3)
#   BMSCANTTL          - 扫描结果存活时间（秒）(默认: 3600)
#   BMSMSRATELIMIT     - 短信发送频率限制（次/分钟）(默认: 10)
#   BMDIALRATELIMIT    - 电话拨号频率限制（次/分钟）(默认: 5)
#   BMLOGINRATELIMIT   - 登录尝试频率限制（次/分钟）(默认: 5)
#   BMSMSMAXLEN        - 短信内容最大长度 (默认: 500)
#   BMALLOWORIGINS     - CORS 允许的域名，逗号分隔 (默认: 空)
#
# 重要提示：
#   1. 必须使用 --net=host 网络模式，否则无法扫描局域网设备
#   2. 建议使用 --restart unless-stopped 确保容器自动重启
#   3. 确保 Docker 服务开机自启：sudo systemctl enable docker
#   4. 数据持久化：使用 -v 挂载数据目录防止数据丢失
# ========================================

FROM python:3.11-slim

ARG VERSION="3.4.0"

LABEL maintainer="lovexme"
LABEL description="绿邮X系列内网群控系统"
LABEL version="${VERSION}"

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    iproute2 \
    iputils-ping \
    procps \
    && rm -rf /var/lib/apt/lists/*

# 安装 Node.js
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 复制后端依赖并安装
COPY backend/requirements.txt /app/backend/
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# 复制前端依赖（FIX(P1#14): 使用 pnpm + pnpm-lock.yaml，保证与仓库一致的依赖树）
COPY frontend/package.json frontend/pnpm-lock.yaml /app/frontend/

# 安装前端依赖并构建
WORKDIR /app/frontend
RUN npm install -g pnpm@9 \
    && pnpm install --frozen-lockfile
COPY frontend/ /app/frontend/
RUN pnpm run build

# 回到应用目录
WORKDIR /app

# 复制后端代码
COPY backend/ /app/backend/

# 创建数据目录和静态文件目录
RUN mkdir -p /opt/board-manager/data /opt/board-manager/static \
    && cp -a /app/frontend/dist/. /opt/board-manager/static/

# 设置环境变量
ENV BMDB=/opt/board-manager/data/data.db
ENV BMSTATIC=/opt/board-manager/static
ENV BMDEVUSER=admin
ENV BMDEVPASS=admin
ENV BMUIUSER=admin
ENV BMUIPASS=admin
ENV BMHTTPTIMEOUT=5.0
ENV BMSCANCONCURRENCY=64
ENV BMTCPCONCURRENCY=128
ENV BMSCANRETRIES=3
ENV BMSCANTTL=3600
ENV BMSMSRATELIMIT=10
ENV BMDIALRATELIMIT=5
ENV BMLOGINRATELIMIT=5
ENV BMSMSMAXLEN=500
ENV BMALLOWORIGINS=""
ENV SERVER_PORT=8000

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${SERVER_PORT}/api/health || exit 1

# 启动命令（支持自定义端口，同时监听 IPv4 和 IPv6）
CMD ["sh", "-c", "python -m uvicorn backend.main:app --host 0.0.0.0 --port ${SERVER_PORT} & python -m uvicorn backend.main:app --host :: --port ${SERVER_PORT} & wait"]
