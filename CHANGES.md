# 修复清单（基线：lvyou_smsweb_3 zip）

排除项：弱默认凭据 / 明文密码比较 / `BMUIPASS=admin` 默认值 — 按要求未改动。

## 必修级（升级 / 部署阻断问题）

| 编号 | 问题 | 修复点 |
|-----|------|--------|
| P0#1 | Docker `HEALTHCHECK` 命中需要 Bearer 的 `/api/health` → 401 | `backend/main.py` 把 `/api/health` 加入 `_PUBLIC_PATHS`；Dockerfile/compose 无需 token 即可通过 |
| N2 | v3 新增 `sim1signal/sim2signal` 但未加迁移，老库升级直接 `OperationalError: no such column` | `_run_migrations()` 改为数据驱动的幂等 ALTER TABLE 列表，覆盖 `token`、`sim1signal`、`sim2signal`、`firmware_version` |
| N1 | `listdevices` 每次 `GET /api/devices` 对所有设备发同步 HTTP，卡 50 秒 + SSRF 放大器 | 恢复为纯 DB 读；首页实时刷新交回前端按需调用 `/detail` |
| N3 | OTA `chkNewVer` 把 `curVer` 写进 `devId`，多台同版本设备互相覆盖 | 新增 `firmware_version` 列；OTA 只更新该列，`devId` 永远指向设备稳定标识 |
| N4 | 批量 OTA 在 ThreadPoolExecutor 里并发共用一个 `db: Session`，SQLAlchemy Session 非线程安全 | `check_ota_task` / `upgrade_ota_task` 改为只接收 `device_id`，每个 worker 各自 `SessionLocal()` |

## 新版本（lvyou_smsweb_3）其它问题

| 编号 | 问题 | 修复点 |
|-----|------|--------|
| N5 | OTA 批量接口无限流、无数量上限，登录用户可让内网设备并发重启 | 新增 `_ota_limiter`（默认 4 次/60 秒/IP）+ `OTA_BATCH_MAX=64` 上限 + audit 日志 |
| N6 | `upgrade_ota_task` 重复调用 `chkNewVer`（每台 2 次外发），N 台设备 2N 次握手 | 合并为单次 `_ota_check`：拿到 `newVer` 后直接触发升级，无匹配/同版本直接返回 `已是最新版本` |
| N7 | WiFi 预览接口返回假数据 `"(待获取)"`，前端却强制要求点预览才能执行 | 预览改为真正并发调用 `get_wifi_info()` 拿回设备当前 SSID；前端不再把"已预览"作为"执行"按钮的解锁条件 |
| N8 | `scripts/lvyou pass` 用 `sed "s/.../${pass1}/"` 替换，密码含 `/ & " \ $` 会炸配置 | 改 awk 原子重写 + `mv` 替换；空配置时直接生成 `UIPASS=...` 行 |
| N9 | `lvyou port` 直接就地 `sed -i` 改 systemd unit，中断会留残缺配置 | 改 pass/unit 都先 `cp -a *.bak.<ts>` 备份，配置用临时文件原子替换，unit 仍用 sed 但正则限定 `--port [0-9]{1,5}` |
| N10 | （review 报告中的"中文乱码截断"项）实际 zip 里 `install.sh:254` / `uninstall.sh:78` 两处 `title` 是完整的 `"安装命令行工具"` / `"删除命令行工具"`，无需修改 | — 无需改动，保留说明 |

## 上一轮 v3.4.0 未修的 P0/P1

| 编号 | 问题 | 修复点 |
|-----|------|--------|
| P0#2 | `ACTIVE_TOKENS: Dict[str, ...]` 是进程内全局，v4/v6 两个 uvicorn 不共享，导致偶发 401 | 新增 `auth_tokens` 表；`_issue/_get/_delete_token` 全部走 SQLite；跨进程自然一致 |
| P0#3 | 所有对 `device.ip` 的外发 HTTP 没校验，可被当跳板打内网/169.254.169.254 | 新增 `_is_device_ip_allowed()`：必须 private、非 loopback/link-local/metadata，且落在本机 IPv4 接口网段内；所有出站前置 `_ensure_device_ip_allowed()` |
| P0#5 | `sh(["bash", "-lc", f"ip ... {iface} ..."])` 走 shell，有注入面 | 全部改 argv（`_run()`），接口名由正则提取，不再拼 shell 字符串 |
| P0#6 | `BMALLOWORIGINS="*"` + `allow_credentials=True` 组合是悄悄失效的 CSRF 脚坑 | 启动时显式 `RuntimeError`，强制用户要么列出明确 origins 要么保持空 |
| P1#7 | `ScanState` 只有 `to_dict` 拿锁，其它 `status/progress/results` 赋值全裸写 | 增加 `set_status/set_progress/set_counts/set_results/set_cidr`，全部在 `_lock` 下赋值 |
| P1#8 | `upsertdevice` 撞到 `ip` UNIQUE 时直接 `db.delete(other)` 会把一台无关设备抹掉 | 改为把旧行 `ip` 置 `__stale_<id>_<ts>` 释放唯一槽位；`flush()` 失败回滚并返回旧设备字典，不做破坏性删除 |
| P1#9 | 每个请求都 `httpx.Client()` 一次，TLS 握手 + 连接重开 | 全局单例 `_sync_client`（连接池），lifespan 管理；`getdevicedata`、`istargetdevice`、`fetch_device_token`、所有 batch 任务、OTA、dial 全部复用 |
| P1#10 | 每个批量接口 `concurrent.futures.ThreadPoolExecutor(...)` 新建再销毁 | 全局单例 `_shared_executor`（lifespan 创建/关闭）；扫描 worker、batch/wifi、batch/sim、batch/forward、OTA 全部使用 |
| P1#11 | `prewarm_neighbors` 一次性 `Popen` 1024 个 ping | 引入 `PREWARM_CONCURRENCY`（默认 64）线程 + `Semaphore` 限流；`subprocess.run(..., timeout=...)` 替代 `Popen.wait + kill` |
| P1#12 | `_active_scans` 无上限 / 无 TTL 清理 | lifespan 起 `_scan_cleanup_loop()` 每 60 秒跑 `_cleanup_old_scans()` + `_cleanup_expired_tokens()`；所有对 `_active_scans` 的读写过 `_active_scans_lock` |
| P1#13 | 登录限流只看 `request.client.host`，反代后全部并合成一个 key | 新增 `BMTRUSTEDPROXYHOPS`；`>=1` 时读 `X-Forwarded-For` 的倒数第 N 个条目，否则落回 socket 对端 |
| P1#14 | 仓库有 `pnpm-lock.yaml`，但 install.sh/Dockerfile 都执行 `npm install`（生成无锁依赖树） | `install.sh`：若缺 pnpm 自动 `npm i -g pnpm@9`，前端构建 `pnpm install --frozen-lockfile && pnpm run build`；Dockerfile 同步修改 |
| P1#15 | `docker-compose.yml` healthcheck `["CMD","curl","-f","http://localhost:${SERVER_PORT}/api/health"]`，`${SERVER_PORT}` 在 compose parse 时展开到宿主环境，常为空 | 改 `CMD-SHELL` 形式：`curl -f "http://localhost:$$SERVER_PORT/api/health"`，由容器 shell 运行时解析，与 Dockerfile 的 `ENV SERVER_PORT=8000` 及 `-e SERVER_PORT=...` 一致 |
| P1#16 | `@app.exception_handler(Exception)` 把 `HTTPException`（认证/校验错误）一并吃掉翻成 500 | 在 handler 里 `isinstance(exc, HTTPException)` 就 `raise`，交回 Starlette 默认处理 |
| P1#17 | `method="99"` 字面量散落代码 | 提取常量 `FORWARD_METHOD_BASIC = "99"`，`api_batch_forward` 使用 |

## 附带加固

- `_check_ota_batch_allowed` 对每次批量 check/upgrade 都做 IP + 数量校验 + audit 日志（`ota_upgrade` action=ip+count）。
- `wifi_task_sync` / `sim_task_sync` / `enhanced_forward_task_sync` 改为接收 `{id, ip, user, pw, ...}` dict 而不是 `Device` ORM 对象，避免跨线程使用游离 ORM 实例。
- `upsertdevice` 失败不再抛，返回 `{"ip", "error"}` 让扫描流程继续保存其它设备。
- `_scan_worker` 内每个 `_probe(ip)` 在 `HTTPException`（白名单拦截）下也不会把扫描任务整个挂掉，仅跳过该 IP。
- `_client_ip(request)` 抽公共函数，登录 + OTA 限流统一走同一套 X-Forwarded-For 语义。

## 配置新增的环境变量

| 变量 | 默认 | 含义 |
|-----|-----|------|
| `BMOTARATELIMIT` | `4` | 每 IP 每 `BMOTARATEPERIOD` 内允许的 OTA 请求次数 |
| `BMOTARATEPERIOD` | `60` | 限流窗口秒数 |
| `BMOTABATCHMAX` | `64` | 单次 OTA 批量不得超过的设备数 |
| `BMPREWARMCONCURRENCY` | `64` | 扫描阶段并发 ping 的上限 |
| `BMTRUSTEDPROXYHOPS` | `0` | 信任的反代层数；`>=1` 时登录/OTA 限流用 `X-Forwarded-For` 真实 IP |

## 迁移备注

第一次启动新版本时：

1. `_run_migrations` 会自动 `ALTER TABLE` 给老库补 `sim1signal / sim2signal / firmware_version / token` 四列，不会删除任何数据。
2. 新建的 `auth_tokens` 表由 `Base.metadata.create_all` 自动创建。
3. 升级后所有已登录用户需要重新登录（内存 token 不再有效），之后 v4/v6 两进程共享会话。
4. 反代（nginx/traefik）前置时请设置 `BMTRUSTEDPROXYHOPS=1`，否则登录限流会把所有请求合并为一个 key。
5. 若此前依赖 compose 通过 `${SERVER_PORT}` 让宿主也生效，新 healthcheck 仍然读容器内 `SERVER_PORT`，`-e SERVER_PORT=9000` 时无需改 compose 即可生效。

## UI 仿写

按你要求没有把镜像产物直接塞进仓库，而是以镜像里的 CSS / DOM 为参照，把这套深色 Apple 风 UI 用 Vue 源码重写到 `frontend/src/App.vue`：

- 完整移植原 `/opt/board-manager/static/assets/index-BBO9V6L9.css` 所有样式变量、布局、响应式断点、动画。
- 组件树按镜像里的 DOM 1:1 还原：登录页、顶部导航、4 格统计、消息发送（短信/拨号 Tab）、工具栏、批选条、设备卡片网格（含 4 个卡片按钮）、号码表格、扫描/WiFi/设备详情三个 Modal、空态提示。
- 所有用户可见文案（"控制台 / 设备管理 / 在线 / 离线 / SIM卡 / 登录成功 / 扫描完成 / …"）取自镜像 JS 里的原始中文串。
- 接口对接已修复后端的实际契约：`/api/{login,logout,devices,numbers,scan/start,scan/status/{id},sms/send-direct,tel/dial,devices/{id}/{detail,alias,group,sim},devices/batch/{delete,wifi}}` 全部实际走得通。
- `install.sh` / `Dockerfile` 恢复标准 `pnpm install --frozen-lockfile && pnpm run build` 流程，不再有 `frontend/prebuilt/` 或 `--from-source` 分支，目录保持和原仓库一致的最小集（`frontend/{index.html,package.json,pnpm-lock.yaml,vite.config.js,src/App.vue,src/main.js}`）。
- 本地 `pnpm run build` 产物：`assets/index-*.css ≈14.6 KB` / `assets/index-*.js ≈130 KB`，与镜像里的原产物 14.4 KB / 128 KB 相当。
- 本地启动 `uvicorn backend.main:app` + 构建产物验证：输入 admin 密码 → 登录成功 → 控制台渲染与原镜像 UI 视觉一致（截图在消息里）。

## UI（以参考 zip 为准）

早先自己仿写的那版丢了一些东西（全选、SIM 信号、标题等），按要求不再擅自加减，直接用你给的 `lvyou_smsweb_4/frontend/` 整体替换：`src/App.vue` + `src/main.js` + `index.html` + `package.json` + `pnpm-lock.yaml` + `vite.config.js`。

- 标题回到 "绿邮X系列内网群控"，登录页是手机 emoji。
- 工具栏 `📶 WiFi / 🔄 OTA / 🗑️ 删除`，下方有「全选」三态复选框（空/半选/全选）。
- 设备卡片 SIM 行显示信号百分比徽章（来自 `sim1signal / sim2signal`）。
- 设备详情 Modal 显示 WiFi 信号强度并按 dBm 着色。
- 其他模块（扫描、消息发送、号码列表、分组/改名/批量 WiFi/批量 OTA）均与参考 zip 一致。

## 未改动

- `BMUIPASS` 默认值、`_check_login_credentials` 明文 `compare_digest` 比较、`Device.passwd` 明文存储 —— 按用户要求排除，本轮不改。
