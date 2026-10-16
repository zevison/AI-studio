# Seedance Studio V5.0.0 | Stable Full Release

**Current Windows setup (new PC):** Run `01_INSTALL.cmd` once, then open the workbench with `00_START.cmd`. These are the only two root-level CMD entry points; dependencies and FFmpeg are installed/verified through `01_INSTALL.cmd`. New computers need Internet; Python/FFmpeg/model binaries are not included for offline setup.

**V5.0.0 scope:** Preserve the functioning V4.2.8.20 product and video workflow. Repair verified live SQLite WAL backup inconsistency and standardize the backup/migration snapshot paths. Video creation, OpenLux billing submission, audio role assignment, and main UI were intentionally not rewritten. New Windows machine installation and paid model quality are not validated by Linux/offline tests.

**Update history:** Refer to the single `版本更新汇总.txt` file. All older instructions below are historical records and do not override the two commands above.

---

## Earlier release information (historical, not current install instructions)

> **V4.2.8.19 CURRENT WINDOWS INSTRUCTIONS**: Run `01_INSTALL.cmd` once to install, then double-click `00_START.cmd` to open the workbench. These are the only two root CMD entry points. Earlier VBS and older install/start filenames mentioned below are historical and do not apply to this version. The package requires internet for first-time dependencies and does not contain offline Python/model binaries.

> V4.2.8.14 fresh Windows installation: run **01_INSTALL.cmd** once, then **00_启动工作台.vbs**. Optional maintenance tools are under `tools/maintenance`. No other install scripts are required.

# Seedance Studio V4.2.8.12 — Unified Installer

**All dependencies are installed or verified by `01_INSTALL.cmd` only.**
`00_INSTALL_NEW_PC.cmd` is a compatibility wrapper calling `01`.
Former 08/10/13/14 dependency installer scripts were removed; their installation
steps (FFmpeg, OpenCV, audio, OCR, YOLO, yt-dlp/curl_cffi) run from `01`.
First installation needs Internet; large models/binaries are downloaded.
`install_logs/dependency_report.json` explicitly reports unavailable optional
capabilities (e.g. MediaPipe/WhisperX on incompatible Python runtimes).
Full Windows end-to-end installation still requires validation on a Windows PC.

# 历史版本说明（以下安装操作如与首页冲突，以最新01统一安装为准）

## Seedance Studio V4.2.8.10｜人工爆款复查与真实视频拆解精简版

**主启动入口：`00_启动工作台.vbs`。请先关闭原版本，保留旧版 `data` 和备份。**

1. 修正 V4.2.8.6 启动器错误检查 V4.2.8.5 的确定性错误。启动器只从 `core/config.py` 读取版本号，不再重复写死版本。
2. 本机健康检查绕过系统 HTTP 代理；只读且不调用付费接口。
3. 保持使用与 `04_DEBUG.cmd` 相同的后台主程序 `app.py`。隐藏启动器在后台驻留，监测自己启动的服务意外退出，最多自动恢复3次（180秒内），不会自动提交视频、图片或 Bright Data 付费任务。
4. 只有后台真正退出才自动恢复；后台有响应延迟但仍在运行时不会强行关闭，避免打断提交中的付费任务。
5. `03_STOP.cmd` 和本机停止接口设置停止标记，阻止主动停止时自动恢复。启动端口、实际版本、根目录写入本地日志。
6. 日志：`data/startup.log`、`data/server_direct.log`、`data/startup_stage.txt`。Windows 实机仍需验收。

注意：服务器重启的几秒内浏览器刷新仍可能短暂提示连接被拒绝；恢复后重新刷新即可。服务自动恢复不等于付费任务自动重发。旧记录和附件需要从原版本按原有工具迁移，新压缩包不携带私人密钥。

---

# Seedance Studio V4.2.8.6｜生成结果历史提示词版

历史备注：当时Tesseract有独立安装入口；V4.2.8.12 已统一纳入 `01_INSTALL.cmd`。

该安装步骤使用固定上游版本及 SHA-256 校验，图形安装结束后系统体检与视频分析会检测真实可运行路径。无需清空历史或重新安装其他组件。

## V4.2.5 安装修复说明

Windows 首次安装运行 `00_INSTALL_NEW_PC.cmd`。本版不依赖 winget，缺少 Python 时从官方 Python.org 下载并校验 Windows 数字签名；详见 `README_NEW_PC_CN.txt`。

# Seedance Studio V4.2.4｜新电脑完整安装整合包

本压缩包含完整的 Seedance Studio 源代码、网页静态资源、Windows 启动及首次环境安装脚本，**不是增量补丁**。已集成 V4.2.1–V4.2.3 的界面与统一历史功能。首次安装请阅读 `README_NEW_PC_CN.txt` 并运行 `00_INSTALL_NEW_PC.cmd`；安装后双击 `00_启动工作台.vbs`。

- 支持 Windows 10/11 x64；首次安装通常需要联网下载 Python 3.12、依赖与可选工具。
- 历史版本曾分步安装本地视频分析组件；自 V4.2.8.12 起统一由 `01_INSTALL.cmd` 安装和校验。
- 不包含个人历史数据、密钥或模型权重；跨电脑迁移请使用 `11_创建新电脑迁移包.cmd`、`12_导入新电脑迁移包.cmd`。
- 已核实历史接口 `/api/history/overview` 在后台直接注册；实际用量只统计本地记录，不推测供应商真实账单。

---

# Seedance Studio V4.2｜全站 UI 架构重构

基于 V4.1 全量源码叠加 V4.1.1 时间补丁开发。V4.2 仅调整界面布局与样式，保留现有业务接口、数据库字段、任务中心和本地密钥存储规则。

**安装/数据迁移：** 请先备份旧工作台，解压到单独目录，确认安装后再用现有迁移工具导入历史资料。只读验收文件与更新细节见 `版本更新汇总.txt`。

---

# Seedance Studio V4.1｜高能力 GPT 对话完整代码包

本版基于 V4.0 完整程序升级，不是差异补丁。**推荐解压至独立英文目录**，不要直接覆盖唯一的历史 `data` 目录；按现有迁移脚本迁移数据库与配置。

**GPT 对话：** 默认 GPT-6 Astra（是否可用须核实账户权限），动态同步 OpenLux 模型列表，流式答复，自定义思考强度，主动上传图片/文档和手动记忆。联网搜索可选，但供应商 web_search 是否可用**待真实账户验证**。

**不等同于 ChatGPT 网站全部功能：** 代码执行器、深度研究、远程网页自动化、实时语音等未在本版实现，不能仅通过模型别名获得。

**速度：** 流式回复改善首字出现时间的体验，不意味着 GPT-6 Astra 的真实生成速度必然快于均衡模型。

**旧功能：** 商品知识中心、视频生成、图像、热点收集、任务和 OSS 组件保持原有逻辑。本包不含用户密钥或历史数据库。

详细更新和历史版本集中在 `版本更新汇总.txt`。

---

# Seedance Studio V3.9.8.19 — 按模型自动匹配清晰度

在 V3.9.8.18 基础上升级，保留原有 TikTok、商品知识中心、音频模式、素材和视频任务。

- **Seedance 2.5**：现有适配列表 480P、720P、1080P、2K。
- **Vidu Q3 Turbo**：当前适配器支持540P、720P、1080P；同步修复通用预览将540P误判为非法的问题。
- **MiniMax H3**：原生768P、2K；可选择720P成片（768P生成并本地缩小）或1080P成片（2K生成并本地缩小）。实际费用依据原生档位。
- 视频创作、爆款复刻和状态与设置中的全局默认模型，全部使用后端统一能力表提供清晰度选项。
- 用户已选清晰度符合新模型则保留；不符合则选最接近的可支持档位，并提示变化；不会发起任何生成请求。
- 智能自动路由在预览之前不能凭空断定模型，因此先显示候选输出规格；预览选出模型后缩小选项范围。

**提示**：菜单代表现有接口适配器可处理的参数，不等同于已在 OpenLux 账户付费实测成功。若后端不支持的素材或能力，仍会在预览阶段提示。

不要重新运行 `01_INSTALL.cmd`。选择升级补丁时只适用于原始 V3.9.8.18 文件；安装器自动备份程序文件，保留 `.env`、SQLite、本地素材、运行环境。
# Seedance Studio V3.9.8.18 — MiniMax H3 清晰度修复

H3 原生输出档位为 768P 与 2K（不是 1080P）。用户仍可在视频创作中手动选择 480P、720P、1080P、2K 最终成片清晰度：

- 最终 480P/720P：调用 H3 768P 原生档，完成后本地缩小。
- 最终 1080P：调用 H3 2K 原生档，完成后本地缩小。
- 最终 2K：直接 H3 2K 档，无本地缩放。

预览必须显示输出清晰度与实际付费模型档位，估价以原生档位为准。不会以 1080P 假装原生 1080P，不自动重复扣费。
原生成文件永远保留；音频三模式以及下载后本地重试不变。真实 OpenLux 账户创建结果仍须实测，离线回归不等于付费生成成功。

---

# Seedance Studio V3.9.8.17 — 三模式音频管理

Seedance Studio V3.9.8.17 | 三模式音频与原音轨自动合成

1. 视频创作、爆款复刻改为三选一：
   - 参考视频原音频：视频模型生成新画面但关闭原生音频；生成完成后本地 FFmpeg 提取参考视频原音轨，合成至新画面（按新画面时长裁剪，较短时静音补齐）。
   - 模型生成新音频：由已选择的模型及接口生成音频，遵守模型原有音频能力限制。
   - 无声视频：禁用模型新音频；本地 FFmpeg 最终移除任何音轨，确保成片静音。
2. 默认音频模式可在「状态与设置」选择；爆款复刻默认保留参考视频原音轨。
3. 在本次生成任务的预览和持久化请求中写入音频模式、原视频素材 ID 和模型 generate_audio 布尔值；模式改变使旧预览失效。
4. 本地输出中的原视频与最终音频成片分别保存；原音轨缺失或 FFmpeg 缺失时付费前阻止；下载后合成失败，保留原始生成视频，并可免费重试本地音轨处理。
5. 不为此新增付费模型调用。视频生成、爆款复刻、图片生成、商品知识、热点数据、10 并发和原有 OpenLux 适配保留。
6. 如使用有版权的音乐，必须确保有 TikTok Shop 商业使用权限。合成不会保证新动作与原节奏完全同步。

安装：关闭正在运行的 V3.9.8.16，解压差异补丁，运行 01_Apply_Patch.cmd，输入安装目录。无需重装 FFmpeg、Python 或再输入密钥。


# Seedance Studio / Pixelle V3.9.8.15｜三条主模型与近似复刻路由修复

本版由 V3.9.8.14 的完整源码升级，普通用户只显示 Seedance 2.5、Vidu Q3 Turbo、MiniMax H3 三条主模型。其余候选仍保留在后台研究目录与历史记录中，不会默认被派发付费任务。

当舞蹈参考同时要求精确动作或原背景不变时，必须由用户确认接受近似复刻，或者选择严格模式（未经实测的专用接口将被阻止）。不暗中降级素材/规格/原生音频。生成前仍显示费用和所选实际模型，并保留单次授权。

此版不表示动作能够达到100%一致，也没有完成 Kling / PixVerse 专用动作迁移接口；它不自动调用更贵模型。

升级已有 V3.9.8.14 请使用补丁，不要覆盖 data、.env、历史任务与 Windows 凭据；详见 V3.9.8.15_升级说明.txt。


V3.9.8.21: product catalog + normalized save-and-readback fix. No changes to video interfaces or TikTok data sources.


## V4.2.8.5｜输出目录与预览修复

- “打开输出目录”不再在请求线程中同步等待系统文件管理器；无论是否成功打开都提供可复制的绝对路径。
- 增加本地输出目录操作日志 `data/output_open.log`。
- `/outputs/` 支持分段视频预览（HTTP 206 Range）和按块下载，避免一次性加载大视频到后台内存。
- 原 V4.2.8.4 视频位于原版本 `data/outputs`；迁移旧版资料时使用 `05_迁移旧版配置和历史.cmd`。
- 本地模拟检查通过，Windows 实际目录弹出仍待用户环境验收。


## V4.2.8.6：已生成视频的历史提示词

- 在视频任务的「生成结果」卡片内展开当次历史提示词，支持复制全文和复用生成。
- 「历史与成本」→「任务历史」下的视频生成任务有只读的历史提示词展开按钮，可复制并恢复素材/参数。
- 旧版历史仅能展示 `data/studio.db` 中实际保存的原文或模型提交提示词。
- 复用按钮只进入编辑模式，不会自动调用付费生成接口。
- 更新后需重启当前后台，并使用新版本页面；旧版仍在运行时页面可能仍指向旧服务。


## V4.2.8.10 · Bright Data 手动热点
- 采集进度显示直接读取 bt_jobs，顶部四个数据卡与详细任务面板一致，不再使用旧热点扫描记录假装采集状态。
- 本地任务编号、远端 snapshot_id、已耗时、真实远端状态、实际返回数、新增入库数、错误/异常状态可见；历史任务可展开。
- 只允许「确认并手动采集」触发 Bright Data 提交。自动轮询仅查询既有任务，远端进度请求服务端最多每30秒一次；刷新后从数据库恢复，无付费自动重提。
- 提交中超过80秒且没有活跃工作线程，将任务标记为 submission_unknown 并阻止同商品新建采集，防止重复扣费。
- 在接口能力和返回格式未经真实账号验证前，不把「服务商成功」当作「有效证据已入库」；实际额度仍按人工核实。
- 如新版解压到独立文件夹，请先完成旧版数据迁移再查看既有任务。


## V4.2.8.10：简化视频闭环（人工触发）

本版改进 Bright Data 手动采集后的视频人工复查界面。点击视频卡片查看来源，按需获取临时 TikTok CDN 视频文件或上传有权使用的本地 MP4，后端核实完整性并将素材持久绑定到所选来源。

GPT 生成脚本前必须有真实本地视频及 FFmpeg；本地提取最多七个带时间戳画面并可选使用已安装的本地语音转写组件。生成包含原视频分镜拆解和商品专属双语脚本。视觉模型通道可用性在实际接口请求时验证；若不支持多图接口，会直接报错且不会静默降级为文字分析。

人工修改脚本并确认后，直接进入原视频创作工具，不再要求新任务重复确认文字分镜。仍需在视频创作页面单独确认付费生成。原有素材、模型、历史、OSS、凭据迁移、并发等模块保持原样。

未加入 Top Ads、店铺订单数据、ROI 学习闭环及自动批量生产。第三方返回的播放量不能视为真实订单。

本地测试：`python tools/test_viral_human_workflow_429.py`（模拟数据与 mock 模型，不会扣费）；Windows 和 OpenLux 多图能力需在用户环境进一步验收。


> **V4.2.8.19**: FFmpeg install now prefers the smaller upstream 7z build (~34 MB) and uses built-in Windows tar to extract, avoiding an extra decompressor. Download source switching, upstream SHA-256 checks and reuse of older binaries are built in. This source-code package still requires an internet connection for a fresh PC without FFmpeg.


V4.2.8.20: The Windows installer retries scene detection import once and records exact import errors in install_logs/dependency_report.json. Existing data and shared runtimes are preserved.
