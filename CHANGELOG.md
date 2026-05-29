# Changelog

本项目的重要变更都会记录在此。

## [Unreleased]

## [5.1.0] - 2026-05-29

### Added
- **路由模块化**：`main.py` 从 2379 行拆分为模块化结构
  - `backend/config.py` — 环境变量配置集中管理
  - `backend/db.py` — 数据库模型 + Token CRUD
  - `backend/security.py` — SSRF 防护、IP 校验、网络工具
  - `backend/routes/auth.py` — 登录/登出/健康检查路由
  - `backend/routes/devices.py` — 设备 CRUD/SMS/WiFi/SIM/Forward/OTA 路由
  - `backend/routes/scan.py` — 网络扫描路由
  - `backend/routes/config.py` — 批量配置读写路由
- **前端组件化**：从单文件 800+ 行拆分为 16 个 Vue 组件
  - `components/` — AppHeader、LoginView、MessagePanel、WifiModal、OtaModal、DetailModal 等
  - `stores/` — Pinia 状态管理（auth、devices、scan、notice、dialog）
  - `api/` — axios 封装（client.js、endpoints.js、auth.js）
  - `utils/` — 工具函数
- **CustomSelect 组件**：自定义深色下拉框，替换原生 `<select>` 解决白底刺眼问题
- **Prometheus 监控**：`/metrics` 端点，支持 HTTP 延迟/计数/状态码监控
- **服务端搜索分页**：`/api/devices` 支持 `q` 和 `group` 参数，服务端过滤
- **日志轮转**：`RotatingFileHandler`，防止日志文件无限增长
- **本地网络缓存**：SSRF 白名单检查加 TTL，避免每次请求 fork `ip` 命令
- **`/api/me` 接口**：SPA 启动时检查登录状态
- **`docker-entrypoint.sh`**：独立入口脚本，正确处理双进程信号转发
- **启动安全校验**：拒绝空/默认密码启动，Cookie 配置一致性检查

### Changed
- **Dockerfile**：双进程模式加 `trap` 信号转发，`docker stop` 正确 graceful shutdown
- **prewarm_neighbors**：从每 IP 一个 thread 改为复用共享 ThreadPoolExecutor
- **Cookie + CSRF 认证**：httpOnly cookie + X-CSRF-Token 双重防护，替代纯 Bearer Token
- **Token TTL**：从 8 小时缩短到 2 小时（可通过 `BMTOKENTTL` 配置）
- **登录限流**：从单维度改为 IP + 用户名双维度，成功登录重置用户窗口
- **BMUIPASS 默认值**：从 `admin` 改为空，强制要求设置强密码
- **用户正则替换**：引入 `regex` 库替代 `re`，支持超时防 ReDoS
- **移除 `aiosqlite`**：代码用同步 SQLAlchemy，该依赖未使用

### Fixed
- **OTA 接口 500 错误**：`request=None` 导致 `AttributeError`，改为 `request: Request`
- **深色主题下拉框白底**：原生 `<select>` 的 `<option>` 无法用 CSS 完全控制，改用自定义组件
- **pnpm-lock.yaml 不同步**：`package.json` 新增 `pinia` 依赖但 lockfile 未更新，导致 CI 构建失败

## [5.0.0] - 2026-04-15

### Added
- Web 登录界面
- 设备扫描
- 设备列表与号码列表
- 短信发送
- 电话拨号 / TTS
- 单台 SIM 编辑
- 批量 SIM 配置
- 批量 WiFi 配置
- 批量转发配置
- 批量设备配置（正则替换 + 预设模板）
- 批量 OTA 升级
- 设备别名 / 分组管理
- 审计日志
- FastAPI + SQLite 后端
- Vue + Vite 前端
- systemd 双服务部署（IPv4 + IPv6）
- Docker 部署支持
- 安全增强：SSRF 防护、频率限制、常量时间密码比较
