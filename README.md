# LinkFlow

基于 **PySide6（Qt）** 与 **Paramiko** 的桌面工具：在**双栏界面**中连接本机、SSH 主机或两端 SSH，通过 **SFTP** 浏览目录并传输文件；传输列表按**文件粒度**展示进度、速度与耗时。

---

## 功能概览

- **双端**：左右两侧可分别选择「本机」或已保存的 SSH 预设，浏览目录树。
- **传输**：支持本机 ↔ 远程、远程 ↔ 远程等组合（以界面中实际能力为准）；文件夹会展开为多个文件任务行。
- **预设**：常用主机保存在项目根目录的 `hosts.json`（见下文安全说明）。
- **资源**：应用图标位于 `icon/`（如 `link.svg`；Windows 打包图标使用 `link.ico` 时需自备并放入同目录）。

---

## 环境要求

- **Python** 3.10+（推荐 3.11）
- 依赖见 `**requirements.txt`**：
  - `paramiko` — SSH/SFTP
  - `PySide6` — Qt 界面

```bash
pip install -r requirements.txt
```

---

## 从源码运行

在**仓库根目录**执行：

```bash
python3 main.py
```

首次运行会在可执行文件或项目根旁读写 `**hosts.json**`（开发模式下为与 `main.py` 同目录）。

---

## `hosts.json` 说明与安全

文件为 JSON，顶层包含 `**devices**` 数组，每项示例字段：


| 字段                  | 说明                              |
| ------------------- | ------------------------------- |
| `name`              | 显示名称                            |
| `host`              | 主机名或 IP                         |
| `port`              | SSH 端口，默认 22                    |
| `user` / `password` | 用户名与密码（**明文**，请限制文件权限并勿提交到公开仓库） |
| `key_path`          | 可选，私钥路径                         |
| `default_path`      | 可选，连接后默认远端路径                    |


仓库 `**.gitignore`** 中可能忽略 `*.json` 或 `hosts` 相关规则，请自行确认是否将真实凭据纳入版本控制。

---

## 目录结构（与开发相关）

```
LinkFlow/
├── main.py              # 主程序（单文件入口）
├── requirements.txt
├── hosts.json           # 本地预设（勿泄露）
├── icon/                # 图标资源
├── packaging/
│   ├── install_linux.sh    # Linux：PyInstaller onefile + 桌面 .desktop
│   ├── install_windows.bat # Windows：onefile + 桌面 .lnk
│   └── set-desktop-shortcut.ps1  # 供 Windows 批处理调用，创建快捷方式
├── dist/                # PyInstaller 输出目录（构建产物，通常不提交）
└── build/               # PyInstaller 中间文件（通常不提交）
```

---

## 打包与安装脚本

### Linux：`packaging/install_linux.sh`

- 在仓库根执行 **PyInstaller `--onefile --windowed`**，生成 `**dist/LinkFlow**`（单文件可执行）。
- 将 `**icon:icon**` 打入包内，与运行时 `_MEIPASS/icon/` 路径一致。
- 复制根目录 `**hosts.json**` 到 `**dist/**`（若存在）。
- 在 `**$XDG_DESKTOP_DIR**`（一般为 `~/Desktop`）生成 `**LinkFlow.desktop**`，并在有 `**gio**` 时尝试标记为受信任启动项。

使用前请编辑脚本中的 `**PYTHON_PATH**` 为本机 Python 解释器绝对路径。

```bash
bash packaging/install_linux.sh
```

**注意**：请用 `**bash`** 调用，勿用 `**sh**`（`dash` 不支持 `pipefail` / `[[`）；脚本开头会在非 Bash 时尝试自动 `exec` 到 Bash。

### Windows：`packaging/install_windows.bat`

- 使用脚本内 `**DEFAULT_PY**` 或环境变量 `**LINKFLOW_BUILD_PYTHON**` 指向的 `python.exe`。
- 调用 PyInstaller 生成 `**dist\LinkFlow.exe**`，并把 `**icon**` 复制到 `**dist\icon**` 供快捷方式使用。
- 调用同目录下的 `**set-desktop-shortcut.ps1**`，在用户桌面（含 OneDrive 桌面等常见路径）创建 `**LinkFlow.lnk**`。

可在资源管理器中双击 `**packaging\install_windows.bat**`（会先 `cd` 到仓库根再构建）。

### Windows 快捷方式脚本

`**packaging/set-desktop-shortcut.ps1**` 参数 `**-AppDir**` 指向包含 `**LinkFlow.exe**` 的目录（上述流程中为 `**dist**`）。可单独调用以修复快捷方式。

---

## 常见问题

- **Linux 双击无反应**：检查桌面项是否已「允许启动」；或从终端运行 `dist/LinkFlow` 查看报错。
- **Windows 图标**：需存在 `**icon\link.ico`**；仅 `link.svg` 时部分步骤仍可用，但 `.exe`/快捷方式图标可能不完整。
- **依赖冲突**：若曾安装完整 `**PySide6`** 元包，打包体积会显著变大；精简场景请使用 `**PySide6-Essentials**` 并避免安装 `**PySide6_Addons**`。

---

## 许可证

以仓库内已有声明为准；使用 Qt（LGPL/GPL）与 Paramiko 等第三方库时，请遵守其各自许可证。