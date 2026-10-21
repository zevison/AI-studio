# CODEX 工程交接说明｜Seedance Studio × 官方 Hypit × OpenLux

> **状态日期**：2026-10-16（本文件是该日的静态快照，不代表之后代码、平台 API 或模型目录不再变化）  
> **交接类型**：继续研发/审计/自动回归，不是正式版本发布说明  
> **工程目标**：将官方 Hypit 专业视频 Agent、Skill、SVML/SVS/SVRun、Runtime、Studio，以及按任务选择 OpenLux 模型的 Provider，真实集成到 Windows 本地 Seedance Studio；在用户上传爆款视频和商品/人物/音频素材后，可自动理解、规划、生成或编辑、合成并交付可追溯的视频工程与成片。  
> **核心声明**：当前**尚未完成官方 Hypit Provider 注册/SDK 构建，也未跑通真实 Windows + OpenLux 账号的端到端链路**。不得把独立模块的离线测试冒充产品已完整可用。

## 0. 给 Codex 的优先执行指令（请先读）

你是接手该项目的资深工程师。**先复现与核对，再连续开发、自动修复、重测和推进**；不要每完成一步就停下来要求用户回复“继续”。如果遇到真实付费、账户权限、不可获得的私有文件或必须用户取舍的阻塞项，才请求批准。除非明确授权，不允许调用收费视频生成，不得绕过安全门禁，也不得伪造测试通过。

1. 阅读本文件、`SOURCE/V5.0.0_Full_Original.zip`（用户提供的完整源码基线）、`REFERENCE/V5.1.1_CumulativePatch_UNVERIFIED.zip`（历史候选，不是稳定版）和 `DEVELOPMENT/Stage4_Hypit_OpenLux_Dev.zip`（开发快照）。**不要将 Stage4 直接覆盖到现有程序**。
2. 在隔离工作区解压 V5.0.0 为可检出的基线；按 V5.1.1 manifest 对比差异；理解 `app.py`、`core/openlux.py`、`core/workflow.py`、`core/db.py`、`core/secrets_store.py`、`core/config.py`、现有视频任务状态、OSS、前端接口。不得想当然认定用户当前实际安装的版本等于资料库某个版本。
3. 单独复跑 Stage4 离线测试。先解决 **TypeScript 构建依赖缺失**（本次实际重测 `tsc -p tsconfig.json` 报 `TS2688 Cannot find type definition file for 'node'`）；增加明确、可锁定的 devDependencies 和 lockfile。现有 28 项 Node 测试运行的是包内已编译 JS；不等于本环境中 TypeScript 重新编译成功。
4. 以锁定版本的 **Hypit v0.3.1** 官方源码/实际导出为准核查 Skill、CLI、Model、Provider SDK；查看 https://github.com/hypit-ai/hypit 和官方 releases。此前资料表明可能使用 `@hypit/hypit/endpoint`，**必须由实际 SDK 验证，不能凭文档名称杜撰 API**。不要把对 `start/poll/collect` 的 Python 模拟接口当作已经注册的 Hypit TypeScript Provider。
5. 为 OpenLux **按具体模型**核对 `/v1/models`、工具调用、视觉、视频编辑/参考、视频异步请求/查询/下载、报价与限制。OpenLux 是模型 API 服务商，**不是 Hypit Agent**。先离线 Mock/只读验证；具体账号能力未知时禁止提交付费生成。Agent 的 Token 调用本身可能收费，也需要预算和用户授权。
6. 在官方 SDK 中完成 Provider 的真实注册、类型检查、Runtime 激活、`start → poll → collect`、安全持久化及可靠恢复；再把真正的 Agent 工程编写和 Hypit 的 `check/plan/build` 接入。先用本地现成 MP4 走免费的官方 Runtime Build，最后才考虑预算严格限制的单次付费验证。
7. 完成后按项目规则制作**从 V5.0.0 及以后支持版本直升的累积补丁**，保留密钥、数据库与任务状态，自动安装前预检/备份/失败回滚；只有明确要求“完整安装包”时才发布所有源码、依赖和启动入口齐全的完整包。
8. 持续更新单一 `版本更新汇总.txt`、版本登记、完整测试证据。不得只给伪补丁、孤立修复脚本、无测试声明的“稳定版”。

## 1. 本包文件说明与可信状态

| 文件（相对交接 ZIP 根目录） | 作用 | 验收性质 |
|---|---|---|
| `SOURCE/V5.0.0_Full_Original.zip` | 用户上传的 183 项文件的完整基线，含 Python 本地 HTTP 后台、UI、OpenLux 客户端、安装脚本 | 来源/ZIP CRC 已核对，**未通过 Windows 生产验收** |
| `REFERENCE/V5.1.1_CumulativePatch_UNVERIFIED.zip` | 历史累积补丁；包含 `core/hypit_official.py`、`core/hybrid_workflow.py` 等变更 | 仅用于 diff/迁移；不是完整 Hypit Agent，**禁止视为稳定可用** |
| `DEVELOPMENT/Stage4_Hypit_OpenLux_Dev.zip` | Agent 基础模块、OpenLux 安全网关、Python Provider 生命周期桥接、工程付费前门禁、TS 合同测试 | 独立开发快照，未接入正式 UI 或官方 SDK |
| `REFERENCE/SeedanceStudio_Version_Register.txt` | 版本号、来源、风险、历史测试摘要 | 历史登记，非产品验收证明 |
| `REFERENCE/Seedance_Hypit_Integration_Audit_20261016.txt` | V5.0.0 旧 API 行为及 Hypit/OpenLux 集成审查 | 离线分析辅助 |
| `EVIDENCE/verification_20261016.txt` | 本轮复测命令和 stdout/stderr 摘要、校验信息 | 仅证明其中列出的当前环境检查 |
| `MANIFEST_SHA256.txt` | 文件包内各源文件 SHA256 | 完整性校验，不代表功能通过 |

来源压缩包 SHA256（原始文件）：

- V5.0.0 完整包：`646a561fe9168679079dc5b685d0b248c1db0f275c878eedeee8f825e6133c6f`。
- V5.1.1 候选补丁：`6e0318af8266696ad6f4f91b941971207ee310d920de6f7e97e654aad6b92452`。
- Stage4 开发包：`3d432ae4adf12a396f249043ceec21ef39b018178818204c3fc9956f7e99fea2`。

> 安全：原始 V5.0.0 ZIP 自带 `.env`，已核对其**与 `.env.example` 字节相同，仅含默认启动配置**；原包没有 `data/` 目录。仍需 Codex 在任何公开提交/日志/报告之前重新扫描凭据，不上传真实 `.env`、Windows 凭据、OSS Key、API Key、用户视频、任务数据库或生产日志。

## 2. 真正完成了什么／没完成什么

| 能力 | 当前证据 | 当前结论 |
|---|---|---|
| Seedance Studio Windows 本地架构 | 完整源码在 V5.0.0 包，使用 Python `ThreadingHTTPServer`，前端在 `static/` | **已有基线**，本轮未实机运行 |
| OpenLux 现有客户端 | V5.0.0 `core/openlux.py` 实现模型查询、视频创建和视频任务查询 | **有代码**，模型/参数/账号实际兼容待核对 |
| 独立 Agent 工具执行边界 | Stage4 `core/hypit_agent_runtime.py` | **离线单测通过**，真实 LLM/Skill 安装未验证 |
| OpenLux 严格校验和重复扣费隔离 | Stage4 `core/hypit_openlux_gateway.py` | **离线单测通过**，真实服务幂等协议未知 |
| Python `start/poll/collect` + 下载校验 | Stage4 `core/hypit_provider_bridge.py` | **桥接逻辑已写**，不是官方 TS Provider |
| `check/plan` 预检与工程指纹保护 | Stage4 `core/hypit_production_gate.py` | **Mock CLI 测试通过**，真实 CLI/Runtime 未验证 |
| TypeScript 请求合同 | Stage4 `src/openlux-contract.ts` 与 `dist/` | **28 项预编译 JS 测试通过；当前 TS 重编译失败，需补 devDeps** |
| 官方 Hypit v0.3.1 SDK 编译、Provider 注册与 Runtime 运行 | 未见官方依赖包或实测结果 | **尚未完成** |
| OpenLux 指定账号模型、函数调用、视频编辑接口真实验证 | 无账号凭据/真实响应 | **尚未完成** |
| 官方 Hypit Skill 驱动 Agent 动态编写/修改工程 | 仅有受限工具层；没有已验收的真实模型工作流 | **尚未完成** |
| Windows 运行、断电恢复、Studio、最终视频复刻质量 | 没有实际验收证据 | **尚未完成** |
| 安装包与累计补丁 | 现有 V5.1.1 仍属历史未验收版本 | **不得宣称完成** |

**明确禁止的混淆**：离线 121 项测试通过 ≠ 121 项业务能力完成；`check/plan` 模拟器通过 ≠ 真实 Hypit CLI 通过；下载本地生成的 MP4 成功 ≠ Seedance 生成质量可用；公开模型目录存在 ≠ OpenLux 当前账号开放该模型；仅运行 `hypit --version` ≠ 完成官方 Hypit Agent 集成。

## 3. 本轮重新执行的测试与发现（新复核，不只是旧记录）

环境：隔离 Linux，Python 3.13.5、Node 22.16.0、全局 `tsc` 存在；**非用户 Windows 主机**。

```bash
# 解压 Stage4_Hypit_OpenLux_Dev.zip 后，位于其根目录
PYTHONPATH=. python -W error -m unittest discover -s tests -q
# 本轮：Ran 93 tests ... OK

node --test dist/tests/*.test.js
# 本轮：tests 28, pass 28, fail 0

tsc -p tsconfig.json
# 本轮失败：TS2688: Cannot find type definition file for 'node'
# 根因：当前项目 package.json 未明确固定 @types/node/TypeScript 开发依赖；
#       包中存在 dist 已编译 JS，不能据此声称完整构建可复现。
```

历史 Stage4 报告记录：FFmpeg 本地生成约 1 秒 MP4 → 模拟下载 → `ffprobe`/SHA256 校验成功。**这项媒体端到端测试未在本轮重新运行**，请 Codex 复测；证据在 `DEVELOPMENT/Stage4_Hypit_OpenLux_Dev.zip` 的 `TEST_RESULTS.txt` 中。

还要检查：`PACKAGE_FILE_MANIFEST.txt`、V5.1.1 `manifest.json` 的文件哈希与允许源版本、安装脚本实际回滚、`.env` / SQLite / OSS / API Key 的保存路径与实际权限。源代码/签名哈希通过不能证明可安全部署。

## 4. 目标系统架构（必须实现的真实链路）

```text
本地中文 UI：爆款复刻（Hypit）
    ↓ 用户提交参考视频、商品/人物/图像/音频；费用预算与授权
本地内容分析：视频镜头、视觉、动作、字幕、音频节奏/时序；证据与素材清单
    ↓
独立 Hypit 视频 Agent（可配置的 OpenLux LLM + 官方 Hypit Skill）
    ├─ 受控工具：查阅 Skill、读写项目文件、check、plan、读取错误并自行修复
    ├─ 创建动态 SVML / SVS / SVRun；分镜和依赖图与输入素材关联
    └─ 禁止任意执行 shell、查看密钥、绕过批准提交付费任务
    ↓
官方 Hypit Runtime / Provider 注册（固定 SDK 版本）
    ├─ 本地原有素材复用、FFmpeg/Chromium/字幕特效、Studio
    └─ OpenLux Provider：按具体图像/视频/音频模型分别匹配接口
         start → 持久记录 → poll → collect/校验 → 恢复/去重
    ↓
免费真实官方 check/plan/build 与源工程、依赖、预算指纹核验
    ↓ （得到用户明确的预算授权后）
正式付费生成/编辑（未知报价/状态/协议 => 拒绝）
    ↓
官方 Runtime 合成 → ffprobe/成片品质检查 → 归档工程、任务和费用
```

关键是 **Hypit 是真正的创作执行主体，OpenLux 是按任务匹配的 AI 模型供应商**。不能先在 Hypit 之外调用 OpenLux 生成整段视频，再只用 Hypit 包装/导出，便自称集成了完整官方 Hypit 视频 Agent。

**音频现有需求必须保留**：仅当用户实际上传/选择音频素材时，才显示“音频使用方式”；`参考音色` 与 `使用上传音频内容并保持音色` 两项互斥，不能在无音频时影响原视频生成。参考音色不得复用原台词和背景声音；具体音色复刻 API 与权限仍需验证。已有工作台的全局最多 10 个视频任务、持久化任务中心、素材语法 `@image 1` / `@video 1` / `@audio 1`、OSS/API 凭据保存不得回归。

## 5. Stage4 核心文件与代码事实

- `core/hypit_agent_runtime.py`：`HypitAuthoringAgent.run`、`ProjectTools.call`、`resolve_official_skill`、`openlux_chat_adapter`；工具白名单 `read_skill/list_files/read_file/write_file/validate`，CLI 仅允许 `check/plan`。工具层有路径、文件大小和并发限制；未证实真实模型已接通。
- `core/hypit_openlux_gateway.py`：`build_video_request`、`task_id_from`、`state_from`、`result_url_from`、`DurableVideoGateway.submit/poll`，SQLite 持久化，未知提交结果进入隔离状态并禁止盲目自动重发。
- `core/hypit_provider_bridge.py`：`VerifiedRoute` 默认 `account_protocol_verified=False`；**硬编码草案路由不是线上接口验收结果**；`OpenLuxHttpTransport` 不重试提交，`SpendEnvelope` 约束金额与授权时效，`ProviderLifecycle.start/poll/collect`、`SafeMp4Collector` 限制 URL/大小，执行真实 `ffprobe`。
- `core/hypit_production_gate.py`：`HypitProductionGate.preflight/start`，验证 `main.svml`、`build.svrun`，锁定项目源文件与 Hypit Runtime Profile/Provider 包指纹，HMAC 签名；不能让前端自造 `SpendEnvelope` 或签名凭证。
- `src/openlux-contract.ts`：模型任务请求与 HTTP/媒体边界合同；**尚未采用官方 Hypit TypeScript SDK 类型检查**。
- `tests/test_*.py`、`tests/openlux-contract.test.ts`：用于复现上述模块测试；`dist/` 是历史编译产物，不应作为新 SDK 兼容证据。

请在改动前确认各文件实际导出、数据结构、线程/进程模型，不要只依据上述概述修改代码。

## 6. 已知缺陷和必须先解决的风险

### P0：任何一个不满足就不得开始付费生成

1. **服务端不得绕过官方 Hypit 工程门禁**：先存在真实可校验的 Hypit 工程，`check` 与 `plan` 成功，涉及的资产/模型/预算/Runtime Profile/Provider 版本与批准时一致；否则拒绝发送 OpenLux POST。
2. **旧 V5.0.0 已离线复现三类问题**：视频编辑缺源视频仍可构造请求；不支持的素材角色可能被静默丢弃；未知任务状态会被误认作运行中。新的 Provider 必须 `fail closed`。
3. **禁止模糊提交自动重试**：POST 已被远端接收但本地未拿到任务 ID 时，不能重新扣费。需要请求身份、可验证的服务端幂等/对账、持久记录与人工恢复策略。
4. **真实费用不明不得批准**：按照模型/时长/清晰度/编辑模式/音频等获得账号实际报价；未知报价不得靠猜测或给前端一个布尔值越权提交。
5. **远程媒体信任边界**：上传素材需内容哈希/对象版本绑定；下载需要核对实际媒体类型、时长、hash、可播放性、签名 URL/私网/重定向拦截。要考虑 DNS 重绑定、恶意 CDN、超大文件、响应头与媒体不一致。
6. **密钥安全与隔离**：用原有当前 Windows 用户加密凭据存储；Agent 不得读取其他项目 JSON/凭据；日志脱敏；控制随机路径、符号链接、任意命令、并发竞争；前端“已确认”不等于服务端授权。

### P1：完整功能与可靠交付

7. **真正实现官方 Hypit SDK Provider**（不是 Python 近似桥接）。验证官方包/导出/API/版本后再注册与编译，测试真实 Profile/Model/Resources/Checkpoint/定价等适用接口。
8. **OpenLux 型号逐项实证**：既有历史候选（例如 Seedance 2.5、Gemini 视觉/工具模型、动作迁移模型）**全部只算候选，非账号验证通过**。逐个确认 model ID、角色、长度、模态限制、异步格式与错误分类，不允许自动切换更贵模型。
9. **Agent 动态创作而非固定模板**：历史 V5.1.1 `_author` 曾在不同参考视频长度下生成相同固定 15 秒工程；需在两个差异明显的参考视频上验证不同的真实时间线与合法工程。
10. **Windows 生命周期**：浏览器刷新、切换页面、服务重启时任务继续存在；多任务全局限额 10；合理中断、取消、定时超时、退避/限次轮询、任务状态对账、恢复与 DB 迁移。
11. **新旧安装与回滚**：直接从 V5.0.0 及其后支持版本升级，预检原文件校验，备份用户数据，升级失败恢复，重复安装幂等，保持 UI 的其他能力不退化。
12. **TypeScript 可复现构建**：独立 `npm ci`/准确锁定开发依赖，实际执行 `tsc`、运行 28 项以上 TS 测试，并通过官方 SDK 真实 typecheck/Runtime test。

## 7. 自动连续实施与验收步骤

请按先后依赖顺序执行，成功就推进，失败就修复、重跑，不需每一步等用户确认。

**阶段 A｜基线和可重复构建**

- 安全解压 V5.0.0 与 Stage4，建立 Git 基线/变更树；比对未验收 V5.1.1 代码，找出真正需要保留的兼容变更（不能简单弃用用户真实功能）。
- 修复 TypeScript devDependencies 与 lockfile；确保全新干净目录安装后不依赖系统已有 `dist` 就能构建。
- 自动化执行 Python、TS、静态代码/依赖审计；记录具体版本和结果。
- 通过标准：独立环境可以构建；不存在凭据泄漏；失败路径有可复现报告。

**阶段 B｜官方 Hypit Provider**

- 锁定/拉取 Hypit v0.3.1 的真实 SDK 与 Skill/CLI；核实具体导出。注册 OpenLux Provider，支持正确的 `start/poll/collect`、模型与资源映射、进度、取消/超时、素材依赖复用。
- 针对每种视频操作（普通生成、参考编辑、延长、动作迁移）建立准确的请求/响应 JSON 契约；不支持的模式在本地明确拒绝。
- 用 FakeTransport 完成任务级错误注入、费用、幂等、HTTP 类型、防 SSRF 和 SQLite 崩溃恢复；先不发真实视频请求。
- 通过标准：**真实官方 SDK 编译成功**，Hypit Runtime 能加载自定义 Provider，离线全流程 `start/poll/collect` 通过。

**阶段 C｜真实 Agent 与本地免费工程**

- 让 Agent 真正读取官方 `SKILL.md`/references，调用 OpenLux 已确认支持 tools 的具体 LLM；受控读取参考素材分析结果并动态编写 SVML/SVS/SVRun。
- 用两个不同的真实本地 MP4/商品素材核验镜头和时长变化，CLI `check/plan` 成功、错误可自动修复；先用本地素材进行官方 Runtime Build、Studio 检查、成片输出。
- 限制 Token 调用预算；实际 Token 调用如收费需用户授权，模拟模型测试不等于真实质量。
- 通过标准：本地免费真实官方工程闭环，不依赖固定模板和假 Runtime。

**阶段 D｜OpenLux 账号级验证与小额付费门槛**

- 首先只读 `GET /v1/models`，验证具体模型权限与能力，核对价格/计费口径；保存不含密钥的脱敏响应样本用于契约测试。
- 支持模型/协议的只读、无费用操作时自动执行；**任何可能扣 Token 或视频生成费用的在线操作必须取得用户明确授权和预算上限**。
- 再完成一次小预算、明确任务范围的视频编辑/参考生成实测；不确认就不提交。
- 通过标准：在线真实任务 ID/status/result、计费和素材语义与实际 OpenLux 返回一致，有恢复证据。

**阶段 E｜合并、升级、正式交付**

- 将官方 Agent + Provider + Runtime 集成到原 UI、后端路由、全局任务系统和成本中心；避免破坏 GPT 聊天、热点采集、OSS、现有视频/图片生成等能力。
- Windows 新电脑安装、升级与回滚多版本矩阵，空数据/旧数据/多任务并发/断电恢复；检查中文操作路径。
- 输出累积补丁；附单一更新 TXT、最终 `VERSION_REGISTER`、SHA256 清单、测试清单及明确剩余风险。
- **只有官方 Runtime、真实模型接口和 Windows 测试通过后，才允许标记为正式稳定版。**

## 8. 版本与交付政策（必须遵守）

- **V5.0.0**：源基线，代表可复用原始完整代码；不意味着已实机稳定。
- **V5.1.1**：可分析的历史未验收补丁；不能假定用户已安装或它已满足“官方 Hypit”功能。
- **Stage4**：`2026-10-16` 开发快照，不分配发布版本号。
- 目标下一版本号：**尚未分配**。必须按真实代码合并和正式验收结果决定。
- 默认制作可从 V5.0.0/已支持后续版本一步直升的累积补丁；单次文件修改需完整可覆盖源码；多文件交付提供可运行文件包。只有用户特别要求完整新电脑独立安装包，才交付真正完整的所有程序与安装流程。
- 源码/版本/文档归档到用户项目资料库；避免多份重复安装包；所有版本说明保持在一个 TXT 中。

## 9. 最终必须交回的东西（验收标准）

1. **能运行**：本地网页、后台 Agent、真实 Hypit Skill/SDK/Runtime，均能在目标 Windows 环境启动并协作。
2. **能创作**：至少两个不同的参考视频得到各自不同的合法视频工程并合成成片，而不是只将 OpenLux 的输出包装成 MP4。
3. **能管钱**：每次付费任务有来源、任务 ID、批准额度、实际请求和恢复记录；重复/未知/超预算默认拒绝。
4. **能恢复**：重启/刷新/任务中断后状态一致，可取消、可回滚，升级不丢凭据与旧任务。
5. **能验证**：自动测试报告、真实 Hypit 构建报告、至少两类真实红队场景与修复复测、明确在线接口及 Windows 未验收项。
6. **能交付**：累积补丁完整包、安装/升级/回滚、单一更新说明及版本登记；不存在“只改一个脚本冒充已完成整体系统”。

**给 Codex 的最终提醒**：用户更看重真正完整实现和减少反复手工测试，而非版本号或报告形式。自行连续推进能验证的工程任务；遇到真实需要用户授权的付费操作，应先准备最小预算、作用范围和可预期风险再询问。不要将未经验证的协议猜测转成付费 API 请求，也不要编造安装/测试通过结果。
