Seedance Studio V3.9.0｜tools 目录说明

bootstrap_install.py        首次安装 / 升级依赖，自动调用完整本地分析安装器
install_analysis_stack.py   安装/修复 FFmpeg+FFprobe、OpenCV、PySceneDetect、Faster-Whisper、WhisperX、Tesseract OCR、MediaPipe、YOLO+ByteTrack
check_analysis.py           检查本地解析能力
watchdog.py                 后台守护 app.py，异常退出时恢复服务，带崩溃循环保护
start_silent.py             静默启动守护进程并打开网页
start_local.py              调试启动入口
stop_local.py               停止本地服务与守护进程
migration_bundle.py         创建/导入新电脑迁移包
backup_local.py             旧版兼容备份工具
migrate_old.py              旧版本配置/历史迁移
install_word_alignment.py   Faster-Whisper 语音时间轴辅助安装器（完整分析推荐使用 install_analysis_stack.py）

自动托管工具目录：
tools/ffmpeg/bin/           Windows 本地 FFmpeg / FFprobe（安装器按需下载）
tools/models/yolo11n.pt     YOLO 轻量本地模型（安装器按需下载并缓存）
