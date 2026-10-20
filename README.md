# Seedance Studio 5.2.0 / R3.8 candidate

当前状态为 **B：待最终验收版**，不是正式稳定版。最新真实测试及剩余门槛见 [R3.8记录](docs/RELEASE_R3_8.txt)。

这是可迁移的应用源码仓库，入口为 `app.py`。`core/`、`static/`、`assets/`、`tools/`、官方 Hypit Skill、npm锁文件、测试、字体和许可证都保留。密钥、用户数据和下载运行库不进入 Git。

- 同事使用完整安装 ZIP：`Studio/01_INSTALL.cmd` 安装，`Studio/00_START.cmd` 启动。源码克隆需要先按下面说明重建运行库和完整包。
- 开发及免费测试：`python -m pip install -r requirements-dev.txt`，`npm ci --ignore-scripts --prefix tools/hypit_provider`，`npm run build:offline --prefix tools/hypit_provider`，`python tools/dev_check.py`。
- GitHub私有发布、Codex Cloud 和完整Windows构建：[迁移及开发说明](docs/CLOUD_DEVELOPMENT.md)。
- 官方工作流：[Hypit使用说明](docs/Hypit_使用说明.txt)。
- 历史版本首页已完整保留：[历史说明](docs/history/README_LEGACY.md)。

Cloud不需要OpenLux或生产服务密钥。测试默认免费，不继承旧付费授权。当前没有证据证明新电脑安装、系统重启及完整外部业务流程已全部通过，不能因CI通过改变此结论。
