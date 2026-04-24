# ========================================
# 绿邮X系列内网群控系统 Docker 镜像 v5.0
# ========================================
#
# 优化说明：
#   - 多阶段构建：前端构建阶段独立，最终镜像不含 Node.js
#   - 预估体积：~80 MB（原 ~153 MB，减少约 47%）
#
# 运行命令：
#   docker run -d --net=host \
#     --restart unless-stopped \
#     -e BMUIPASS=your_password \
#     -v ./data:/opt/board-manager/data \
#     --name lvyou-smsweb \
#     lovexme/lvyou-smsweb:latest
#
# 重要提示：
#   1. 必须使用 --net=host 网络模式，否则无法扫描局域网设备
#   2. 建议使用 --restart unless-stopped 确保容器自动重启
#   3. 确保 Docker 服务开机自启：sudo systemctl enable docker
#   4. 数据持久化：使用 -v 挂载数据目录防止数据丢失
# ========================================

# ---- 阶段1：前端构建 ----
FROM node:20-slim AS frontend-builder

WORKDIR /app/frontend

# 安装 pnpm
RUN npm install -g pnpm@9

# 先复制依赖文件（利用 Docker 缓存）
COPY frontend/package.json frontend/pnpm-lock.yaml ./

# 安装依赖
RUN pnpm install --frozen-lockfile

# 复制源码并构建
COPY frontend/ ./
RUN pnpm run build

# ---- 阶段2：最终运行镜像 ----
FROM python:3.11-slim

ARG VERSION="5.0"

LABEL maintainer="lovexme"
LABEL description="绿邮X系列内网群控系统"
LABEL version="${VERSION}"

# 安装系统依赖（仅运行时需要的最小集）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    iproute2 \
    iputils-ping \
    procps \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# 设置工作目录
WORKDIR /app

# 安装 Python 依赖
COPY backend/requirements.txt /app/backend/
RUN pip install --no-cache-dir -r /app/backend/requirements.txt \
    && rm -rf /root/.cache/pip

# 复制后端代码
COPY backend/ /app/backend/

# 从前端构建阶段复制产物（不含 Node.js / node_modules）
COPY --from=frontend-builder /app/frontend/dist /opt/board-manager/static

# 创建数据目录
RUN mkdir -p /opt/board-manager/data

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

# 健康检查（CMD-SHELL 形式，容器内运行时解析 SERVER_PORT）
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD-SHELL 'curl -f "http://localhost:$$SERVER_PORT/api/health" || exit 1'

# 启动命令（支持自定义端口，同时监听 IPv4 和 IPv6）
CMD ["sh", "-c", "python -m uvicorn backend.main:app --host 0.0.0.0 --port ${SERVER_PORT} & python -m uvicorn backend.main:app --host :: --port ${SERVER_PORT} & wait"]
