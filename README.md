# 绿邮X系列内网群控系统 v5.2

一个用于管理局域网内智能设备（如短信转发设备）的 Web 管理平台，支持设备扫描、短信发送、电话拨号、批量配置、OTA 升级等功能。

## 功能特性

- **设备管理**：自动扫描局域网设备，支持别名、分组管理
- **短信发送**：通过设备 SIM 卡发送短信，支持频率限制
- **电话拨号**：支持 TTS 语音播报，频率控制防止滥用
- **批量配置**：WiFi、SIM 卡号、消息转发、正则替换、预设模板
- **批量 OTA**：检查固件版本，一键升级多台设备
- **安全增强**：Cookie + CSRF 认证、登录限流、请求频率控制、审计日志、SSRF 防护
- **性能优化**：异步扫描任务、HTTP 连接池、共享线程池、本地网络缓存
- **双栈支持**：同时支持 IPv4 和 IPv6 访问
- **监控**：Prometheus `/metrics` 端点，支持 HTTP 延迟/计数/状态码采集

## 技术栈

- **后端**：FastAPI + SQLAlchemy + SQLite + httpx
- **前端**：Vue 3 + Vite + Pinia + axios
- **部署**：systemd / Docker

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

# 更改密码
lvyou pass

# 更改端口
lvyou port

# 查看配置
lvyou config
```

### 方式二：Docker 部署

```bash
# 创建环境变量文件
echo "BMUIPASS=your_strong_password" > .env
# 可选：不设置 BMUIPASS 则默认使用 admin

# 启动
docker compose up -d

# 查看日志
docker compose logs -f

# 停止
docker compose down
```

#### Docker 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SERVER_PORT` | 8000 | 服务端口 |
| `BMUIUSER` | admin | UI 用户名 |
| `BMUIPASS` | admin | UI 登录密码（默认 admin，建议首次登录后修改） |
| `BMDEVUSER` | admin | 设备用户名 |
| `BMDEVPASS` | admin | 设备密码 |
| `BMHTTPTIMEOUT` | 5.0 | HTTP 请求超时（秒） |
| `BMSCANCONCURRENCY` | 64 | 扫描并发数 |
| `BMSMSRATELIMIT` | 10 | 短信频率限制（次/分钟） |
| `BMLOGINRATELIMIT` | 5 | 登录频率限制（次/分钟） |
| `BMTOKENTTL` | 7200 | Token 有效期（秒） |
| `BMALLOWORIGINS` | （空） | CORS 允许的源 |

### 方式三：手动部署

```bash
# 安装依赖
cd backend
pip install -r requirements.txt

# 构建前端
cd ../frontend
pnpm install
pnpm run build

# 启动服务（同时监听 IPv4 和 IPv6）
cd ..
./start.sh
# 可选：设置 BMUIPASS=your_password 自定义密码，不设置则默认 admin
# 可选：./start.sh 9000 指定端口
```

## 项目结构

```
├── backend/
│   ├── main.py          # FastAPI 应用入口、中间件、生命周期
│   ├── config.py        # 环境变量配置
│   ├── db.py            # SQLAlchemy 模型、Token CRUD
│   ├── security.py      # SSRF 防护、IP 校验、网络工具
│   ├── audit.py         # 结构化 JSON 审计日志
│   ├── ratelimit.py     # SQLite 持久化限流器
│   ├── http_client.py   # httpx 连接池 + 线程池管理
│   ├── device_client.py # 设备通信（token/配置/OTA/WiFi）
│   ├── routes/
│   │   ├── auth.py      # 登录/登出/健康检查
│   │   ├── devices.py   # 设备 CRUD/SMS/WiFi/SIM/Forward/OTA
│   │   ├── scan.py      # 网络扫描
│   │   └── config.py    # 批量配置读写
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.vue          # 主应用（布局组装层）
│   │   ├── api/             # axios 封装
│   │   ├── components/      # Vue 组件
│   │   ├── composables/     # 组合式函数（业务逻辑）
│   │   ├── stores/          # Pinia 状态管理
│   │   ├── styles/          # 样式文件
│   │   └── utils/           # 工具函数
│   └── package.json
├── docker-entrypoint.sh # Docker 入口脚本
├── start.sh             # 手动部署启动脚本（v4+v6 双栈）
├── Dockerfile
├── docker-compose.yml
├── install.sh
├── CHANGELOG.md
└── README.md
```

## 短信回执与推送（必读）

面板能显示「板子已发出 / 已提交运营商 / 发送失败」，但前提是**开发板把异步回执推给面板**。
只在面板里点发送是不够的：`sendsms` 的 HTTP 响应只有 `type=401 / "sending..."`，
真正的结果要靠开发板后续推送的 `402`（板子已处理）和 `502 / 503`（运营商回执）消息。

每台开发板需要做两件事，都在板子的 `/ctrl` 上执行（`<TOKEN>` 是板子管理员 Token）：

1. 把上报接口指向面板的 `/webhook`，带上面板生成的 Webhook Token：

   ```
   GET http://<板子IP>/ctrl?token=<TOKEN>&cmd=setinf
       &p1=<http://面板IP:端口/webhook?k=WEBHOOK_TOKEN>&p2=post&p3=json
   ```

   用 `cmd=askinf` 复查，`method` 应为 `1`、`ctype` 应为 `1`。

2. 打开 `402`、`503` 消息号上报（默认是关闭的，这是「发短信没反应」最常见的原因）：

   ```
   GET http://<板子IP>/ctrl?token=<TOKEN>&cmd=setallmsgcode&p1=402,503&p2=on&p3=replace
   ```

   用 `cmd=askallmsgcode` 复查，`en` 里应包含 `402`、`501`、`502`、`503`。

面板侧在「系统设置 → 消息推送（Bark）」里填完整的 Bark 地址即可；保存后只显示域名，
不会再回显完整链接。Webhook Token 在访问日志里会被掩码成 `k=***`。

设备列表里选中板子后点「接入推送」，面板会自动算出自己能通到板子的内网地址，
对每台设备执行上面两条命令，并回读 `askinf` / `askallmsgcode` 确认生效。放在
Docker 桥接网络里、自动算不出地址时，用 `BMPANELURL=http://<局域网IP>:<端口>` 指定。

## 安全说明

- **密码要求**：默认密码为 `admin`，启动时会打印安全警告，建议通过 `lvyou pass` 更换强密码
- **本地开发**：可设置 `BMINSECURE_DEFAULT_PASSWORD=1` 跳过密码检查
- **认证方式**：httpOnly Cookie + CSRF Token，防止 XSS 窃取
- **SSRF 防护**：设备 IP 必须在本机子网范围内
- **频率限制**：登录、短信、拨号、OTA 均有独立限流

## 更新日志

详见 [CHANGELOG.md](./CHANGELOG.md)

## 许可证

MIT License
