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
# 若已装过元包 PySide6 / PySide6_Addons，会打进 WebEngine 等，frozen 体积暴增；构建前卸掉，仅保留 requirements 中的 Essentials。
"$LINKFLOW_BUILD_PYTHON" -m pip uninstall -y PySide6 PySide6_Addons 2>/dev/null || true
"$LINKFLOW_BUILD_PYTHON" -m pip install -q -r "$ROOT/requirements.txt" "pyinstaller>=6.0"
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

chmod +x "$DESKTOP_IN_DIST"


if [[ -f "$ROOT/hosts.json" ]]; then
  cp "$ROOT/hosts.json" "$APPDIR/hosts.json"
fi

mv "$APPDIR/LinkFlow.desktop" "$HOME/Desktop/LinkFlow.desktop"


TGZ="$ROOT/dist/LinkFlow-linux-amd64.tar.gz"
rm -f "$TGZ"
tar -C "$ROOT/dist" -czf "$TGZ" LinkFlow

echo ""
echo "应用已生成: $APPDIR ($(du -sh "$APPDIR" | cut -f1) 解压后)"
echo "  可执行文件: $EXEC"
echo "  桌面入口（可复制到桌面）: $DESKTOP_IN_DIST"
echo "  压缩分发包（通常 <100MB）: $TGZ ($(du -sh "$TGZ" | cut -f1))"
echo "解压后体积主要来自 Qt6 + Python + cryptography；分发请优先发 .tar.gz。"
echo "若移动目录，请改 LinkFlow.desktop 中的 Exec、Icon、Path。"
