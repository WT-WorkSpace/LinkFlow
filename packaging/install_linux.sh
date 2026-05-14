#!/usr/bin/env bash
# 勿用 sh 直接跑：系统 sh 多为 dash，不支持 pipefail / [[。未在 bash 下时会自动改用 bash 重新执行。
if [[ -z "${BASH_VERSION:-}" ]]; then
  exec /usr/bin/env bash "$0" "$@"
fi
# onefile 打包 + 在用户桌面生成 LinkFlow.desktop（GNOME/KDE 等）
# 本脚本位于 packaging/，仓库根为其上一级目录。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_PATH="/home/wt/miniconda3/envs/pcdview/bin/python"

# --add-data 使用 icon:icon，与 main.py 中 frozen 时 _MEIPASS/icon/link.svg 一致
$PYTHON_PATH -m PyInstaller --onefile --windowed --name "LinkFlow" --icon "icon/link.ico" \
  --add-data "icon:icon" \
  main.py

EXE="$ROOT/dist/LinkFlow"
if [[ ! -f "$EXE" ]]; then
  echo "错误: 未找到可执行文件 $EXE" >&2
  exit 1
fi
chmod +x "$EXE"

if [[ -f "$ROOT/hosts.json" ]]; then
  cp -f "$ROOT/hosts.json" "$ROOT/dist/hosts.json"
fi

DESKTOP_DIR="${XDG_DESKTOP_DIR:-$HOME/Desktop}"
DESKTOP_FILE="$DESKTOP_DIR/LinkFlow.desktop"
ICON="$ROOT/icon/link.svg"
[[ -f "$ICON" ]] || ICON="$ROOT/icon/link.ico"

cat >"$DESKTOP_FILE" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=LinkFlow
GenericName=SSH 文件传输
Comment=LinkFlow（PyInstaller onefile）
Exec=$EXE
Icon=$ICON
Path=$ROOT/dist
Terminal=false
Categories=Network;
Keywords=ssh;sftp;transfer;
EOF
chmod +x "$DESKTOP_FILE"

if command -v gio &>/dev/null; then
  gio set "$DESKTOP_FILE" metadata::trusted true 2>/dev/null || true
fi

echo "已生成桌面快捷方式: $DESKTOP_FILE"
echo "可执行文件: $EXE"
