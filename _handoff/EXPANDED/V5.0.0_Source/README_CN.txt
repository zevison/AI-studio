V4.2.8.19 新增：多人对话请在提示词用 @audio 1 明确指定音色属于哪个人物；详见 README_NEW_PC_CN.txt。
安装有分阶段进度和官方 Python 下载真实进度。
V4.2.8.19 当前操作方式（以此处为准）：首次运行 01_INSTALL.cmd 安装，之后双击 00_START.cmd 启动工作台；根目录只保留这两个 CMD 入口。旧版 00_启动工作台.vbs 已移除。
UI修复：视频创作和爆款复刻页面的“音频使用方式”不再竖排，音色参考业务规则不变。以下为保留的旧版说明，仅用于查阅历史，不作为当前操作步骤。

V4.2.8.15 新电脑使用：只运行 01_INSTALL.cmd 安装；运行 00_启动工作台.vbs 启动。其他维护工具位于 tools/maintenance。

【当前统一安装方式】
唯一的依赖安装脚本是 01_INSTALL.cmd。请直接运行 01；08/10/13/14 旧安装命令已经整合，无需再次运行。
Windows 新电脑也只需运行 01_INSTALL.cmd，不存在第二个主安装入口。
安装完成或失败请查看 install_logs/dependency_report.json 和 dependency_install.log。
以下是旧版本的产品说明，若安装步骤冲突，以上述最新说明为准。

Seedance Studio V4.2｜全站 UI 架构重构完整版

1. 本包以 V4.1.0 完整源码 + V4.1.1 时间功能构建，包含全部后端/前端/安装脚本，不是样式补丁。
2. 此次只重构全站布局及视觉结构。保留既有视频生成、爆款复刻、商品知识中心、GPT 聊天、本地记忆、Bright Data 手动收集、10并发任务与计费保护。
3. 升级请先完整备份目前工作台（尤其 data、素材、.env 与旧配置）。解压本包到新英文目录，先运行 01_INSTALL.cmd，再按需使用 05_迁移旧版配置和历史.cmd 迁移旧数据；务必核对迁移源与目标，千万不要覆盖唯一数据库。
4. 原 Windows 用户的加密 API 凭据会按既有程序机制尝试继承；新电脑迁移时请使用程序原有备份/迁移功能，勿在聊天里发送密钥。
5. UI 功能在 1750、1366、1080 窗口尺寸做了浏览器静态验证；当前容器不能直接验证用户 Windows 的真实界面、OpenLux 账号权限和 Bright Data 真实费用。
6. 所有后续版本说明只追加到根目录「版本更新汇总.txt」。

V4.2 关键页面
- 热点收集：独立结果列与分析设置列、手动采集顶部工具条
- 爆款复刻：左侧参考素材，右侧目标与模型，次要设置折叠
- 图片生成：结果预览更大；历史版本自适应
- GPT 对话：保留独立聊天、历史记录、记忆和消息时间
- 其他菜单：统一页面容器、输入控件、状态和响应式布局


V4.2.8.5 正常启动修复（2026-10-14）
- 正常使用双击 02_START.cmd，后台隐藏运行。首次启动或较慢电脑最长等待90秒。
- 启动成功后自动打开浏览器，同目录重复点击只打开现有服务，不重复创建后台任务。
- 如果没有打开，请查看 data/startup.log、data/server_direct.log、data/startup_stage.txt。04_DEBUG.cmd 仅作为备用诊断。
- 本发布包不内置任何用户数据库或 API 密钥。已有数据必须从旧版目录迁移，不要删除旧版 data。
- 当前版本的 Windows 启动方式仍需在你的 Windows 电脑上做最终验证。


V4.2.8.5 注意：02_START.cmd 直接后台启动 app.py，不再启动 watchdog.py。浏览器旧标签页仍可能显示旧版；请使用本版 data/current_url.txt 的实际地址。启动异常请提供 data/startup.log、data/server_direct.log、data/startup_stage.txt，不要发送密钥。升级时勿删除旧版 data 目录及凭据。


V4.2.8.5｜打开输出目录修复
- 页面顶端和任务详情里的“打开输出目录”改为立即返回操作请求，系统另行打开本地目录，避免 HTTP 请求等待 Windows 资源管理器而报 Failed to fetch。
- 点击后显示本地完整路径和“复制路径”；若未弹出文件夹，复制路径后粘贴到 Windows 资源管理器地址栏。
- Windows 开目录失败日志：data/output_open.log。视频本地分段加载支持 Range。
- 旧版已生成的视频仍存放在旧版的 data\outputs；新版本若要显示旧历史、素材及视频，需在旧任务结束后运行 05_迁移旧版配置和历史.cmd 并输入旧版目录。
- 已通过不使用 OpenLux 真实密钥的本地接口测试，Windows 资源管理器弹出仍需本机确认。


V4.2.8.20: The Windows installer retries scene detection import once and records exact import errors in install_logs/dependency_report.json. Existing data and shared runtimes are preserved.
