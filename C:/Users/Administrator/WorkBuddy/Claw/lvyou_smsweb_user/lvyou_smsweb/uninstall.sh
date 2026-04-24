#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
APPDIR="/opt/board-manager"
CONFIG_FILE="/etc/board-manager.conf"

source "${ROOT_DIR}/scripts/common.sh"

usage() {
  cat <<EOF
用法:
  sudo ./uninstall.sh
  sudo ./uninstall.sh --force
EOF
}

FORCE=0
if [[ "${1:-}" == "--force" ]]; then
  FORCE=1
elif [[ -n "${1:-}" ]]; then
  usage
  exit 1
fi

need_root

title "卸载 Board Manager"

echo ""
log_warn "将删除以下内容："
echo "  - ${APPDIR}"
echo "  - ${CONFIG_FILE}"
echo "  - /etc/systemd/system/board-manager-v4.service"
echo "  - /etc/systemd/system/board-manager-v6.service"
echo ""

if [[ "${FORCE}" -ne 1 ]]; then
  read -r -p "确认卸载？(y/N): " confirm
  if [[ ! "${confirm:-}" =~ ^[Yy] ]]; then
    log_info "已取消"
    exit 0
  fi
fi

title "停止并禁用服务"

systemctl stop board-manager-v4 2>/dev/null || true
systemctl stop board-manager-v6 2>/dev/null || true

systemctl disable board-manager-v4 2>/dev/null || true
systemctl disable board-manager-v6 2>/dev/null || true

title "删除 systemd 服务文件"

rm -f /etc/systemd/system/board-manager-v4.service
rm -f /etc/systemd/system/board-manager-v6.service

systemctl daemon-reload
systemctl reset-failed 2>/dev/null || true

title "删除程序与配置"

if [[ -d "${APPDIR}" ]]; then
  rm -rf "${APPDIR}"
  log_info "已删除 ${APPDIR}"
else
  log_warn "${APPDIR} 不存在，跳过"
fi

if [[ -f "${CONFIG_FILE}" ]]; then
  rm -f "${CONFIG_FILE}"
  log_info "已删除 ${CONFIG_FILE}"
else
  log_warn "${CONFIG_FILE} 不存在，跳过"
fi

title "删除命令行工具"

if [[ -f /usr/local/bin/lvyou ]]; then
  rm -f /usr/local/bin/lvyou
  log_info "已删除 lvyou 命令"
else
  log_warn "/usr/local/bin/lvyou 不存在，跳过"
fi

title "完成"

log_info "卸载完成"
log_info "如需重新安装，请执行: sudo ./install.sh install"
