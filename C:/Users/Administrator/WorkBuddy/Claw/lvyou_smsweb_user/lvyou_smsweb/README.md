# 绿邮X系列内网群控系统 v3.4.0

一个用于管理局域网内智能设备（如短信转发设备）的 Web 管理平台，支持设备扫描、短信发送、电话拨号、批量配置等功能。

## 功能特性

- **设备管理**：自动扫描局域网设备，支持别名、分组管理
- **短信发送**：通过设备 SIM 卡发送短信，支持频率限制
- **电话拨号**：支持 TTS 语音播报，频率控制防止滥用
- **批量配置**：WiFi、SIM 卡号、消息转发等
- **多种推送**：支持 Bark、SMTP、企业微信、钉钉、飞书、Server酱、PushPlus、WxPusher 等
- **安全增强**：登录频率限制、请求频率控制、审计日志
- **性能优化**：异步扫描任务、HTTP 连接池、多线程并发
- **双栈支持**：同时支持 IPv4 和 IPv6 访问

## 安装方式

### 方式一：脚本安装（推荐）

适用于 Ubuntu / Debian 系统。

```bash
# 下载项目
git clone https://github.com/lovexme/lvyou_smsweb.git
cd lvyou_smsweb

# 执行安装
sudo bash install.sh install
```

安装过程中会提示输入：
- 服务端口（默认 8000）
- UI 登录密码（至少 6 位）

安装完成后访问：
- IPv4: `http://192.168.x.x:8000/`
- IPv6: `http://[您的IPv6地址]:8000/`

#### 脚本命令说明

```bash
# 安装
sudo bash install.sh install

# 查看状态
sudo bash install.sh status

# 重启服务
sudo bash install.sh restart

# 查看日志
sudo bash install.sh logs
```

#### 安装参数（可选）

```bash
sudo bash install.sh install \
  --port 8000 \
  --scan-user admin \
  --scan-pass admin \
  --ui-pass 123456
```

| 参数 | 说明 |
|------|------|
| `--port` | 服务端口，默认 8000 |
| `--scan-user` | 设备扫描用户名，默认 admin |
| `--scan-pass` | 设备扫描密码，默认 admin |
| `--ui-pass` | UI 登录密码 |

### 方式二：Docker 安装

#### 快速启动

```bash
# 拉取镜像
docker pull lovexme/lvyou-smsweb:latest

# 运行容器（必须使用 host 网络模式）
docker run -d --net=host \
  --restart unless-stopped \
  -e BMUIPASS=登录密码 \
  -v ./data:/opt/board-manager/data \
  --name lvyou-smsweb \
  lovexme/lvyou-smsweb:latest
```

> **重要**：必须使用 `--net=host` 网络模式，否则无法扫描局域网设备。

#### 自定义端口

```bash
docker run -d --net=host \
  --restart unless-stopped \
  -e BMUIPASS=登录密码 \
  -e SERVER_PORT=8000 \
  -v ./data:/opt/board-manager/data \
  --name lvyou-smsweb \
  lovexme/lvyou-smsweb:latest
```

#### Docker Compose 示例

项目已包含完整的 `docker-compose.yml` 配置文件，可直接使用：

```bash
# 复制示例配置（如果需要自定义）
cp docker-compose.yml docker-compose.prod.yml

# 编辑环境变量
vi docker-compose.prod.yml

# 启动服务
docker-compose -f docker-compose.prod.yml up -d
```

或直接使用项目提供的配置：

```yaml
# 详见 docker-compose.yml 文件
# 包含完整的 v3.4.0 环境变量配置
```

启动命令：

```bash
docker compose up -d
```

#### 环境变量说明

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SERVER_PORT` | 8000 | 服务端口 |
| `BMUIUSER` | admin | UI 登录用户名 |
| `BMUIPASS` | admin | UI 登录密码 |
| `BMDEVUSER` | admin | 设备扫描用户名 |
| `BMDEVPASS` | admin | 设备扫描密码 |
| `BMHTTPTIMEOUT` | 5.0 | HTTP 超时秒数 |
| `BMSCANCONCURRENCY` | 64 | 扫描并发数 |
| `BMTCPCONCURRENCY` | 128 | TCP 端口探测并发数 |
| `BMSCANRETRIES` | 3 | 扫描重试次数 |
| `BMSCANTTL` | 3600 | 扫描结果存活时间（秒） |
| `BMSMSRATELIMIT` | 10 | 短信发送频率限制（次/分钟） |
| `BMDIALRATELIMIT` | 5 | 电话拨号频率限制（次/分钟） |
| `BMLOGINRATELIMIT` | 5 | 登录尝试频率限制（次/分钟） |
| `BMSMSMAXLEN` | 500 | 短信内容最大长度 |
| `BMALLOWORIGINS` | 空 | CORS 允许的域名，逗号分隔 |

#### 本地构建镜像

项目提供构建脚本，支持多平台构建：

```bash
# 查看构建帮助
./build-docker.sh --help

# 本地构建
./build-docker.sh

# 构建指定版本
./build-docker.sh -t v3.4.0

# 多平台构建并推送到 Docker Hub（需要登录）
./build-docker.sh -p --platform linux/amd64,linux/arm64
```

构建脚本功能：
- 支持多平台构建 (amd64, arm64)
- 自动标签管理
- 健康检查集成
- Docker Hub 推送支持

#### 重要配置说明

**1. 网络配置（关键）**
- **必须使用 `--net=host` 网络模式**：容器需要直接访问主机网络接口才能扫描局域网设备
- **不能使用 bridge 网络**：bridge网络会隔离容器，导致无法发现局域网设备
- **Docker Compose 配置**：确保 `network_mode: host` 设置

**2. 开机自启配置**
容器使用 `--restart unless-stopped` 策略后，还需要确保 Docker 服务本身开机启动：

```bash
# Ubuntu/Debian 系统
sudo systemctl enable docker

# CentOS/RHEL 系统
sudo systemctl enable docker

# 检查 Docker 服务状态
sudo systemctl status docker

# 设置 Docker 服务开机自启
sudo systemctl enable --now docker
```

**3. 完整运行示例**
```bash
# 完整命令，包含网络模式和重启策略
docker run -d --net=host \
  --restart unless-stopped \
  -e BMUIPASS=your_password \
  -e BMSMSRATELIMIT=10 \
  -e BMDIALRATELIMIT=5 \
  -e BMLOGINRATELIMIT=5 \
  -v ./data:/opt/board-manager/data \
  --name lvyou-smsweb \
  lovexme/lvyou-smsweb:latest
```

**4. 验证网络连接**
启动后验证容器能否访问局域网：
```bash
# 进入容器
docker exec -it lvyou-smsweb bash

# 在容器内测试网络
ping 192.168.1.1  # 替换为你的网关地址
ip addr show      # 查看网络接口
```

## 卸载

### 脚本安装卸载

```bash
cd lvyou_smsweb
sudo bash uninstall.sh
```

### Docker 安装卸载

```bash
docker rm -f lvyou-smsweb
docker rmi lovexme/lvyou-smsweb:latest
rm -rf ./data
```

## 常见问题

### 出现 `{"detail":"UI not built"}`

前端静态文件未正确部署，手动重新构建：

```bash
cd /root/lvyou_smsweb/frontend
npm run build
sudo mkdir -p /opt/board-manager/static
sudo cp -a dist/. /opt/board-manager/static/
sudo systemctl restart board-manager-v4 board-manager-v6
```

### 查看服务状态

```bash
sudo systemctl status board-manager-v4 --no-pager
sudo systemctl status board-manager-v6 --no-pager
```

### 查看配置文件

```bash
cat /etc/board-manager.conf
```

### Docker 容器无法启动

1. 确保使用了 `--net=host` 网络模式
2. 检查端口是否被占用：`ss -tlnp | grep 8000`
3. 查看容器日志：`docker logs lvyou-smsweb`

## 版本更新 v3.4.0

### 安全性增强
- **登录频率限制**：防止暴力破解，默认5次/分钟
- **请求频率控制**：短信发送和电话拨号频率限制
- **审计日志**：记录关键操作（登录、扫描、短信、拨号）
- **强化验证**：手机号格式验证、短信内容长度限制

### 性能优化
- **HTTP客户端升级**：从 `requests` 迁移到 `httpx`，支持异步和连接池
- **异步扫描任务**：扫描改为后台任务，支持实时状态查询
- **连接池管理**：优化HTTP连接复用，减少资源消耗
- **并发配置优化**：提高扫描和探测的并发数

### 功能完善
- **扫描状态查询**：新增 `/api/scan/status/{scan_id}` 接口
- **增强错误处理**：统一的异常处理机制和错误ID追踪
- **数据验证强化**：使用 Pydantic 模型进行输入验证
- **凭据安全**：扫描凭据通过 POST Body 传递，避免URL暴露

### 环境变量新增
- `BMSMSRATELIMIT` - 短信发送频率限制
- `BMDIALRATELIMIT` - 电话拨号频率限制
- `BMLOGINRATELIMIT` - 登录尝试频率限制
- `BMSCANTTL` - 扫描结果存活时间
- `BMSMSMAXLEN` - 短信内容最大长度

## 技术栈

- **后端**：Python + FastAPI + SQLAlchemy + httpx
- **前端**：Vue 3 + Vite
- **部署**：systemd / Docker

## 许可证

MIT License
