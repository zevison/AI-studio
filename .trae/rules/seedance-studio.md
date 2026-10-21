# Seedance Studio 项目规则

## 架构约束

- 保留现有架构：Python 3 单进程 `http.server` 后端（`app.py`）+ `static/` 前端 + `core/` 模块 + `tools/` 构建与运行组件。不得另建简化版系统替代。
- 前端只通过 `/api/*` 访问后端；后端统一返回 `{"ok":true,"data":...}`，前端 `api()` 已解包为 `data`，失败时抛出异常。
- 后台耗时任务不得阻塞 UI；进度必须来自真实任务状态，禁止固定假进度。

## 版本与发布

- `core/config.py` 的 `APP_VERSION` / `APP_BUILD` 是唯一版本来源；发布前必须提升 `APP_BUILD`。
- 提升版本时同步更新 `static/index.html` 中 `app.js?v=<build>`，用于破缓存。
- 发布流程：构建 EXE → 创建 GitHub Release → 更新 `docs/latest-release.json`（version/build/installer_url/installer_sha256）→ 推送。
- 老版本必须能直接升级到最新版；升级不得删除用户 API 密钥、商品资料、项目与素材。

## 数据保护

- 用户数据在 `%LOCALAPPDATA%\SeedanceStudio`（frozen 模式，含 `.env`、数据库、项目）。任何迁移/清理前先备份。
- 禁止把 `data/`、`.env`、`*.dpapi`、`*.key` 纳入源码或安装包。

## 构建工具链（锁定）

- Python：3.14.8，依赖必须与 `requirements/python-win-x64-py314-r39-candidate.lock.txt` 完全一致。
- Inno Setup：6.4.3，逐文件 SHA 必须匹配 `tools/desktop-toolchain.lock.json`。
- 网络受限时：ASR 模型可经 `--asr-model-directory` 提供（逐文件 SHA 校验），其余组件走 `--cache` 复用。

```powershell
& <py314>\python.exe tools\build_desktop_exe.py --output <out> --cache <cache> `
  --iscc <inno643>\ISCC.exe --git "C:\Program Files\Git\cmd\git.exe" `
  --asr-model-directory <asr-model>
```

## 成本与付费安全

- 任何付费视频生成必须经用户确认后执行；禁止批量自动付费任务。
- 超时或未知状态先查询原任务结果，禁止直接重新提交。
- 费用必须区分「实际扣费」与「估算」，估算不得标记为实际消费。

## 完成度纪律

- 未实际运行/验证的功能不得报告为完成；无凭据的第三方（Bright Data / OpenLux / Hypit）只能标注「未验证」或「需实机测试」。
- 修复必须定位共同根因，不为同一问题不断产出新的临时脚本。
