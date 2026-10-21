# 依赖审计收尾

完整分析和官方来源见 `../DEPENDENCY_AUDIT.md`。旧环境、47 项候选、69 项 PyAV 19 失败组合及后续修复均保留独立记录，没有把失败候选晋升稳定版。

最终待冻结组合：Python 3.14.8、Node 22.23.3、PyInstaller 6.22.3、FFmpeg 9.0.2、Chromium 155.0.8059.84、Fixed WebView2 155.0.4283.62、Tesseract 5.5.3.20260724、Faster-Whisper 1.2.1/PyAV 18.1.0、TUF 7.0.1。69 项 Python 包的完整版本和 artifact SHA 记录在 `requirements/python-win-x64-py314-r39-candidate.*`。

实际完成：完整锁离线安装及 pip check；69 包安全公告查询零已知命中；固定语音权重本地转写及词时间戳；官方 Node SHA、SDK 测试；Chromium 免费 Film 组合；Microsoft Fixed WebView2 签名/解压；OCR 英文合成图识别；两次 Python 3.14 源码 wheel 重建 SHA 一致。源码 wheel 在 Python 3.12/3.14 下容器 SHA 不同但成员内容逐字节相同，最终锁采用可在 3.14 重现的产物。

pip-audit 不覆盖全部 native DLL。Tesseract 的 MSYS/第三方 DLL 再分发许可证尚未全部审定，不能声称完成商业许可审核。Node 官方已预告 2026-10-21 安全更新，本次日期为 10-20，不能虚构尚未发布的补丁已安装。

Dependabot 和每周只读元数据工作流已写入仓库；尚未在 GitHub 远程运行，无自动合并/升级正式依赖行为。
