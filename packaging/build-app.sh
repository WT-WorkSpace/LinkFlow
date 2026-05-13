#!/usr/bin/env bash
# PyInstaller 打包为 dist/LinkFlow/（onedir，无控制台窗口）
set -euo pipefail

# 本机构建使用的 Python 绝对路径：只改引号里的地址即可。
# 若运行前已 export LINKFLOW_BUILD_PYTHON=...，则以环境变量为准（不改本文件）。
: "${LINKFLOW_BUILD_PYTHON:=/home/wt/miniconda3/envs/pcdview/bin/python}"


SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ ! -x "$LINKFLOW_BUILD_PYTHON" ]]; then
  echo "错误: 解释器不存在或不可执行: $LINKFLOW_BUILD_PYTHON" >&2
  exit 1
fi

echo "使用解释器: $LINKFLOW_BUILD_PYTHON"
cd "$ROOT"
"$LINKFLOW_BUILD_PYTHON" -m pip install -q "pyinstaller>=6.0"
"$LINKFLOW_BUILD_PYTHON" -m PyInstaller --noconfirm "$ROOT/LinkFlow.spec"

APPDIR="$ROOT/dist/LinkFlow"
EXEC="$APPDIR/LinkFlow"
if [[ ! -x "$EXEC" ]]; then
  echo "错误: 未生成可执行文件 $EXEC" >&2
  exit 1
fi

if [[ -d "$APPDIR/_internal/icon" ]]; then
  rm -rf "$APPDIR/icon"
  cp -a "$APPDIR/_internal/icon" "$APPDIR/"
fi
ICON="$APPDIR/icon/link.svg"
if [[ ! -f "$ICON" ]]; then
  echo "警告: 未找到图标 $ICON，桌面图标可能无法显示" >&2
fi

DESKTOP_IN_DIST="$APPDIR/LinkFlow.desktop"
cat >"$DESKTOP_IN_DIST" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=LinkFlow
GenericName=SSH 文件传输
Comment=LinkFlow 独立应用（PyInstaller）
Exec=$EXEC
Icon=$ICON
Path=$APPDIR
Terminal=false
Categories=Network;
Keywords=ssh;sftp;transfer;
EOF

sudo chmod +x "$DESKTOP_IN_DIST"


# 将 hosts.json 复制到 dist/LinkFlow 目录
cp "$ROOT/hosts.json" "$APPDIR/hosts.json"

mv "$APPDIR/LinkFlow.desktop" "$HOME/Desktop/LinkFlow.desktop"


echo ""
echo "应用已生成: $APPDIR"
echo "  可执行文件: $EXEC"
echo "  桌面入口（可复制到桌面）: $DESKTOP_IN_DIST"
echo "分发整个 dist/LinkFlow 文件夹即可；若移动目录，请改 LinkFlow.desktop 中的 Exec、Icon、Path。"
