# Windows EXE 候选构建与交付

本轮为 5.2.0 / R3.9-desktop-candidate。源码和内部构建结果不能代替正式发布门槛；最终状态以构建目录的 ACCEPTANCE_REPORT、BUILD_MANIFEST 和 SHA256SUMS 为准。当前未配置正式发布证书和线上 TUF 信任根。

## 同事使用

运行完整 `SeedanceStudio_Setup_*.exe`，安装后从开始菜单或桌面启动。EXE 应包含 Python、Node、FFmpeg、Chromium、固定 WebView2、OCR 及 small 语音模型；不要求同事先装 Python、Node 或运行 pip。安装目录和用户数据分开，卸载保留用户数据。

同事首次验收使用安装包旁的唯一 `01_INSTALL_AND_VERIFY.cmd`，通过 Windows 自带 PowerShell 完成校验、安装、独立测试数据的启动/保存/恢复及真实 GUI 截图，产生回传 ZIP。需要验证系统重启时，由操作者重启后运行 `02_RESUME_AFTER_REBOOT.cmd`；程序不自动重启电脑。脚本只对本次进程使用执行策略参数，不改变机器策略。已有注册安装时先核对完整文件，不覆盖另一个版本；未知非空目录保留并拒绝覆盖。

首次在“状态与设置”填写自己的 API 账号。密钥使用当前 Windows 用户的 DPAPI 保存，配置导出不带密钥。换电脑/换 Windows 用户需要重新输入凭据，不能把旧 DPAPI 文件当作可跨账号使用的密钥。

旧版数据只通过明确选择旧安装目录迁移，禁止覆盖已有目标数据。旧 Hypit 工程仅重绑定受控本地 Runtime 路径，保留原 JSON；有执行中或结果未知的付费任务时不迁移该工程配置。

## 从源码重新构建

选择 `tools/desktop-toolchain.lock.json` 中的 Python 3.14.8 x64 和已核验 Inno 6.4.3 工具链。本次 pin 是经过验收的候选，不是永久版本政策；升级先进入隔离环境。

```powershell
python tools/prepare_locked_python.py --output C:\StudioBuild\wheels
python -m venv C:\StudioBuild\venv
C:\StudioBuild\venv\Scripts\python.exe -m pip install --no-index --find-links C:\StudioBuild\wheels --only-binary=:all: --require-hashes -r requirements/python-win-x64-py314-r39-candidate.lock.txt
C:\StudioBuild\venv\Scripts\python.exe -m pip check
C:\StudioBuild\venv\Scripts\python.exe tools/build_desktop_exe.py --output C:\StudioBuild\candidate --cache C:\StudioBuild\cache --iscc C:\BuildTools\Inno643\ISCC.exe --git C:\BuildTools\Git\cmd\git.exe
```

开发构建首次需要联网下载公开依赖，来源和 SHA 均锁定。唯一没有官方 wheel 的 proxy_tools 从固定源码包在临时环境中构建，必须复现记录的 SHA；不依赖旧电脑 wheel。npm 依赖在构建暂存目录中按 package-lock 重装，不执行第三方安装脚本，并重新编译、核对已入库的 dist。

构建器拒绝不一致的 Python/传递依赖/编译器文件，拒绝源码在冻结期间变化，执行源码和 payload 敏感内容扫描，记录完整文件清单。不要手动把开发目录中的 `.env`、数据库、凭据、日志或缓存复制到产物。

Inno 6.4.3 是本轮已实测的内部候选工具链，不声称是最新版；后续版本的安全变化、许可及编译兼容性必须另行评审，不能自动切换。第三方 DLL 的完整再分发许可审查尚未完成，因此不能把内部验收包当作已完成商业分发审核的正式包。

## 当前边界

- Windows 11 x64 为主要目标；必须在仍受支持的实际系统上完成首次安装及重启验收。
- Windows 10 的技术最低 build 为 19041，实际兼容仍待验证；已结束普通支持的系统还需核实其适用的维护/ESU 条件。
- 本地 ASR 需要 AVX2，旧 CPU 会明确跳过；WhisperX、MediaPipe、YOLO 等高级扩展未随本轮候选完成独立验证，不能显示为已通过。
- API 推理仍通过用户配置的服务进行，网页和聊天缓存保存在本机；这不是在电脑上安装 GPT 权重。自定义服务目前限受校验的公网 HTTPS 协议和已支持的参数契约。
- 更新只自动检查，不自动安装。缺少正式 TUF 根/源/发布证书时显示 BLOCKED，不联网下载或执行安装。
- 真实付费模型、Bright Data 和视频业务需要新的有界授权，离线回归不会继承历史额度。

详见 `release-r39/` 的审计记录。它们是阶段证据索引，未完成的最终 EXE、新系统和真实业务验收仍必须保留未通过状态。
