#!/usr/bin/env bash

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[✓]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[!]${NC} $*"; }
log_err() { echo -e "${RED}[✗]${NC} $*"; }
title() { echo -e "${BLUE}==>${NC} $*"; }

need_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    log_err "请使用 sudo 运行"
    exit 1
  fi
}

read_tty() {
  local prompt="$1"
  local varname="$2"
  if [[ -t 0 ]]; then
    read -r -p "${prompt}" "${varname}"
  else
    read -r -p "${prompt}" "${varname}" < /dev/tty
  fi
}

check_dependencies() {
  for cmd in python3 npm systemctl sed; do
    if ! command -v "${cmd}" >/dev/null 2>&1; then
      log_err "缺少依赖命令: ${cmd}"
      exit 1
    fi
  done
}