# Changelog

本项目的重要变更都会记录在此。

## [Unreleased]

### Added
- 重构项目目录为单一源码来源结构
- 新增独立 `install.sh`
- 新增独立 `uninstall.sh`
- 新增 `systemd` 服务模板目录
- 新增 `backend/requirements.txt`
- 新增 `README.md`
- 新增 `.gitignore`

### Changed
- 后端源码统一收敛到 `backend/main.py`
- 前端源码统一收敛到 `frontend/src/App.vue`
- 安装脚本不再内嵌大段业务代码
- 前端构建与部署流程改为标准 `vite build`
- systemd 服务改为模板渲染安装方式

### Fixed
- 修复前端登录失败提示问题
- 修复短信发送功能
- 修复拨号功能
- 修复部分模板字符串污染问题
- 修复批量操作提示文案

## [1.0.0] - 2026-03-15

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
- 设备别名 / 分组管理
- FastAPI + SQLite 后端
- Vue + Vite 前端
- systemd 双服务部署

### Notes
- 当前版本核心功能可用
- 扫描后前端列表刷新体验仍可继续优化
- 建议后续继续完善安装脚本与文档
