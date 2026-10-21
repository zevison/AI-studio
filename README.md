# Seedance Studio 5.2.0 / R3.10 desktop candidate

当前为 Windows EXE 开发候选，**不能宣称正式稳定版**。桌面构建、锁定依赖和外部验收门槛见 [EXE 构建说明](docs/DESKTOP_BUILD.md)。[R3.8记录](docs/RELEASE_R3_8.txt) 作为历史证据保留，不能替代当前构建结果。

R3.10 增加完整简体中文安装器、统一视频业务图标、并行启动校验及应用网页身份验证。启动及故障测试见 [本轮交付范围](docs/RELEASE_R3_10.txt)，最终实际数值以安装包旁的验收报告为准。

这是可迁移的应用源码仓库，入口为 `app.py`。`core/`、`static/`、`assets/`、`tools/`、官方 Hypit Skill、npm锁文件、测试、字体和许可证都保留。密钥、用户数据和下载运行库不进入 Git。

- 新桌面版使用 `SeedanceStudio_Setup_*.exe` 安装，随后通过桌面或开始菜单启动。最终候选是否通过验收，以同包报告和 SHA256 为准；源码目录中的旧 `01_INSTALL.cmd` 属历史兼容安装流程。
- 开发及免费测试：`python -m pip install -r requirements-dev.txt`，`npm ci --ignore-scripts --prefix tools/hypit_provider`，`npm run build:offline --prefix tools/hypit_provider`，`python tools/dev_check.py`。
- GitHub私有发布、Codex Cloud 和完整Windows构建：[迁移及开发说明](docs/CLOUD_DEVELOPMENT.md)。
- 官方工作流：[Hypit使用说明](docs/Hypit_使用说明.txt)。
- 历史版本首页已完整保留：[历史说明](docs/history/README_LEGACY.md)。

Cloud不需要OpenLux或生产服务密钥。测试默认免费，不继承旧付费授权。当前没有证据证明新电脑安装、系统重启及完整外部业务流程已全部通过，不能因CI通过改变此结论。
