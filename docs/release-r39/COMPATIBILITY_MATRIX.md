# 兼容性证据

| 环境/事项 | 当前证据 | 限制 |
|---|---|---|
| 当前 Windows 11 Pro x64 build 22631 | 真实冻结启动、WebView2 页面、免费官方 Film、安装/重装/卸载已对 build3 执行 | 开发机；不等于当前完整依赖候选或全新 OS 通过 |
| Python 3.14.8 ABI | 真正冻结程序和 GUI 运行通过 | 最终全组件包须另验 |
| Windows 11 当前受支持版本、新用户首次安装 | 待真实环境验证 | 本机没有可用 Sandbox/Hyper-V/VM；不能冒充干净机器 |
| Windows 10 x64 | 静态最低 build 19041；Fixed WebView2 有专用沙箱 ACL | 未实机验证；普通支持已结束，维护条件需核实 |
| 中文/空格路径 | 当前主机真实 EXE 和隔离测试通过 | 不代表任意无限长路径；源码 wheel 缓存溢出已实际修复 |
| 无旧 Python/Node/FFmpeg/宿主浏览器 | 完整包设计随附运行库、冻结环境已测 | 最终独立包及干净 OS 门槛仍需对应证据 |
| AVX2 CPU | 本地 small ASR 实际转写通过 | 不支持 AVX2 的 CPU 跳过本地 ASR，不导入该 native 推理组件 |
| 125/150/200% 原生 DPI、多显示器 | 待真实环境验证 | 浏览器视口模拟不能冒充 Windows DPI 验收 |

当前主机为 23H2 Home/Pro 系列，其维护状态也不能当作受支持的新系统资格。微软说明 [Windows 11 23H2 Home/Pro 结束更新](https://learn.microsoft.com/en-au/lifecycle/announcements/windows-11-23h2-end-of-updates-home-pro)；Windows 10 普通支持于 2025-10-14 结束，见 [官方支持说明](https://support.microsoft.com/en-us/windows/deployment/updates-lifecycle/windows-10-support-has-ended-on-october-14-2025)。软件可启动与系统仍有安全维护是两项不同事实。
