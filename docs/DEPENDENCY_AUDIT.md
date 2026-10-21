# 依赖生命周期与隔离候选验收

审计日期：2026-10-20。本文是依赖附录，不把 R3.9 候选改称正式稳定版。
依赖库没有已知公告、可导入或测试通过，均不能替代全新 Windows 安装、重启、升级回滚及实际业务验收。

最终候选已扩为 **69 项完整传递依赖**，包含 ASR 与 TUF。真实语音验收发现 PyAV 19 与 Faster-Whisper 1.2.1 不兼容，独立验证后固定 PyAV 18.1.0。最终 69 包已按 SHA 在空环境离线安装、pip check 通过，安全公告查询零已知命中。源 wheel 改用固定 Python 3.14.8 重建，两次独立 SHA 一致；旧 47 包及 PyAV 19 证据仅保留历史意义，不冒充最终证据。详见 `release-r39/DEPENDENCY_AUDIT_REPORT.md` 和最终构建报告。

## 基线和升级规则

- **已获正式稳定资格的基线：尚无。** 旧 R3.8/R3.9 开发候选保留用于复现，不宣称为安全稳定基线。
- 当前构建和安全升级候选使用不同目录、独立 Python 环境、运行组件和报告，不原地替换已测试环境。
- 官方安全补丁先进入候选：来源与哈希 → 许可证 → 公告适用性 → 完整传递锁 → 离线安装 → 导入/CLI → SDK/本地媒体 → 冻结 EXE → 安装/恢复/回滚。
- `tools/dependency_audit.py` 不安装、不更新、不提升候选，不访问生产数据库。`candidate_decision()` 即使门槛全通过也要求人工发布决策。
- 未完成的门槛必须保留 `NOT TESTED` 或 `BLOCKED`，不得把旧候选自动当作回滚后即安全的正式版本。

## 真实扫描与适用性

在独立审计虚拟环境运行 pip-audit，未向正在冻结的 Python 环境安装任何审计工具。
npm 使用实际 package-lock 执行运行树与完整树两次 audit，没有执行 `npm audit fix`。

| 范围 | 实测结果 | 解释 |
|---|---|---|
| 原 Python 3.12.10 环境 | pip-audit 返回失败；3 个包命中公告 | 原始数据库返回重复 PYSEC 条目，不能把 16 条记录称为 16 个独立漏洞 |
| 原 curl_cffi 0.13.0 | CVE-2026-33752；公告修复版本 0.15.0 | 原帖下载器实际导入该包并提供 yt-dlp 浏览器模拟；存在真实调用链。允许 TikTok 原帖域名、人工确认及限时能缩小入口，但不等于验证了底层重定向目的地安全；没有执行利用攻击 |
| 原 pip 25.0.1 | 包含 CVE-2026-1703、8643、13346 等安装路径公告 | 主要影响构建/依赖安装工具；应用 EXE 不靠运行时 pip 安装。CVE-2025-8869 的旧版 tarfile 回退条件在 Python 3.12 不成立，不能只凭版本表断言可利用 |
| 原 pytest 8.3.5 | CVE-2025-71176 | 公告对应 UNIX 临时目录攻击面；不是本 Windows GUI 的可利用证明。维护跨平台测试工具仍需升级 |
| 新 Python 3.14.8 候选完整环境 | pip-audit 返回 0，未发现数据库已知公告 | 包括 pip 26.2.1、curl_cffi 0.15.0、pytest 9.1.1、PyInstaller 6.22.3；这是查询时点结果，不能保证未来没有新漏洞 |
| npm 运行树与完整树 | 两次 audit 均返回 0，0 advisory | npm audit 不涵盖 Node 可执行文件、系统 DLL、Chromium 或 Python |

原始扫描日志与 JSON 在工作区 `work/dependency-lifecycle-20261020/`，包括 `pip-audit-observed.json`、`pip-audit-candidate.json`、`npm-audit-runtime.json` 和 `npm-audit-all.json`。这些是本地执行结果，不是远程 CI 结果。

## 运行组件的支持与候选

| 组件 | 已核实事实 | 本轮处理 |
|---|---|---|
| Python | 3.12 已进入仅安全维护至 2028-10；3.12.10 是最后提供官方传统 Windows 安装器的 3.12 完整修复版本，3.12.15 为后续安全源码版 | 不把“最后安装器”误称“最新安全补丁”。独立 Python 3.14.8 候选由签名/哈希验证的官方分发生成；实际 ABI 与 EXE 验收记录见主报告 |
| Node | 22 系维护期至 2027-04-30。22.16.0 早于官方 22.17.1 的 Windows 路径安全修复 CVE-2025-27210 | 独立下载 22.23.3，按官方 SHASUMS 校验；40 项现有 SDK 测试实际通过。旧版本包含已公布安全修复缺口，不能无条件按稳定版交付 |
| Node 后续公告 | 官方公告预告 2026-10-21 发布 HIGH 级安全更新 | 本次检查日期为 10-20；没有把尚未发布的版本或 CVE 虚构成已安装、已修复 |
| Chromium | Chrome for Testing 不自动更新；原 152.0.7928.2 不等同于当前稳定通道 | 官方 CfT JSON 查询得到 Stable 155.0.8059.84，独立下载与 `--version` 通过，和新 Node 的官方免费 Film 组合验收另见主报告 |
| 浏览器签名 | 官方 CfT headless exe 的本机 Authenticode 结果为 `NotSigned` | 明确保留此结果。来源为官方通道 JSON 与 Google HTTPS，下载后 SHA256 固定；官方 JSON未发布独立 SHA256，不能谎称已验证签名或上游签名校验和 |
| FFmpeg | 当前锁固定 9.0.2，二进制来源为项目认可的 Gyan 分发；FFmpeg 官方有逐版本 CVE 修复列表 | 不把第三方二进制分发称为 FFmpeg 官方签名。没有推断固定的多年 LTS 周期；按官方修复列表持续复核，具体静态库编译清单适用性仍需记录 |
| PyInstaller | 官方已发布 6.22.3，后续源码 changelog 的 Next Release 不等于已发布 | 新环境候选 6.22.3；原 6.16.0 没有被未经测试直接替换到旧构建环境 |
| WebView2 | Evergreen 自动更新；bootstrap 哈希不能代表实际 runtime 完整锁。Fixed Version 需要软件发行方持续维护。官方说明 Windows 10 22H2 支持至少至 2028-10 | 独立获取官方 Fixed 155.0.4283.62 x64 CAB，310492238 字节；实际 Microsoft Authenticode `Valid`。是否已用于最终 EXE，以主构建与 GUI 验收为准；不能把下载成功记成 GUI 已通过 |

官方候选 Node ZIP：`2b0ff57b049cda1bbcea2240eec20467018713c1efe1f7360c2681859b90ed71`。
官方 HTTPS 取得的浏览器 ZIP 本次计算 SHA256：`5ca590a4968864e219ea25ccd134597ef8898a733a4698c07fc3e8b2c480fef4`。
微软 Fixed WebView2 CAB SHA256：`cf54aa717a0ebd05b40e87620724583cb6eb0a5b8b64764cc11e42822f68bf6c`；签名主体 Microsoft Corporation，证书指纹 `21CA9DD6DA7C57C3A599880D17317D7BDA31899C`。来源 URL 与实际本地资源记录于 `runtime-security-candidates.json`。

## Python 完整锁与复现边界

`requirements/` 保留两组不同用途的锁：

1. `python-win-x64-py312-r39-observed.*`：原环境的 43 项传递依赖与真实 wheel 哈希，仅用于对照复现；已知安全问题明确标记 `BLOCKED`，不能选择为最终安全候选。
2. `python-win-x64-py314-r39-candidate.*`：新的 3.14.8 候选完整 **47 项**依赖，来自实际安装报告、完整冻结版本、官方下载 SHA 和新隔离 wheel 仓库。已在另外新建的 venv 中按 `--no-index --only-binary=:all: --require-hashes` 实际离线安装并 `pip check` 通过。后续若加入语音识别等组件，必须单独扩锁重新验证，不能把当前 47 项锁冒充含未加入的组件。

`pip freeze` 只确定已安装版本，**不是哈希锁**。本轮额外下载真实 artifacts，逐个对照 PyPI SHA。
`proxy_tools 0.1.0` 上游只提供源码包，已先核对官方 sdist SHA，再用固定 setuptools 84.2.0 与 `SOURCE_DATE_EPOCH` 构建通用 wheel；锁中分别保留 sdist 来源哈希和生成 wheel 哈希。
该 wheel 的在线 PyPI URL不能冒充不存在的官方 wheel 地址。离线安装需要单独提供完整 wheelhouse，不将大型二进制提交到源码库。

示例（只适用于匹配的 Python/Windows 架构及完整 wheelhouse）：

```powershell
python -m pip install --no-index --find-links WHEELHOUSE --only-binary=:all: --require-hashes -r requirements/python-win-x64-py314-r39-candidate.lock.txt
python -m pip check
```

## 自动维护与风险演练

- Dependabot 仅创建依赖升级 PR，无自动合并；仓库管理员仍需启用所需 GitHub 功能。没有远程仓库配置时，不声称已经在 GitHub 创建或运行。
- 每周及手动触发的 `dependency-audit.yml` 仅读官方 runtime 元数据、查询 npm 公告并上传报告；不改 runtime lock，不安装应用，不需要生产 secret，不调用付费 AI。
- Action 使用可追溯的官方 commit pin，权限只有 `contents: read`，checkout 不保存凭据。尚未远程执行，状态 `NOT TESTED`。
- 本地等效入口：`python tools/dependency_audit.py --root . --refresh --output build/dependency-audit.json`。该入口明确不会把“元数据读取成功”记成“无漏洞”。
- 离线红队测试已覆盖：不受信来源、错误 SHA、artifact 篡改、路径逃逸、缺失哈希锁、自动升级提升阻止、候选依赖启动崩溃、有限超时、候选数据库被升级至未来 schema 后原 WAL 基线仍完整可用。
- 上述数据库演练只证明独立复制的测试数据库没有被候选修改，不冒充真实安装器回滚或全新 Windows 验收。

## 当前入口和未晋升状态

- 同事使用最终 EXE 安装入口与桌面快捷方式，无需执行下列开发命令。
- 新的 Windows 构建候选使用 Python 3.14.8、Node 22.23.3 及仓库 runtime lock；这些是本轮真实测试对象，仍需通过主 Release Gate 才能晋升正式稳定版本。
- 开发轻量测试入口仍为 `requirements-dev.txt`、`npm ci --ignore-scripts --prefix tools/hypit_provider`、`npm run build:offline --prefix tools/hypit_provider` 和 `python tools/dev_check.py`。轻量开发依赖不能当作包含桌面、语音识别和更新器全部组件的完整安装锁。
- `.codex/setup.sh` 只准备开发环境，不构建正式安装包，不安装生产凭据。执行机器须先选择与当前验收候选一致的运行时；打印版本号不能代替版本检查。
- 旧 Python 3.12/Node 22.16 的说明和兼容脚本仅保留历史复现用途，不应重新成为默认安全交付入口。统一 README、Cloud 和 CI 的最终候选说明由主发布记录负责，不删除旧兼容脚本来掩盖差异。
- 定时维护若有官方元数据源无法核实，工具保存 `BLOCKED` 证据并以非零码结束，避免把不完整查询显示成监测成功。当前仓库没有远程执行证据，不能将配置文件存在称作 GitHub CI 已运行。

## 官方与原始来源

- [Python 版本状态](https://devguide.python.org/versions/)、[3.12.15 安全版](https://www.python.org/downloads/release/python-31215/)、[3.14.8](https://www.python.org/downloads/release/python-3148/)
- [Node Release Working Group](https://github.com/nodejs/Release)、[2025-07 安全公告](https://nodejs.org/en/blog/vulnerability/july-2025-security-releases)、[22.23.3](https://nodejs.org/en/blog/release/v22.23.3)、[2026-10 安全预告](https://nodejs.org/en/blog/vulnerability/october-2026-security-releases)
- [Chrome for Testing 官方通道 JSON](https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json)、[Chromium 下载与自动更新说明](https://www.chromium.org/getting-involved/download-chromium/)
- [FFmpeg 官方安全记录](https://ffmpeg.org/security.html)、[PyInstaller 官方 changelog](https://pyinstaller.org/en/latest/CHANGES.html)
- [pytest 官方安全修复记录](https://docs.pytest.org/en/stable/changelog.html#pytest-9-0-3-2026-04-07)、[pytest 公告](https://github.com/advisories/GHSA-6w46-j5rx-g56g)
- [Microsoft WebView2 分发机制](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)、[Edge/WebView2 生命周期](https://learn.microsoft.com/en-us/deployedge/microsoft-edge-support-lifecycle)
- curl_cffi、pip 的具体公告和去重标识记录在实际 pip-audit JSON；数据库查询结果与本项目利用条件审查严格区分。
