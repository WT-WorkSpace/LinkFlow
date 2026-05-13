#!/usr/bin/env python3
"""
双端（输出端 / 输入端）SSH 文件传输 — Qt（PySide6）界面。
传输列表按文件逐行展示；文件夹会预先展开为多个文件行，未开始的行状态为「等待传输」，并显示「耗时」列。
"""

from __future__ import annotations

import json
import os
import posixpath
import queue
import shutil
import stat
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStyle,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

try:
    import paramiko
except ImportError as e:  # pragma: no cover
    raise SystemExit(
        "请先安装依赖: pip install -r requirements.txt\n" f"详情: {e}"
    ) from e


def _format_speed(bytes_per_sec: float) -> str:
    if bytes_per_sec >= 1024 * 1024:
        return f"{bytes_per_sec / (1024 * 1024):.2f} MB/s"
    if bytes_per_sec >= 1024:
        return f"{bytes_per_sec / 1024:.2f} KB/s"
    return f"{bytes_per_sec:.0f} B/s"


def _format_size(n: int) -> str:
    if n >= 1024**3:
        return f"{n / (1024**3):.2f} GiB"
    if n >= 1024**2:
        return f"{n / (1024**2):.2f} MiB"
    if n >= 1024:
        return f"{n / 1024:.2f} KiB"
    return f"{n} B"


def _format_eta(seconds: float) -> str:
    if seconds < 0 or seconds > 86400 * 7:
        return "--:--:--"
    s = int(seconds)
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _mtime_str(ts: float | int) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    except (OSError, ValueError):
        return "—"


def _log_timestamp() -> str:
    """传输日志行首时间戳。"""
    return datetime.now().strftime("【%Y-%m-%d %H:%M:%S】")


ROLE_PATH = Qt.ItemDataRole.UserRole
ROLE_IS_DIR = Qt.ItemDataRole.UserRole + 1
ROLE_JOB_ID = Qt.ItemDataRole.UserRole
ROLE_BATCH_ID = Qt.ItemDataRole.UserRole + 10
# 传输表名称列：记录重传所需的源/目标绝对路径与方向
ROLE_XFER_DIR = Qt.ItemDataRole.UserRole + 20
ROLE_XFER_SP = Qt.ItemDataRole.UserRole + 21
ROLE_XFER_DP = Qt.ItemDataRole.UserRole + 22
# 传输表名称列：日志用「源/目标」端点描述（预设名、IP、本机等）
ROLE_XFER_LOG_SRC = Qt.ItemDataRole.UserRole + 30
ROLE_XFER_LOG_DST = Qt.ItemDataRole.UserRole + 31

# 预设下拉框中「本机模式」条目的 itemData 标记（与 SSH 预设 dict 区分）
PRESET_COMBO_DATA_LOCAL = "__local_disk__"

# 传输列表 table_jobs 列索引（名称后为「传输方向」）
XFER_JOB_COL_NAME = 0
XFER_JOB_COL_ROUTE = 1
XFER_JOB_COL_SIZE = 2
XFER_JOB_COL_DONE = 3
XFER_JOB_COL_TIME = 4
XFER_JOB_COL_SPEED = 5
XFER_JOB_COL_PROGRESS = 6
XFER_JOB_COL_STATE = 7
XFER_JOB_COL_ACTION = 8

# 与 main.py 同目录的 JSON，存放常用 SSH 设备（密码为明文，请妥善保管文件权限）
HOSTS_CONFIG_FILENAME = "hosts.json"


def hosts_config_path() -> Path:
    return Path(__file__).resolve().parent / HOSTS_CONFIG_FILENAME


def load_host_devices() -> list[dict[str, Any]]:
    path = hosts_config_path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    devices = raw.get("devices") if isinstance(raw, dict) else raw
    if not isinstance(devices, list):
        return []
    out: list[dict[str, Any]] = []
    for item in devices:
        if not isinstance(item, dict):
            continue
        host = str(item.get("host", "")).strip()
        if not host:
            continue
        name = str(item.get("name", "") or host).strip()
        try:
            port = int(item.get("port", 22))
        except (TypeError, ValueError):
            port = 22
        dp = str(item.get("default_path", "")).strip()
        out.append(
            {
                "name": name,
                "host": host,
                "port": port,
                "user": str(item.get("user", "")).strip(),
                "password": str(item.get("password", "")),
                "key_path": str(item.get("key_path", "")).strip(),
                "default_path": dp,
            }
        )
    return out


def _expand_remote_path_tilde(path: str, remote_home: str | None) -> str:
    """将远端路径中的 ~ 展开为 remote_home（仅处理前缀 ~/ 或单独的 ~）。"""
    path = path.replace("\\", "/").strip()
    if not path:
        return remote_home or "/"
    home = (remote_home or "").rstrip("/")
    if path == "~":
        return home or "/"
    if path.startswith("~/") and home:
        return posixpath.normpath(posixpath.join(home, path[2:]))
    return posixpath.normpath(path)


def _ssh_remote_home(ssh: paramiko.SSHClient) -> str:
    """通过 SSH 会话读取远端用户主目录；失败时退回 /。"""
    for cmd in (
        "echo $HOME",
        'getent passwd "$(id -un)" | cut -d: -f6',
    ):
        try:
            _stdin, stdout, _stderr = ssh.exec_command(cmd, timeout=12)
            data = stdout.read().decode("utf-8", errors="replace").strip()
            for line in data.splitlines():
                h = line.strip()
                if h.startswith("/"):
                    return posixpath.normpath(h)
        except Exception:
            continue
    return "/"


def save_host_devices(devices: list[dict[str, Any]]) -> None:
    path = hosts_config_path()
    payload = {"devices": devices}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _decode_sftp_name(name: str | bytes) -> str:
    if isinstance(name, bytes):
        return name.decode("utf-8", errors="replace")
    return str(name)


def _resolve_remote_mode(
    sftp: paramiko.SFTPClient, full_path: str, attr: paramiko.SFTPAttributes
) -> int:
    """部分服务器 listdir_attr 不填 st_mode，需 stat 后才能区分文件/目录。"""
    m = attr.st_mode
    if m is not None and m != 0:
        return int(m)
    try:
        st = sftp.stat(full_path)
        return int(st.st_mode)
    except OSError:
        return 0


def _sftp_remove_recursive(sftp: paramiko.SFTPClient, path: str) -> None:
    st = sftp.stat(path)
    mode = int(st.st_mode)
    if stat.S_ISDIR(mode):
        for attr in sftp.listdir_attr(path):
            name = _decode_sftp_name(attr.filename)
            if name in (".", ".."):
                continue
            full = posixpath.join(path, name)
            _sftp_remove_recursive(sftp, full)
        sftp.rmdir(path)
    else:
        sftp.remove(path)


class SidePane(QWidget):
    """单侧：连接表单 + 目录树。"""

    def __init__(self, title: str) -> None:
        super().__init__()
        self.label = title
        self.is_local = False
        self.ssh: paramiko.SSHClient | None = None
        self.sftp: paramiko.SFTPClient | None = None
        self._main: MainWindow | None = None

        root = QVBoxLayout(self)
        grp = QGroupBox(title)
        gl = QGridLayout(grp)

        preset_row = QWidget()
        pr = QHBoxLayout(preset_row)
        pr.setContentsMargins(0, 0, 0, 0)
        pr.setSpacing(10)
        pr.addWidget(QLabel("预设设备"))
        self.combo_hosts = QComboBox()
        self.combo_hosts.setMinimumContentsLength(12)
        self.combo_hosts.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.combo_hosts.addItem("-- 手动输入 --", None)
        self.combo_hosts.addItem(
            "本机模式（本地磁盘，无需 SSH）", PRESET_COMBO_DATA_LOCAL
        )
        self.combo_hosts.currentIndexChanged.connect(self._on_host_preset_changed)
        pr.addWidget(self.combo_hosts, 1)
        self.btn_save_preset = QPushButton("保存为预设…")
        self.btn_save_preset.clicked.connect(self._on_save_preset_clicked)
        pr.addWidget(self.btn_save_preset)
        gl.addWidget(preset_row, 0, 0, 1, 3)

        r = 1
        gl.addWidget(QLabel("主机"), r, 0)
        self.ed_host = QLineEdit("127.0.0.1")
        gl.addWidget(self.ed_host, r, 1, 1, 2)
        r += 1
        gl.addWidget(QLabel("端口"), r, 0)
        self.ed_port = QLineEdit("22")
        gl.addWidget(self.ed_port, r, 1)
        r += 1
        gl.addWidget(QLabel("用户名"), r, 0)
        self.ed_user = QLineEdit()
        gl.addWidget(self.ed_user, r, 1, 1, 2)
        r += 1
        gl.addWidget(QLabel("密码"), r, 0)
        self.ed_pass = QLineEdit()
        self.ed_pass.setEchoMode(QLineEdit.EchoMode.Password)
        gl.addWidget(self.ed_pass, r, 1, 1, 2)
        r += 1
        gl.addWidget(QLabel("私钥"), r, 0)
        key_row = QWidget()
        key_l = QHBoxLayout(key_row)
        key_l.setContentsMargins(0, 0, 0, 0)
        self.ed_key = QLineEdit()
        btn_key = QPushButton("浏览…")
        btn_key.clicked.connect(self._browse_key)
        key_l.addWidget(self.ed_key)
        key_l.addWidget(btn_key)
        gl.addWidget(key_row, r, 1, 1, 2)
        r += 1

        btn_row = QWidget()
        br = QHBoxLayout(btn_row)
        br.setContentsMargins(0, 0, 0, 0)
        self.btn_connect = QPushButton("连接")
        self.btn_disconnect = QPushButton("断开")
        self.btn_connect.clicked.connect(self._emit_connect)
        self.btn_disconnect.clicked.connect(self._emit_disconnect)
        br.addWidget(self.btn_connect)
        br.addWidget(self.btn_disconnect)
        br.addStretch()
        gl.addWidget(btn_row, r, 0, 1, 3)
        r += 1

        self.lbl_status = QLabel("状态：未连接")
        self.lbl_status.setStyleSheet("color: gray;")
        gl.addWidget(self.lbl_status, r, 0, 1, 3)
        r += 1

        nav = QWidget()
        nl = QHBoxLayout(nav)
        nl.setContentsMargins(0, 0, 0, 0)
        self.btn_up = QPushButton("⬆️")
        self.btn_refresh = QPushButton("刷新")
        self.ed_path = QLineEdit()
        self.btn_up.clicked.connect(self._emit_go_up)
        self.btn_refresh.clicked.connect(self._emit_refresh)
        nl.addWidget(self.btn_up)
        nl.addWidget(self.ed_path, 1)
        nl.addWidget(self.btn_refresh)
        gl.addWidget(nav, r, 0, 1, 3)
        r += 1

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["名称", "大小", "类型", "已修改"])
        self.tree.setColumnWidth(0, 200)
        self.tree.setColumnWidth(1, 90)
        self.tree.setColumnWidth(2, 80)
        self.tree.setColumnWidth(3, 130)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.itemDoubleClicked.connect(self._on_tree_double)
        self.tree.itemSelectionChanged.connect(self._update_sel_label)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        gl.addWidget(self.tree, r, 0, 1, 3)
        r += 1

        self.lbl_sel = QLabel("0 个对象被选定")
        gl.addWidget(self.lbl_sel, r, 0, 1, 3)
        r += 1

        self.btn_xfer = QPushButton()
        gl.addWidget(self.btn_xfer, r, 0, 1, 3)

        root.addWidget(grp)
        self.ed_path.setText(os.path.expanduser("~"))

    def set_main(self, main: MainWindow) -> None:
        self._main = main

    def _on_tree_context_menu(self, pos) -> None:
        if self._main:
            self._main.show_pane_file_context_menu(self, pos)

    def refresh_host_combo(self) -> None:
        if not self._main:
            return
        self.combo_hosts.blockSignals(True)
        self.combo_hosts.clear()
        self.combo_hosts.addItem("-- 手动输入 --", None)
        self.combo_hosts.addItem(
            "本机模式", PRESET_COMBO_DATA_LOCAL
        )
        for dev in self._main.get_host_devices():
            label = str(dev.get("name") or dev.get("host") or "未命名")
            self.combo_hosts.addItem(label, dict(dev))
        self.combo_hosts.setCurrentIndex(0)
        if self.is_local:
            self._leave_local_mode()
        self.combo_hosts.blockSignals(False)

    def _on_host_preset_changed(self, index: int) -> None:
        d = self.combo_hosts.itemData(index, Qt.ItemDataRole.UserRole)
        if d == PRESET_COMBO_DATA_LOCAL:
            self._enter_local_mode()
            return
        if self.is_local:
            self._leave_local_mode()
        if index <= 0:
            return
        if not isinstance(d, dict):
            return
        self.apply_device_dict(d)
        if self._main:
            self._main.connect_side(self)

    def apply_device_dict(self, d: dict) -> None:
        if d.get("host"):
            self.ed_host.setText(str(d["host"]))
        self.ed_port.setText(str(d.get("port", 22)))
        self.ed_user.setText(str(d.get("user", "")))
        self.ed_pass.setText(str(d.get("password", "")))
        self.ed_key.setText(str(d.get("key_path", "")))
        dp = str(d.get("default_path", "")).strip()
        if dp:
            self.ed_path.setText(dp.replace("\\", "/"))
        else:
            # 未配置默认路径时清空，避免沿用本机绝对路径（如 /home/wt/...）误作远端目录
            self.ed_path.setText("")

    def active_preset_device(self) -> dict[str, Any] | None:
        idx = self.combo_hosts.currentIndex()
        if idx <= 0:
            return None
        d = self.combo_hosts.itemData(idx, Qt.ItemDataRole.UserRole)
        if d == PRESET_COMBO_DATA_LOCAL or not isinstance(d, dict):
            return None
        return d

    def _enter_local_mode(self) -> None:
        self.is_local = True
        self.close_remote()
        self.lbl_status.setText("状态：本地模式")
        self.lbl_status.setStyleSheet("color: green;")
        if not self.ed_path.text().strip():
            self.ed_path.setText(os.path.expanduser("~"))
        if self._main:
            cur = self.ed_path.text().strip() or os.path.expanduser("~")
            self._main._append_log(
                f"[{self.label}] 本机已连接，浏览路径：{cur}"
            )
            self._main.refresh_list(self)

    def _leave_local_mode(self) -> None:
        self.is_local = False
        self.lbl_status.setText("状态：未连接")
        self.lbl_status.setStyleSheet("color: gray;")
        if self._main:
            self._main._append_log(
                f"[{self.label}] 本机已断开（已切换为手动输入或其它 SSH 预设）"
            )

    def _on_save_preset_clicked(self) -> None:
        if self._main:
            self._main.save_host_preset_from_pane(self)

    def set_xfer_button(self, text: str, slot) -> None:
        try:
            self.btn_xfer.clicked.disconnect()
        except TypeError:
            pass
        self.btn_xfer.setText(text)
        self.btn_xfer.clicked.connect(slot)

    def _browse_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择私钥文件")
        if path:
            self.ed_key.setText(path)

    def _emit_connect(self) -> None:
        if self._main:
            self._main.connect_side(self)

    def _emit_disconnect(self) -> None:
        if self._main:
            self._main.disconnect_side(self)

    def _emit_go_up(self) -> None:
        if self._main:
            self._main.go_up(self)

    def _emit_refresh(self) -> None:
        if self._main:
            self._main.refresh_list(self)

    def _on_tree_double(self, item: QTreeWidgetItem, _col: int) -> None:
        if item.data(0, ROLE_IS_DIR):
            self.ed_path.setText(str(item.data(0, ROLE_PATH)))
            if self._main:
                self._main.refresh_list(self)

    def _update_sel_label(self) -> None:
        n = len(self.tree.selectedItems())
        self.lbl_sel.setText(f"{n} 个对象被选定")

    def close_remote(self) -> None:
        for c in (self.sftp, self.ssh):
            if c is not None:
                try:
                    c.close()
                except Exception:
                    pass
        self.sftp = None
        self.ssh = None

    def local_checked(self) -> bool:
        idx = self.combo_hosts.currentIndex()
        d = self.combo_hosts.itemData(idx, Qt.ItemDataRole.UserRole)
        return d == PRESET_COMBO_DATA_LOCAL

    def selected_paths(self) -> list[tuple[str, bool]]:
        out: list[tuple[str, bool]] = []
        for it in self.tree.selectedItems():
            p = it.data(0, ROLE_PATH)
            if p:
                out.append((str(p), bool(it.data(0, ROLE_IS_DIR))))
        return out


def _xfer_log_endpoint(pane: SidePane) -> str:
    """传输日志中的端点描述：本机、预设别名、或 user@host:port。"""
    if pane.local_checked():
        return "本机"
    host = pane.ed_host.text().strip()
    try:
        port = int((pane.ed_port.text() or "22").strip() or "22")
    except ValueError:
        port = 22
    user = (pane.ed_user.text() or "").strip()
    if host and user:
        base = f"{user}@{host}:{port}"
    elif host:
        base = f"{host}:{port}"
    else:
        base = ""

    dev = pane.active_preset_device()
    if isinstance(dev, dict):
        nm = str(dev.get("name", "")).strip()
        if nm:
            if base and nm != host:
                return f"{nm}({base})"
            if base:
                return base
            return nm
    return base or pane.label


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("双端 SCP / SFTP 传输")
        self.resize(1100, 720)
        self.setMinimumSize(900, 600)

        self._msg_queue: queue.Queue = queue.Queue()
        self._xfer_queue: queue.Queue = queue.Queue()
        self._xfer_pending_lock = threading.Lock()
        self._xfer_pending_batches = 0
        self._xfer_batch_seq = 0
        self._xfer_consumer_thread: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._io_lock = threading.Lock()
        self._job_seq_lock = threading.Lock()
        self._job_seq = 0
        self._xfer_skip_jobs_lock = threading.Lock()
        self._xfer_skip_job_ids: set[str] = set()
        self._xfer_abort_lock = threading.Lock()
        self._xfer_abort_job_id: str | None = None
        self._devices: list[dict[str, Any]] = load_host_devices()

        cw = QWidget()
        self.setCentralWidget(cw)
        main_l = QVBoxLayout(cw)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.pane_out = SidePane("主机1")
        self.pane_in = SidePane("主机2")
        self.pane_out.set_main(self)
        self.pane_in.set_main(self)
        self.pane_out.refresh_host_combo()
        self.pane_in.refresh_host_combo()
        self.pane_out.set_xfer_button("发送到主机2 →", lambda: self.start_transfer("out_to_in"))
        self.pane_in.set_xfer_button("← 发送到主机1", lambda: self.start_transfer("in_to_out"))
        split.addWidget(self.pane_out)
        split.addWidget(self.pane_in)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        main_l.addWidget(split, 2)

        tabs = QTabWidget()
        jobs = QWidget()
        jl = QVBoxLayout(jobs)
        topj = QHBoxLayout()
        self.lbl_job_summary = QLabel("就绪")
        btn_clear = QPushButton("清除已完成")
        btn_clear.clicked.connect(self._clear_done_jobs)
        topj.addWidget(self.lbl_job_summary)
        topj.addStretch()
        topj.addWidget(btn_clear)
        jl.addLayout(topj)

        self.table_jobs = QTableWidget(0, 9)
        self.table_jobs.setHorizontalHeaderLabels(
            [
                "名称",
                "传输方向",
                "大小",
                "已传输",
                "耗时",
                "速度",
                "进度",
                "状态",
                "操作",
            ]
        )
        self.table_jobs.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for c in range(1, 9):
            self.table_jobs.horizontalHeader().setSectionResizeMode(
                c, QHeaderView.ResizeMode.ResizeToContents
            )
        jl.addWidget(self.table_jobs)

        log_w = QWidget()
        ll = QVBoxLayout(log_w)
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumBlockCount(5000)
        ll.addWidget(self.txt_log)

        tabs.addTab(jobs, "传输列表")
        tabs.addTab(log_w, "传输日志")
        main_l.addWidget(tabs, 1)

        bottom = QHBoxLayout()
        bottom.addStretch()
        self.btn_cancel_xfer = QPushButton("取消当前传输")
        self.btn_cancel_xfer.setEnabled(False)
        self.btn_cancel_xfer.clicked.connect(self._on_cancel)
        bottom.addWidget(self.btn_cancel_xfer)
        main_l.addLayout(bottom)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_queue)
        self._timer.start(80)

    def get_host_devices(self) -> list[dict[str, Any]]:
        return self._devices

    def refresh_all_host_combos(self) -> None:
        self.pane_out.refresh_host_combo()
        self.pane_in.refresh_host_combo()

    def _select_host_combo_by_name(self, pane: SidePane, name: str) -> None:
        for i in range(pane.combo_hosts.count()):
            d = pane.combo_hosts.itemData(i, Qt.ItemDataRole.UserRole)
            if isinstance(d, dict) and str(d.get("name", "")) == name:
                pane.combo_hosts.blockSignals(True)
                pane.combo_hosts.setCurrentIndex(i)
                pane.combo_hosts.blockSignals(False)
                pane._on_host_preset_changed(i)
                return

    def save_host_preset_from_pane(self, pane: SidePane) -> None:
        if pane.local_checked():
            QMessageBox.information(self, "提示", "本机模式下无需保存 SSH 预设。")
            return
        host = pane.ed_host.text().strip()
        user = pane.ed_user.text().strip()
        if not host or not user:
            QMessageBox.warning(self, "提示", "请先填写主机与用户名再保存。")
            return
        name, ok = QInputDialog.getText(
            self, "保存预设", "预设名称（用于下拉列表显示）:"
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        try:
            port = int(pane.ed_port.text().strip() or "22")
        except ValueError:
            QMessageBox.warning(self, "提示", "端口必须是数字。")
            return
        pw = pane.ed_pass.text()
        key = pane.ed_key.text().strip()
        if not pw and not key:
            QMessageBox.warning(self, "提示", "请至少填写密码或私钥路径后再保存。")
            return
        path_save = pane.ed_path.text().strip().replace("\\", "/")
        entry: dict[str, Any] = {
            "name": name,
            "host": host,
            "port": port,
            "user": user,
            "password": pw,
            "key_path": key,
            "default_path": path_save,
        }
        for i, d in enumerate(self._devices):
            if str(d.get("name", "")) == name:
                self._devices[i] = entry
                break
        else:
            self._devices.append(entry)
        try:
            save_host_devices(self._devices)
        except OSError as e:
            QMessageBox.critical(self, "保存失败", str(e))
            return
        self.refresh_all_host_combos()
        self._select_host_combo_by_name(pane, name)
        self._append_log(f"已保存 SSH 预设「{name}」到 {hosts_config_path()}")

    def _job_row(self, job_id: str) -> int | None:
        for row in range(self.table_jobs.rowCount()):
            it = self.table_jobs.item(row, XFER_JOB_COL_NAME)
            if it and it.data(ROLE_JOB_ID) == job_id:
                return row
        return None

    def _xfer_batch_route_log(self, batch_id: str) -> str:
        """从表格元数据取该批次的源→目标端点描述（用于汇总日志）。"""
        for row in range(self.table_jobs.rowCount()):
            it0 = self.table_jobs.item(row, XFER_JOB_COL_NAME)
            if it0 and str(it0.data(ROLE_BATCH_ID)) == str(batch_id):
                ls = it0.data(ROLE_XFER_LOG_SRC)
                ld = it0.data(ROLE_XFER_LOG_DST)
                if ls is not None and ld is not None:
                    return f"{ls} → {ld}"
                break
        return f"批次 {batch_id}"

    def _append_log(self, line: str) -> None:
        self.txt_log.appendPlainText(f"{_log_timestamp()} {line}")
        sb = self.txt_log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _log_xfer_job_state_line(self, job_id: str, state_text: str) -> None:
        """为单文件传输终态写入传输日志（避免「传输中」刷屏）。"""
        if state_text == "传输中":
            return
        if state_text not in (
            "传输成功",
            "已中止",
            "已取消",
            "已移除",
            "已跳过",
        ) and not state_text.startswith("失败"):
            return
        row = self._job_row(job_id)
        disp = job_id
        route = ""
        if row is not None:
            it = self.table_jobs.item(row, XFER_JOB_COL_NAME)
            if it is not None and it.text():
                disp = it.text()
            if it is not None:
                ls = it.data(ROLE_XFER_LOG_SRC)
                ld = it.data(ROLE_XFER_LOG_DST)
                if ls is not None and ld is not None:
                    route = f"{ls} → {ld}"
        if route:
            self._append_log(f"「{disp}」({job_id}) [{route}] {state_text}")
        else:
            self._append_log(f"「{disp}」({job_id}) {state_text}")

    def show_pane_file_context_menu(self, pane: SidePane, pos) -> None:
        item = pane.tree.itemAt(pos)
        if item is not None:
            if not item.isSelected():
                pane.tree.clearSelection()
                item.setSelected(True)

        paths = pane.selected_paths()
        if not paths:
            return

        menu = QMenu(self)
        sty = self.style()
        act_send = QAction(
            sty.standardIcon(QStyle.StandardPixmap.SP_ArrowForward),
            "发送",
            self,
        )
        act_send.triggered.connect(
            lambda _checked=False, p=pane: self._ctx_send_from_pane(p)
        )
        act_del = QAction(
            sty.standardIcon(QStyle.StandardPixmap.SP_TrashIcon),
            "删除",
            self,
        )
        sel = list(paths)
        act_del.triggered.connect(
            lambda _checked=False, p=pane, s=sel: self._ctx_delete_paths(p, s)
        )
        act_ren = QAction(
            QIcon.fromTheme("edit-rename", sty.standardIcon(QStyle.StandardPixmap.SP_FileDialogInfoView)),
            "重命名",
            self,
        )
        act_ren.setEnabled(len(paths) == 1)
        old_p, is_dir0 = paths[0]
        act_ren.triggered.connect(
            lambda _checked=False,
            p=pane,
            op=old_p,
            isd=is_dir0: self._ctx_rename_in_pane(p, op, isd),
        )
        menu.addAction(act_send)
        menu.addAction(act_del)
        menu.addAction(act_ren)
        menu.exec(pane.tree.viewport().mapToGlobal(pos))

    def _ctx_send_from_pane(self, pane: SidePane) -> None:
        paths = pane.selected_paths()
        if not paths:
            return
        if pane is self.pane_out:
            direction = "out_to_in"
            dst = self.pane_in
        elif pane is self.pane_in:
            direction = "in_to_out"
            dst = self.pane_out
        else:
            return
        dst_root = dst.ed_path.text().strip()
        self._enqueue_transfer_paths(
            direction, dst_root, list(paths), log_prefix="右键发送"
        )

    def _ctx_delete_paths(self, pane: SidePane, paths: list[tuple[str, bool]]) -> None:
        if not paths:
            return

        def _ui_basename(p: str) -> str:
            if pane.local_checked():
                return os.path.basename(os.path.normpath(p))
            return posixpath.basename(p)

        n = len(paths)
        preview = ", ".join(_ui_basename(p[0]) for p in paths[:5])
        if n > 5:
            preview += "…"
        ret = QMessageBox.question(
            self,
            "确认删除",
            f"确定彻底删除选中的 {n} 项？\n{preview}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        def work() -> None:
            err: str | None = None
            try:
                if pane.local_checked():
                    for fp, is_dir in paths:
                        p = os.path.normpath(os.path.expanduser(fp))
                        if is_dir:
                            shutil.rmtree(p)
                        else:
                            os.unlink(p)
                else:
                    with self._io_lock:
                        sftp = pane.sftp
                        if not sftp:
                            err = "未连接，无法删除"
                        else:
                            for fp, _is_dir in paths:
                                _sftp_remove_recursive(sftp, fp)
            except Exception as e:
                err = str(e)
            self._msg_queue.put(("pane_file_ops_done", (pane, err)))

        threading.Thread(target=work, daemon=True).start()

    def _ctx_rename_in_pane(self, pane: SidePane, old_path: str, _is_dir: bool) -> None:
        if pane.local_checked():
            old_norm = os.path.normpath(os.path.expanduser(old_path))
            parent = os.path.dirname(old_norm)
            base = os.path.basename(old_norm)
        else:
            parent = posixpath.dirname(old_path)
            base = posixpath.basename(old_path)

        new_name, ok = QInputDialog.getText(self, "重命名", "新名称：", text=base)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name in (".", ".."):
            QMessageBox.warning(self, "重命名", "名称无效")
            return
        if any(sep in new_name for sep in ("/", "\\")):
            QMessageBox.warning(self, "重命名", "名称中不能包含路径分隔符")
            return

        if pane.local_checked():
            new_path = os.path.normpath(os.path.join(parent, new_name))
        else:
            new_path = posixpath.join(parent, new_name)

        if new_path == (
            old_norm if pane.local_checked() else old_path
        ):
            return

        if pane.local_checked():
            if os.path.exists(new_path):
                QMessageBox.critical(self, "重命名", f"已存在：{new_path}")
                return
        else:
            if not pane.sftp:
                QMessageBox.critical(self, "重命名", "未连接")
                return
            try:
                with self._io_lock:
                    pane.sftp.stat(new_path)
                QMessageBox.critical(self, "重命名", f"已存在：{new_path}")
                return
            except OSError:
                pass

        def work() -> None:
            err: str | None = None
            try:
                if pane.local_checked():
                    os.rename(os.path.expanduser(old_path), new_path)
                else:
                    with self._io_lock:
                        sf = pane.sftp
                        if not sf:
                            raise RuntimeError("未连接")
                        sf.rename(old_path, new_path)
            except Exception as e:
                err = str(e)
            self._msg_queue.put(("pane_file_ops_done", (pane, err)))

        threading.Thread(target=work, daemon=True).start()

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self._msg_queue.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "path_set":
                    pane, path = payload
                    pane.ed_path.setText(path)
                elif kind == "list_ready":
                    pane, entries = payload
                    self._fill_tree(pane, entries)
                elif kind == "batch_prepared":
                    batch_id, job_rows, src, dst, sl, dl = payload
                    route_txt = f"{src.label} → {dst.label}"
                    log_src = _xfer_log_endpoint(src)
                    log_dst = _xfer_log_endpoint(dst)
                    for jid, name, fsz, sp, dp in job_rows:
                        row = self.table_jobs.rowCount()
                        self.table_jobs.insertRow(row)
                        it0 = QTableWidgetItem(name)
                        it0.setData(ROLE_JOB_ID, jid)
                        it0.setData(ROLE_BATCH_ID, batch_id)
                        it0.setData(ROLE_XFER_DIR, "out_to_in" if src is self.pane_out else "in_to_out")
                        it0.setData(ROLE_XFER_SP, sp)
                        it0.setData(ROLE_XFER_DP, dp)
                        it0.setData(ROLE_XFER_LOG_SRC, log_src)
                        it0.setData(ROLE_XFER_LOG_DST, log_dst)
                        it0.setToolTip(sp)
                        self.table_jobs.setItem(row, XFER_JOB_COL_NAME, it0)
                        self.table_jobs.setItem(
                            row, XFER_JOB_COL_ROUTE, QTableWidgetItem(route_txt)
                        )
                        self.table_jobs.setItem(
                            row, XFER_JOB_COL_SIZE, QTableWidgetItem(_format_size(fsz))
                        )
                        self.table_jobs.setItem(
                            row, XFER_JOB_COL_DONE, QTableWidgetItem(_format_size(0))
                        )
                        self.table_jobs.setItem(
                            row, XFER_JOB_COL_TIME, QTableWidgetItem("00:00:00")
                        )
                        self.table_jobs.setItem(
                            row, XFER_JOB_COL_SPEED, QTableWidgetItem("—")
                        )
                        self._attach_row_progress_bar(row)
                        self.table_jobs.setItem(
                            row, XFER_JOB_COL_STATE, QTableWidgetItem("等待传输")
                        )
                        self._attach_row_action_button(row, jid)
                    self._xfer_queue.put((batch_id, job_rows, src, dst, sl, dl))
                    self._append_log(
                        f"批次 {batch_id} 已就绪：{log_src} → {log_dst}，共 {len(job_rows)} 个文件待传"
                    )
                elif kind == "xfer_prep_aborted":
                    idle = self._xfer_decrement_pending()
                    if idle:
                        self._msg_queue.put(("xfer_idle", None))
                elif kind == "batch_queue_cancelled":
                    self._cancel_waiting_rows_for_batch(str(payload))
                elif kind == "cancel_batch_waiting_rows":
                    self._cancel_waiting_rows_for_batch(str(payload))
                elif kind == "skip_batch_waiting":
                    self._skip_remaining_waiting_in_batch(str(payload))
                elif kind == "xfer_progress":
                    (
                        job_id,
                        pct_o,
                        pct_f,
                        done_s,
                        speed_s,
                        summary,
                        elapsed_s,
                    ) = payload
                    row = self._job_row(job_id)
                    if row is not None:
                        self.table_jobs.setItem(
                            row, XFER_JOB_COL_DONE, QTableWidgetItem(done_s)
                        )
                        self.table_jobs.setItem(
                            row, XFER_JOB_COL_TIME, QTableWidgetItem(elapsed_s)
                        )
                        self.table_jobs.setItem(
                            row, XFER_JOB_COL_SPEED, QTableWidgetItem(speed_s)
                        )
                        pb = self._row_progress_bar(row)
                        if pb is not None:
                            pb.setValue(int(pct_f))
                    self.lbl_job_summary.setText(summary)
                elif kind == "xfer_job_state":
                    job_id, state_text = payload
                    row = self._job_row(job_id)
                    if row is not None:
                        self.table_jobs.setItem(
                            row, XFER_JOB_COL_STATE, QTableWidgetItem(state_text)
                        )
                        self._sync_action_button_for_job(job_id)
                        self._sync_progress_bar_for_job(job_id)
                    self._log_xfer_job_state_line(job_id, state_text)
                elif kind == "xfer_batch_error":
                    err = str(payload)
                    QMessageBox.critical(self, "传输失败", err)
                elif kind == "xfer_idle":
                    with self._xfer_pending_lock:
                        still_pending = self._xfer_pending_batches
                    if still_pending == 0:
                        self._set_xfer_busy(False)
                        self.lbl_job_summary.setText("就绪")
                        self._append_log("全部传输批次已结束，已刷新两侧目录列表")
                        self.refresh_list(self.pane_out)
                        self.refresh_list(self.pane_in)
                elif kind == "connect_done":
                    pane, err, remote_home = payload
                    ep = _xfer_log_endpoint(pane)
                    if err:
                        self._append_log(f"[{pane.label}] SSH 连接失败 {ep}：{err}")
                        QMessageBox.critical(self, "连接失败", err)
                        pane.lbl_status.setText("状态：连接失败")
                        pane.lbl_status.setStyleSheet("color: red;")
                    else:
                        self._append_log(
                            f"[{pane.label}] SSH 已连接 {ep}（远端主目录：{remote_home or '—'}）"
                        )
                        pane.lbl_status.setText("状态：已连接")
                        pane.lbl_status.setStyleSheet("color: green;")
                        self._apply_remote_path_after_connect(pane, remote_home)
                        self.refresh_list(pane)
                elif kind == "pane_file_ops_done":
                    pane, err = payload
                    if err:
                        QMessageBox.critical(self, "文件操作", err)
                        self._append_log(f"[{pane.label}] 操作失败: {err}")
                    else:
                        self._append_log(f"[{pane.label}] 操作已完成")
                    self.refresh_list(pane)
        except queue.Empty:
            pass

    def _set_xfer_busy(self, busy: bool) -> None:
        self.btn_cancel_xfer.setEnabled(busy)

    def _xfer_decrement_pending(self) -> bool:
        with self._xfer_pending_lock:
            self._xfer_pending_batches -= 1
            return self._xfer_pending_batches == 0

    def _cancel_waiting_rows_for_batch(self, batch_id: str) -> None:
        n = 0
        for row in range(self.table_jobs.rowCount()):
            it0 = self.table_jobs.item(row, XFER_JOB_COL_NAME)
            st = self.table_jobs.item(row, XFER_JOB_COL_STATE)
            if (
                it0
                and st
                and str(it0.data(ROLE_BATCH_ID)) == str(batch_id)
                and st.text() == "等待传输"
            ):
                st.setText("已取消")
                n += 1
        self._sync_action_buttons_for_batch(batch_id)
        self._sync_progress_bars_for_batch(batch_id)
        if n:
            br = self._xfer_batch_route_log(batch_id)
            self._append_log(
                f"{br}：{n} 个等待中的任务已标记为「已取消」（取消传输或批次中断）"
            )

    def _on_cancel(self) -> None:
        self._cancel_event.set()
        drained = 0
        while True:
            try:
                batch = self._xfer_queue.get_nowait()
                with self._xfer_pending_lock:
                    self._xfer_pending_batches -= 1
                drained += 1
                if batch and len(batch) > 0 and isinstance(batch[0], str):
                    self._msg_queue.put(("batch_queue_cancelled", batch[0]))
            except queue.Empty:
                break
        if drained:
            self._msg_queue.put(
                (
                    "log",
                    f"已取消排队中的 {drained} 个传输批次；当前批次在本次读写结束后停止",
                )
            )
        else:
            self._msg_queue.put(("log", "正在取消当前传输（当前读写结束后尽快停止）…"))

    def _attach_row_action_button(self, row: int, job_id: str) -> None:
        w = QWidget()
        hl = QHBoxLayout(w)
        hl.setContentsMargins(2, 0, 2, 0)
        btn = QPushButton("删除")
        btn.setMinimumWidth(52)
        btn.setMaximumWidth(72)
        btn.setProperty("job_id", job_id)
        hl.addWidget(btn)
        hl.addStretch()
        self.table_jobs.setCellWidget(row, XFER_JOB_COL_ACTION, w)
        self._sync_action_button_for_job(job_id)

    def _row_progress_bar(self, row: int) -> QProgressBar | None:
        w = self.table_jobs.cellWidget(row, XFER_JOB_COL_PROGRESS)
        if w is None:
            return None
        return w.findChild(QProgressBar)

    def _attach_row_progress_bar(self, row: int) -> None:
        w = QWidget()
        hl = QHBoxLayout(w)
        hl.setContentsMargins(4, 2, 4, 2)
        pb = QProgressBar()
        pb.setRange(0, 100)
        pb.setValue(0)
        pb.setTextVisible(True)
        pb.setFormat("%p%")
        pb.setFixedHeight(16)
        pb.setEnabled(False)
        pb.setStyleSheet(
            "QProgressBar{border:1px solid #bbb;border-radius:3px;background:#f5f5f5;}"
            "QProgressBar::chunk{background-color:#ff9800;border-radius:2px;}"
        )
        hl.addWidget(pb)
        self.table_jobs.setCellWidget(row, XFER_JOB_COL_PROGRESS, w)

    def _sync_progress_bar_for_job(self, job_id: str) -> None:
        row = self._job_row(job_id)
        if row is None:
            return
        pb = self._row_progress_bar(row)
        if pb is None:
            return
        st = self.table_jobs.item(row, XFER_JOB_COL_STATE)
        if not st:
            return
        txt = st.text()
        if txt == "等待传输":
            pb.setEnabled(False)
            pb.setValue(0)
        elif txt == "传输中":
            pb.setEnabled(True)
        elif txt == "传输成功":
            pb.setEnabled(True)
            pb.setValue(100)
        else:
            pb.setEnabled(False)

    def _sync_progress_bars_for_batch(self, batch_id: str) -> None:
        for row in range(self.table_jobs.rowCount()):
            it0 = self.table_jobs.item(row, XFER_JOB_COL_NAME)
            if it0 and str(it0.data(ROLE_BATCH_ID)) == str(batch_id):
                jid = it0.data(ROLE_JOB_ID)
                if jid:
                    self._sync_progress_bar_for_job(str(jid))

    def _sync_action_button_for_job(self, job_id: str) -> None:
        row = self._job_row(job_id)
        if row is None:
            return
        w = self.table_jobs.cellWidget(row, XFER_JOB_COL_ACTION)
        if w is None:
            return
        btn = w.findChild(QPushButton)
        if btn is None:
            return
        st = self.table_jobs.item(row, XFER_JOB_COL_STATE)
        if not st:
            btn.setEnabled(False)
            return
        txt = st.text()
        try:
            btn.clicked.disconnect()
        except TypeError:
            pass
        if txt in ("已中止", "已取消"):
            btn.setText("重传")
            btn.setEnabled(True)
            btn.clicked.connect(
                lambda _checked=False, j=job_id: self._on_retransmit_job(j)
            )
        elif txt in ("等待传输", "传输中"):
            btn.setText("删除")
            btn.setEnabled(True)
            btn.clicked.connect(
                lambda _checked=False, j=job_id: self._on_remove_transfer_job(j)
            )
        else:
            btn.setText("删除")
            btn.setEnabled(False)

    def _sync_action_buttons_for_batch(self, batch_id: str) -> None:
        for row in range(self.table_jobs.rowCount()):
            it0 = self.table_jobs.item(row, XFER_JOB_COL_NAME)
            if it0 and str(it0.data(ROLE_BATCH_ID)) == str(batch_id):
                jid = it0.data(ROLE_JOB_ID)
                if jid:
                    self._sync_action_button_for_job(str(jid))

    def _on_remove_transfer_job(self, job_id: str) -> None:
        row = self._job_row(job_id)
        if row is None:
            return
        st_it = self.table_jobs.item(row, XFER_JOB_COL_STATE)
        if not st_it:
            return
        st = st_it.text()
        if st == "等待传输":
            with self._xfer_skip_jobs_lock:
                self._xfer_skip_job_ids.add(job_id)
            self.table_jobs.removeCellWidget(row, XFER_JOB_COL_PROGRESS)
            self.table_jobs.removeCellWidget(row, XFER_JOB_COL_ACTION)
            self.table_jobs.removeRow(row)
            self._append_log(f"已从队列移除任务 {job_id}")
        elif st == "传输中":
            with self._xfer_abort_lock:
                self._xfer_abort_job_id = job_id
            self._append_log(f"正在中止传输任务 {job_id}…")

    def _on_retransmit_job(self, job_id: str) -> None:
        row = self._job_row(job_id)
        if row is None:
            return
        it0 = self.table_jobs.item(row, XFER_JOB_COL_NAME)
        st_it = self.table_jobs.item(row, XFER_JOB_COL_STATE)
        if not it0 or not st_it:
            return
        if st_it.text() not in ("已中止", "已取消"):
            return
        direction = str(it0.data(ROLE_XFER_DIR) or "")
        sp = str(it0.data(ROLE_XFER_SP) or "").strip()
        dp = str(it0.data(ROLE_XFER_DP) or "").strip()
        if not sp or not dp or direction not in ("out_to_in", "in_to_out"):
            QMessageBox.warning(
                self, "重传", "无法读取该任务的源/目标路径记录，无法重传。"
            )
            return
        dst = self.pane_in if direction == "out_to_in" else self.pane_out
        dst_use_local = bool(dst.local_checked() or dst.is_local)
        if dst_use_local:
            dn = os.path.normpath(os.path.expanduser(dp.replace("/", os.sep)))
            par = os.path.dirname(dn)
            dst_root = par if par else dn
        else:
            dn = posixpath.normpath(dp.replace("\\", "/"))
            dst_root = posixpath.dirname(dn) or "/"

        if not self._enqueue_transfer_paths(
            direction, dst_root, [(sp, False)], log_prefix="重传"
        ):
            return
        self.table_jobs.removeCellWidget(row, XFER_JOB_COL_PROGRESS)
        self.table_jobs.removeCellWidget(row, XFER_JOB_COL_ACTION)
        self.table_jobs.removeRow(row)

    def _clear_done_jobs(self) -> None:
        for row in range(self.table_jobs.rowCount() - 1, -1, -1):
            it = self.table_jobs.item(row, XFER_JOB_COL_STATE)
            if it:
                st = it.text()
                if (
                    "成功" in st
                    or "失败" in st
                    or "取消" in st
                    or st == "已移除"
                    or st == "已中止"
                    or st == "已跳过"
                ):
                    self.table_jobs.removeCellWidget(row, XFER_JOB_COL_PROGRESS)
                    self.table_jobs.removeCellWidget(row, XFER_JOB_COL_ACTION)
                    self.table_jobs.removeRow(row)

    def _skip_remaining_waiting_in_batch(self, batch_id: str) -> None:
        n = 0
        for row in range(self.table_jobs.rowCount()):
            it0 = self.table_jobs.item(row, XFER_JOB_COL_NAME)
            st = self.table_jobs.item(row, XFER_JOB_COL_STATE)
            if (
                it0
                and st
                and str(it0.data(ROLE_BATCH_ID)) == str(batch_id)
                and st.text() == "等待传输"
            ):
                st.setText("已跳过")
                n += 1
        self._sync_action_buttons_for_batch(batch_id)
        self._sync_progress_bars_for_batch(batch_id)
        if n:
            br = self._xfer_batch_route_log(batch_id)
            self._append_log(
                f"{br}：{n} 个等待中的任务已标记为「已跳过」（因同批次前置任务失败）"
            )

    def _fill_tree(
        self, pane: SidePane, entries: list[tuple[str, bool, int, float, str]]
    ) -> None:
        pane.tree.clear()
        for path, is_dir, size, mtime, kind in sorted(
            entries, key=lambda x: (not x[1], str(x[0]).lower())
        ):
            name = os.path.basename(str(path).rstrip("/")) or str(path)
            sz = "—" if is_dir else _format_size(size)
            mt = "—" if is_dir else _mtime_str(mtime)
            item = QTreeWidgetItem([name, sz, kind, mt])
            item.setData(0, ROLE_PATH, path)
            item.setData(0, ROLE_IS_DIR, is_dir)
            pane.tree.addTopLevelItem(item)

    def refresh_list(self, pane: SidePane) -> None:
        main = self
        path0 = pane.ed_path.text().strip()
        use_local = pane.local_checked()

        def work() -> None:
            err: str | None = None
            entries: list[tuple[str, bool, int, float, str]] = []
            path = path0
            try:
                if use_local:
                    if not path:
                        path = os.path.expanduser("~")
                        main._msg_queue.put(("path_set", (pane, path)))
                    if not os.path.isdir(path):
                        err = f"本地路径不是目录: {path}"
                    else:
                        for name in os.listdir(path):
                            if name in (".", ".."):
                                continue
                            fp = os.path.join(path, name)
                            try:
                                st = os.stat(fp)
                                is_dir = stat.S_ISDIR(st.st_mode)
                                entries.append(
                                    (
                                        fp,
                                        is_dir,
                                        0 if is_dir else st.st_size,
                                        st.st_mtime,
                                        "文件夹" if is_dir else "文件",
                                    )
                                )
                            except OSError:
                                continue
                else:
                    with main._io_lock:
                        sftp = pane.sftp
                    if not sftp:
                        err = "未连接"
                    else:
                        if not path:
                            path = "/"
                            main._msg_queue.put(("path_set", (pane, path)))
                        for attr in sftp.listdir_attr(path):
                            name = _decode_sftp_name(attr.filename)
                            if name in (".", ".."):
                                continue
                            full = posixpath.join(path, name)
                            mode = _resolve_remote_mode(sftp, full, attr)
                            if stat.S_ISLNK(mode):
                                try:
                                    st = sftp.stat(full)
                                    mode = int(st.st_mode)
                                except OSError:
                                    continue
                            is_dir = stat.S_ISDIR(mode)
                            sz = 0 if is_dir else int(attr.st_size or 0)
                            if not is_dir and sz == 0:
                                try:
                                    sz = int(sftp.stat(full).st_size)
                                except OSError:
                                    pass
                            mtime = float(attr.st_mtime or 0)
                            entries.append(
                                (
                                    full,
                                    is_dir,
                                    sz,
                                    mtime,
                                    "文件夹" if is_dir else "文件",
                                )
                            )
            except Exception as e:
                err = str(e)
            if err:
                main._msg_queue.put(("log", f"[{pane.label}] 列出目录失败: {err}"))
            main._msg_queue.put(("list_ready", (pane, entries)))

        threading.Thread(target=work, daemon=True).start()

    def _parse_conn(self, pane: SidePane) -> tuple[str, int, str, str | None, str | None] | None:
        if pane.local_checked():
            return None
        host = pane.ed_host.text().strip()
        user = pane.ed_user.text().strip()
        try:
            port = int(pane.ed_port.text().strip() or "22")
        except ValueError:
            QMessageBox.critical(self, "参数错误", f"{pane.label} 端口必须是整数")
            return None
        pw = pane.ed_pass.text() or None
        key = pane.ed_key.text().strip() or None
        if not host or not user:
            QMessageBox.critical(self, "参数错误", f"{pane.label} 请填写主机与用户名")
            return None
        if not pw and not key:
            QMessageBox.critical(
                "认证方式",
                f"{pane.label} 请填写密码或私钥路径（至少一项）",
            )
            return None
        return host, port, user, pw, key

    def _connect_ssh(
        self, host: str, port: int, user: str, password: str | None, key_path: str | None
    ) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kw: dict = {
            "hostname": host,
            "port": port,
            "username": user,
            "timeout": 30,
            "allow_agent": True,
            "look_for_keys": True,
        }
        if password:
            kw["password"] = password
        if key_path:
            kw["key_filename"] = [key_path]
        client.connect(**kw)
        return client

    def _apply_remote_path_after_connect(
        self, pane: SidePane, remote_home: str | None
    ) -> None:
        """连接成功后：优先预设 default_path；否则沿用已填路径；若仍为空则用远端 $HOME。"""
        preset = pane.active_preset_device()
        cfg = str((preset or {}).get("default_path", "")).strip()
        rh = (
            remote_home
            if (remote_home and str(remote_home).strip().startswith("/"))
            else None
        )
        home_base = rh or "/"

        if cfg:
            final = _expand_remote_path_tilde(cfg, rh)
        else:
            typed = pane.ed_path.text().strip().replace("\\", "/")
            if typed:
                final = _expand_remote_path_tilde(typed, rh)
            else:
                final = home_base
        fs = str(final).strip()
        if not fs.startswith("/"):
            fs = "/"
        pane.ed_path.setText(posixpath.normpath(fs))

    def connect_side(self, pane: SidePane) -> None:
        if pane.local_checked():
            pane.is_local = True
            pane.lbl_status.setText("状态：本地模式")
            pane.lbl_status.setStyleSheet("color: green;")
            cur = pane.ed_path.text().strip() or os.path.expanduser("~")
            self._append_log(
                f"[{pane.label}] 本机已连接（连接按钮），浏览路径：{cur}"
            )
            self.refresh_list(pane)
            return

        parsed = self._parse_conn(pane)
        if not parsed:
            return
        host, port, user, pw, key = parsed

        def work() -> None:
            try:
                with self._io_lock:
                    pane.close_remote()
                    ssh = self._connect_ssh(host, port, user, pw, key)
                    sftp = ssh.open_sftp()
                    pane.ssh = ssh
                    pane.sftp = sftp
                self._msg_queue.put(("log", f"[{pane.label}] 已连接 {user}@{host}:{port}"))
                rh = _ssh_remote_home(ssh)
                self._msg_queue.put(("log", f"[{pane.label}] 远端主目录: {rh}"))
                self._msg_queue.put(("connect_done", (pane, None, rh)))
            except Exception as e:
                self._msg_queue.put(("connect_done", (pane, str(e), None)))

        pane.lbl_status.setText("状态：连接中…")
        pane.lbl_status.setStyleSheet("color: blue;")
        threading.Thread(target=work, daemon=True).start()

    def disconnect_side(self, pane: SidePane) -> None:
        was_local = pane.local_checked()
        had_ssh = pane.sftp is not None or pane.ssh is not None
        ep_ssh = _xfer_log_endpoint(pane) if had_ssh else ""
        with self._io_lock:
            pane.close_remote()
        pane.is_local = pane.local_checked()
        if pane.is_local:
            pane.lbl_status.setText("状态：本地模式")
            pane.lbl_status.setStyleSheet("color: green;")
            if was_local:
                cur = pane.ed_path.text().strip() or "—"
                self._append_log(
                    f"[{pane.label}] 本机已断开（仍为本地模式，浏览路径：{cur}）"
                )
        else:
            pane.lbl_status.setText("状态：未连接")
            pane.lbl_status.setStyleSheet("color: gray;")
            if had_ssh and ep_ssh and ep_ssh != "本机":
                self._append_log(f"[{pane.label}] SSH 已断开 {ep_ssh}")

    def go_up(self, pane: SidePane) -> None:
        cur = pane.ed_path.text().strip()
        if pane.local_checked():
            parent = os.path.dirname(cur.rstrip(os.sep)) or cur
            pane.ed_path.setText(parent)
            self.refresh_list(pane)
            return
        if not pane.sftp:
            return
        parent = posixpath.dirname(cur.rstrip("/")) or "/"
        pane.ed_path.setText(parent)
        self.refresh_list(pane)

    def _ensure_xfer_consumer(self) -> None:
        if self._xfer_consumer_thread and self._xfer_consumer_thread.is_alive():
            return
        self._xfer_consumer_thread = threading.Thread(
            target=self._xfer_consumer_loop, daemon=True
        )
        self._xfer_consumer_thread.start()

    def _xfer_consumer_loop(self) -> None:
        while True:
            batch = self._xfer_queue.get()
            batch_id, job_rows, src, dst, sl, dl = batch
            try:
                self._cancel_event.clear()
                with self._xfer_abort_lock:
                    self._xfer_abort_job_id = None
                self._run_prepared_batch(batch_id, job_rows, src, dst, sl, dl)
            except InterruptedError:
                log_src = _xfer_log_endpoint(src)
                log_dst = _xfer_log_endpoint(dst)
                self._msg_queue.put(
                    (
                        "log",
                        f"当前传输批次已中断（{log_src} → {log_dst}）：取消或操作中止",
                    )
                )
            except Exception as e:
                log_src = _xfer_log_endpoint(src)
                log_dst = _xfer_log_endpoint(dst)
                self._msg_queue.put(
                    ("log", f"传输批次失败（{log_src} → {log_dst}）：{e}")
                )
                self._msg_queue.put(("xfer_batch_error", str(e)))
                self._msg_queue.put(("skip_batch_waiting", batch_id))
            finally:
                with self._xfer_pending_lock:
                    self._xfer_pending_batches -= 1
                    idle = self._xfer_pending_batches == 0
                if idle:
                    self._msg_queue.put(("xfer_idle", None))

    def _enqueue_transfer_paths(
        self,
        direction: str,
        dst_dir: str,
        paths: list[tuple[str, bool]],
        *,
        log_prefix: str = "",
    ) -> bool:
        if direction == "out_to_in":
            src, dst = self.pane_out, self.pane_in
        else:
            src, dst = self.pane_in, self.pane_out

        if not paths:
            return False

        dst_work = dst_dir.strip()
        if not dst_work:
            QMessageBox.critical(self, "目标路径", "请输入目标侧当前目录")
            return False

        if not dst.local_checked() and not dst.sftp:
            QMessageBox.critical(self, "目标", "输入端未连接（或启用本地模式）")
            return False
        if not src.local_checked() and not src.sftp:
            QMessageBox.critical(self, "来源", "输出端未连接（或启用本地模式）")
            return False

        if not dst.local_checked():
            dst_work = dst_work.replace("\\", "/")

        src_use_local = bool(src.local_checked() or src.is_local)
        dst_use_local = bool(dst.local_checked() or dst.is_local)

        with self._xfer_pending_lock:
            self._xfer_pending_batches += 1
            n_pending = self._xfer_pending_batches
        self._xfer_batch_seq += 1
        batch_id = f"b{self._xfer_batch_seq}"
        self._set_xfer_busy(True)
        lead = f"{log_prefix}：" if log_prefix else ""
        log_src = _xfer_log_endpoint(src)
        log_dst = _xfer_log_endpoint(dst)
        self._msg_queue.put(
            (
                "log",
                f"{lead}已加入传输队列：{log_src} → {log_dst}，{len(paths)} 项"
                f"（当前共 {n_pending} 个批次）；正在扫描文件夹并列出待传文件…",
            )
        )
        threading.Thread(
            target=self._prep_batch_worker,
            args=(
                batch_id,
                src,
                dst,
                dst_work,
                list(paths),
                src_use_local,
                dst_use_local,
            ),
            daemon=True,
        ).start()
        self._ensure_xfer_consumer()
        return True

    def start_transfer(self, direction: str) -> None:
        if direction == "out_to_in":
            src, dst = self.pane_out, self.pane_in
        else:
            src, dst = self.pane_in, self.pane_out

        paths = src.selected_paths()
        if not paths:
            QMessageBox.warning(self, "提示", "请先在来源侧选择要传输的文件或文件夹")
            return

        dst_root = dst.ed_path.text().strip()
        self._enqueue_transfer_paths(direction, dst_root, list(paths))

    def _build_file_jobs(
        self,
        src: SidePane,
        dst: SidePane,
        dst_dir: str,
        paths: list[tuple[str, bool]],
        src_use_local: bool,
        dst_use_local: bool,
    ) -> list[tuple[str, str, int]]:
        file_jobs: list[tuple[str, str, int]] = []
        for src_path, is_dir in paths:
            if is_dir:
                for fp, dest_rel, sz in self._expand_dir(
                    src, src_path, src_use_local
                ):
                    dest = self._join_dest(
                        dst, dst_dir, dest_rel, dst_use_local
                    )
                    file_jobs.append((fp, dest, sz))
            else:
                sz = self._file_size(src, src_path, src_use_local)
                name = os.path.basename(src_path)
                dest = self._join_dest(dst, dst_dir, name, dst_use_local)
                file_jobs.append((src_path, dest, sz))
        return file_jobs

    def _prep_batch_worker(
        self,
        batch_id: str,
        src: SidePane,
        dst: SidePane,
        dst_dir: str,
        paths: list[tuple[str, bool]],
        src_use_local: bool,
        dst_use_local: bool,
    ) -> None:
        try:
            file_jobs = self._build_file_jobs(
                src, dst, dst_dir, paths, src_use_local, dst_use_local
            )
            if not file_jobs:
                self._msg_queue.put(("log", "没有可传输的文件（目录可能为空）"))
                self._msg_queue.put(("xfer_prep_aborted", batch_id))
                return
            job_rows: list[tuple[str, str, int, str, str]] = []
            with self._job_seq_lock:
                for sp, dp, fsz in file_jobs:
                    self._job_seq += 1
                    jid = f"j{self._job_seq}"
                    job_rows.append((jid, os.path.basename(sp), fsz, sp, dp))
            self._msg_queue.put(
                ("batch_prepared", (batch_id, job_rows, src, dst, src_use_local, dst_use_local))
            )
        except Exception as e:
            self._msg_queue.put(("log", f"准备传输列表失败: {e}"))
            self._msg_queue.put(("xfer_prep_aborted", batch_id))

    def _run_prepared_batch(
        self,
        batch_id: str,
        job_rows: list[tuple[str, str, int, str, str]],
        src: SidePane,
        dst: SidePane,
        src_use_local: bool,
        dst_use_local: bool,
    ) -> None:
        total_bytes = sum(t[2] for t in job_rows)
        if total_bytes < 1:
            total_bytes = 1
        done_bytes = 0
        nfiles = len(job_rows)
        batch_t0 = time.monotonic()

        try:
            for idx, (job_id, _name, fsz, sp, dp) in enumerate(job_rows, start=1):
                if self._cancel_event.is_set():
                    raise InterruptedError("已取消")
                with self._xfer_skip_jobs_lock:
                    skipped = job_id in self._xfer_skip_job_ids
                if skipped:
                    with self._xfer_skip_jobs_lock:
                        self._xfer_skip_job_ids.discard(job_id)
                    done_bytes += fsz
                    self._msg_queue.put(("xfer_job_state", (job_id, "已移除")))
                    continue

                self._msg_queue.put(("xfer_job_state", (job_id, "传输中")))
                file_t0 = time.monotonic()

                def on_chunk(sent: int, total: int, _idx: int = idx) -> None:
                    base = done_bytes
                    now = time.monotonic()
                    elapsed = max(now - file_t0, 1e-6)
                    spd = float(sent) / elapsed
                    overall = base + sent
                    pct_o = 100.0 * overall / total_bytes
                    pct_f = 100.0 * sent / max(total, 1)
                    rem = (total_bytes - overall) / spd if spd > 1.0 else -1
                    summary = (
                        f"当前 {_idx}/{nfiles}   总进度 {pct_o:.1f}%   "
                        f"速度 {_format_speed(spd)}   剩余 {_format_eta(rem)}"
                    )
                    elapsed_str = _format_eta(int(now - file_t0))
                    self._msg_queue.put(
                        (
                            "xfer_progress",
                            (
                                job_id,
                                pct_o,
                                pct_f,
                                _format_size(sent),
                                _format_speed(spd),
                                summary,
                                elapsed_str,
                            ),
                        )
                    )

                try:
                    self._copy_one(
                        src,
                        sp,
                        dst,
                        dp,
                        fsz,
                        on_chunk,
                        src_use_local,
                        dst_use_local,
                        transfer_job_id=job_id,
                    )
                except InterruptedError:
                    if self._cancel_event.is_set():
                        self._msg_queue.put(("xfer_job_state", (job_id, "已取消")))
                        raise
                    self._msg_queue.put(("xfer_job_state", (job_id, "已中止")))
                    continue
                except Exception as e:
                    self._msg_queue.put(("xfer_job_state", (job_id, f"失败: {e}")))
                    raise
                t_done = time.monotonic()
                spd_done = fsz / max(t_done - file_t0, 1e-6)
                done_after = done_bytes + fsz
                pct_o_done = 100.0 * done_after / total_bytes
                elapsed_batch = max(t_done - batch_t0, 1e-6)
                rate_batch = done_after / elapsed_batch
                left = total_bytes - done_after
                rem_done = (
                    left / rate_batch
                    if rate_batch > 1.0 and left > 0
                    else (-1 if left > 0 else 0)
                )
                elapsed_done = _format_eta(int(t_done - file_t0))
                self._msg_queue.put(
                    (
                        "xfer_progress",
                        (
                            job_id,
                            pct_o_done,
                            100.0,
                            _format_size(fsz),
                            _format_speed(spd_done),
                            f"当前 {idx}/{nfiles}   总进度 {pct_o_done:.1f}%   "
                            f"速度 {_format_speed(spd_done)}   剩余 {_format_eta(rem_done)}",
                            elapsed_done,
                        ),
                    )
                )
                self._msg_queue.put(("xfer_job_state", (job_id, "传输成功")))
                done_bytes += fsz
        except InterruptedError:
            self._msg_queue.put(("cancel_batch_waiting_rows", batch_id))
            raise

    def _expand_dir(
        self, src: SidePane, root: str, src_use_local: bool
    ) -> list[tuple[str, str, int]]:
        out: list[tuple[str, str, int]] = []
        if src_use_local:
            # 必须用 realpath：abspath 不解析符号链接，walk 可能产生与 root 前缀不一致的路径，
            # relpath 会出现 ".."，拼到远端后变成错误路径 → [Errno 2] No such file。
            root_norm = os.path.realpath(
                os.path.abspath(os.path.expanduser(str(root)))
            )
            base_name = os.path.basename(root_norm.rstrip(os.sep)) or "folder"
            for dirpath, _, filenames in os.walk(root_norm):
                for fn in filenames:
                    fp = os.path.join(dirpath, fn)
                    try:
                        fp_real = os.path.realpath(fp)
                    except OSError:
                        continue
                    try:
                        if os.path.commonpath([root_norm, fp_real]) != root_norm:
                            continue
                    except ValueError:
                        continue
                    rel_file = os.path.relpath(fp_real, root_norm)
                    if rel_file.startswith(".." + os.sep) or rel_file == "..":
                        continue
                    dest_rel = posixpath.join(
                        base_name, rel_file.replace(os.sep, "/")
                    )
                    try:
                        st = os.stat(fp_real)
                    except OSError:
                        continue
                    if not stat.S_ISREG(st.st_mode):
                        continue
                    out.append((fp_real, dest_rel, st.st_size))
        else:
            with self._io_lock:
                if not src.sftp:
                    return out
                sftp = src.sftp
                root_norm = posixpath.normpath(str(root).replace("\\", "/"))
                try:
                    rs = sftp.stat(root_norm)
                    if not stat.S_ISDIR(rs.st_mode):
                        return out
                except OSError:
                    return out
                base_name = os.path.basename(root_norm.rstrip("/")) or "folder"

                def walk(rpath: str, rel: str) -> None:
                    for attr in sftp.listdir_attr(rpath):
                        name = _decode_sftp_name(attr.filename)
                        if name in (".", ".."):
                            continue
                        full = posixpath.join(rpath, name)
                        mode = _resolve_remote_mode(sftp, full, attr)
                        if stat.S_ISLNK(mode):
                            try:
                                st = sftp.stat(full)
                                mode = int(st.st_mode)
                            except OSError:
                                continue
                        if stat.S_ISDIR(mode):
                            nrel = posixpath.join(rel, name) if rel else name
                            walk(full, nrel)
                        elif stat.S_ISREG(mode):
                            sz = int(attr.st_size or 0)
                            if sz == 0:
                                try:
                                    sz = int(sftp.stat(full).st_size)
                                except OSError:
                                    sz = 0
                            rel_file = posixpath.join(rel, name) if rel else name
                            out.append(
                                (full, posixpath.join(base_name, rel_file), sz)
                            )

                walk(root_norm, "")
        return out

    def _file_size(self, src: SidePane, path: str, src_use_local: bool) -> int:
        if src_use_local:
            return os.path.getsize(path)
        with self._io_lock:
            if not src.sftp:
                return 0
            return int(src.sftp.stat(path).st_size)

    def _join_dest(
        self, dst: SidePane, dst_dir: str, rel: str, dst_use_local: bool
    ) -> str:
        rel = rel.lstrip("/").replace("\\", "/")
        if dst_use_local:
            base = os.path.normpath(os.path.expanduser(dst_dir))
            parts = [p for p in rel.split("/") if p and p != "."]
            return os.path.normpath(os.path.join(base, *parts)) if parts else base
        base = posixpath.normpath(dst_dir.rstrip("/") or "/")
        if not rel:
            return base
        return posixpath.normpath(posixpath.join(base, rel))

    def _remote_makedirs(self, sftp: paramiko.SFTPClient, path: str) -> None:
        path = posixpath.normpath(str(path).replace("\\", "/").rstrip("/") or "/")
        if path in ("/", ".", ""):
            return
        cur = ""
        for part in path.split("/"):
            if not part:
                continue
            cur += "/" + part
            try:
                st = sftp.stat(cur)
            except OSError as e:
                if getattr(e, "errno", None) not in (2, None) and "No such file" not in str(
                    e
                ):
                    raise
                try:
                    sftp.mkdir(cur)
                except OSError as e2:
                    try:
                        st2 = sftp.stat(cur)
                        if stat.S_ISDIR(st2.st_mode):
                            continue
                    except OSError:
                        pass
                    raise OSError(
                        f"无法创建远程目录 {cur!r}（权限或上级路径）: {e2}"
                    ) from e2
                continue
            if not stat.S_ISDIR(st.st_mode):
                raise OSError(
                    f"远程路径 {cur!r} 已存在且不是目录，无法写入其下文件"
                )

    def _ensure_local_parent(self, path: str) -> None:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)

    def _copy_one(
        self,
        src: SidePane,
        src_path: str,
        dst: SidePane,
        dst_path: str,
        total: int,
        on_chunk: Callable[[int, int], None],
        src_use_local: bool,
        dst_use_local: bool,
        transfer_job_id: str | None = None,
    ) -> None:
        buf = 256 * 1024
        sl = bool(src_use_local)
        dl = bool(dst_use_local)

        def pump(rf, wf) -> None:
            sent = 0
            if total <= 0:
                on_chunk(0, 1)
                return
            while sent < total:
                if self._cancel_event.is_set():
                    raise InterruptedError("已取消")
                if transfer_job_id:
                    with self._xfer_abort_lock:
                        if self._xfer_abort_job_id == transfer_job_id:
                            self._xfer_abort_job_id = None
                            raise InterruptedError("已中止")
                n = min(buf, total - sent)
                chunk = rf.read(n)
                if not chunk:
                    break
                wf.write(chunk)
                sent += len(chunk)
                on_chunk(sent, total)

        with self._io_lock:
            if self._cancel_event.is_set():
                raise InterruptedError("已取消")
            if sl and dl:
                sp_abs = os.path.normpath(
                    os.path.abspath(os.path.expanduser(str(src_path)))
                )
                dp_abs = os.path.normpath(
                    os.path.abspath(os.path.expanduser(str(dst_path)))
                )
                self._ensure_local_parent(dp_abs)
                if not os.path.isfile(sp_abs):
                    raise FileNotFoundError(
                        f"本地源不是普通文件或不存在: {sp_abs!r}"
                    )
                with open(sp_abs, "rb") as rf, open(dp_abs, "wb") as wf:
                    pump(rf, wf)
                return

            if sl and not dl:
                assert dst.sftp is not None
                sp_abs = os.path.normpath(
                    os.path.abspath(os.path.expanduser(str(src_path)))
                )
                rp = posixpath.normpath(str(dst_path).replace("\\", "/"))
                if not posixpath.isabs(rp):
                    raise OSError(f"远程目标必须是绝对路径: {rp!r}")
                if not os.path.isfile(sp_abs):
                    raise FileNotFoundError(
                        f"本地源不是普通文件或不存在: {sp_abs!r}"
                    )
                self._remote_makedirs(dst.sftp, posixpath.dirname(rp))
                with open(sp_abs, "rb") as rf:
                    with dst.sftp.open(rp, "wb") as wf:
                        pump(rf, wf)
                return

            if not sl and dl:
                assert src.sftp is not None
                sp_r = posixpath.normpath(str(src_path).replace("\\", "/"))
                dp_abs = os.path.normpath(
                    os.path.abspath(os.path.expanduser(str(dst_path)))
                )
                if not posixpath.isabs(sp_r):
                    raise OSError(f"远程源路径必须是绝对路径: {sp_r!r}")
                self._ensure_local_parent(dp_abs)
                with src.sftp.open(sp_r, "rb") as rf:
                    with open(dp_abs, "wb") as wf:
                        pump(rf, wf)
                return

            assert src.sftp is not None and dst.sftp is not None
            sp_r = posixpath.normpath(str(src_path).replace("\\", "/"))
            rp = posixpath.normpath(str(dst_path).replace("\\", "/"))
            if not posixpath.isabs(sp_r) or not posixpath.isabs(rp):
                raise OSError(f"远程路径必须为绝对路径: src={sp_r!r} dst={rp!r}")
            self._remote_makedirs(dst.sftp, posixpath.dirname(rp))
            with src.sftp.open(sp_r, "rb") as rf:
                with dst.sftp.open(rp, "wb") as wf:
                    pump(rf, wf)


def main() -> None:
    app = QApplication([])
    win = MainWindow()
    win.show()
    app.exec()


if __name__ == "__main__":
    main()
