#!/bin/bash

# 绿邮X系列内网群控系统 Docker 构建脚本
# 版本: 3.4.0

set -e

# 默认配置
IMAGE_NAME="lovexme/lvyou-smsweb"
TAG="latest"
VERSION="3.4.0"
PLATFORMS="linux/amd64,linux/arm64"
BUILD_ARGS=""
PUSH=false

# 显示帮助信息
show_help() {
    cat << EOF
绿邮X系列内网群控系统 Docker 构建脚本

用法: $0 [选项]

选项:
  -t, --tag TAG         镜像标签 (默认: latest)
  -v, --version VER     版本号 (默认: 3.4.0)
  -p, --push            推送到 Docker Hub
  --platform PLATFORMS  构建平台 (默认: linux/amd64,linux/arm64)
  -h, --help            显示此帮助信息

示例:
  $0                          # 构建最新版本镜像
  $0 -t v3.4.0               # 构建指定标签
  $0 -p -t v3.4.0            # 构建并推送
  $0 --platform linux/amd64  # 仅构建 amd64 架构

环境变量:
  DOCKER_USERNAME   Docker Hub 用户名
  DOCKER_PASSWORD   Docker Hub 密码或访问令牌

注意:
  1. 推送镜像需要先登录 Docker Hub:
       docker login -u \$DOCKER_USERNAME -p \$DOCKER_PASSWORD
  2. 多平台构建需要开启 buildx:
       docker buildx create --use
EOF
}

# 解析命令行参数
while [[ $# -gt 0 ]]; do
    case $1 in
        -t|--tag)
            TAG="$2"
            shift 2
            ;;
        -v|--version)
            VERSION="$2"
            shift 2
            ;;
        -p|--push)
            PUSH=true
            shift
            ;;
        --platform)
            PLATFORMS="$2"
            shift 2
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo "错误: 未知选项 $1"
            show_help
            exit 1
            ;;
    esac
done

# 检查 Docker 是否可用
if ! command -v docker &> /dev/null; then
    echo "错误: Docker 未安装"
    exit 1
fi

# 构建镜像
echo "=== 构建 Docker 镜像 ==="
echo "镜像名称: ${IMAGE_NAME}:${TAG}"
echo "版本: ${VERSION}"
echo "平台: ${PLATFORMS}"
echo "构建参数: ${BUILD_ARGS}"
echo ""

# 创建构建参数
BUILD_ARGS="--build-arg VERSION=${VERSION}"

if [ "$PUSH" = true ]; then
    echo "模式: 构建并推送"
    # 检查是否已登录 Docker Hub
    if ! docker info 2>/dev/null | grep -q "Username"; then
        echo "警告: 未检测到 Docker Hub 登录状态"
        echo "请先运行: docker login -u <username> -p <password>"
        read -p "是否继续构建但不推送? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
        PUSH=false
    fi
fi

# 构建命令
if [ "$PUSH" = true ]; then
    echo "启用多平台构建和推送..."
    docker buildx build \
        --platform "${PLATFORMS}" \
        --tag "${IMAGE_NAME}:${TAG}" \
        --tag "${IMAGE_NAME}:${VERSION}" \
        ${BUILD_ARGS} \
        --push \
        .
else
    echo "本地构建..."
    docker build \
        --tag "${IMAGE_NAME}:${TAG}" \
        --tag "${IMAGE_NAME}:${VERSION}" \
        ${BUILD_ARGS} \
        .
fi

# 构建完成
echo ""
echo "=== 构建完成 ==="
echo "镜像已构建:"
echo "  - ${IMAGE_NAME}:${TAG}"
echo "  - ${IMAGE_NAME}:${VERSION}"
echo ""
echo "运行容器:"
echo "  docker run -d --net=host \\"
echo "    -e BMUIPASS=your_password \\"
echo "    -v ./data:/opt/board-manager/data \\"
echo "    ${IMAGE_NAME}:${TAG}"
echo ""
echo "或使用 Docker Compose:"
echo "  docker-compose up -d"
echo ""
if [ "$PUSH" = true ]; then
    echo "镜像已推送到 Docker Hub"
fi