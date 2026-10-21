Seedance Studio × Hypit × OpenLux — Stage4 连续开发快照（2026-10-16）

【注意/Risk】这是开发源码和离线测试，不是正式升级补丁/完整安装包。
不应覆盖现有 Seedance Studio V5.0.0 / V5.1.1 文件，也未将正式 OpenLux 付费生成打开。
本快照尚未注册为 @hypit/hypit/endpoint 官方 Provider，真实 Hypit Runtime 执行和 Windows 实机仍待验收。

一、真实完成的内容
1. core/hypit_agent_runtime.py：受限 Agent 文件工具和 Hypit check/plan 执行边界；当前只离线测试，未用账号验证真实 LLM。
2. core/hypit_openlux_gateway.py：模型和角色严格映射，SQLite 防重复提交与未知提交状态隔离。
3. core/hypit_provider_bridge.py：可配置（默认未验证、禁止提交）的 OpenLux HTTP 边界、独立 start/poll/collect 生命周期、费用上限、签名视频下载、SHA256 和 ffprobe 可播放性检查。
4. core/hypit_production_gate.py：正式付费前强制要求 Hypit check/plan 成功、工程源码和 Runtime 配置/Provider 包指纹不变，请求与预算授权匹配。指纹由服务端临时密钥签名，不能依赖前端确认状态。
5. src/openlux-contract.ts：既有 TypeScript 视频请求合同；保留真实 Hypit SDK 接入之前的离线桥接依据。

二、测试结果（当前隔离 Linux 环境，不是用户电脑）
- Python 单元/故障注入：93项，通过，测试脚本都使用 FakeTransport/Mock CLI。
- TypeScript 合同测试：28项，通过，运行 tsc 和 node --test（未依赖官方 Hypit SDK）。
- 本地 FFmpeg 生成 1 秒 MP4 → 受控模拟下载 → ffprobe 验证和 SHA256：通过。
- 未调用任何 OpenLux 账号、未产生视频生成费用。
- 覆盖缺少参考视频、角色丢弃、未知状态、重试重复扣费、旧批准、跨任务串号、恶意 URL、签名链接、损坏视频、Runtime/Profile 变更等风险。

三、强制限制及未完成事项
- OpenLux 的账号权限、模型参数、实际生成/查询 API 返回、任务去重语义、费率未在线验证。因此 VerifiedRoute.account_protocol_verified 默认 FALSE，无法付款提交；人工硬改布尔值不是验收。
- Hypit 0.3.1 @hypit/hypit SDK 不在当前环境，npm 公网访问失败。本快照不是已注册/编译的官方 Provider；需使用该 SDK 正确适配 endpoint activation、compileWireRequest、resources、checkpoint、pricing、Runtime Profile。
- 本地示例视频只是 FFmpeg 自生成，不证明正式模型生成效果。官方 Hypit CLI 真机 check/plan/build、Windows 服务恢复、Studio 可编辑工程尚未测试。
- SafeMp4Collector 限制 HTTPS、固定 CDN 域名、DNS 私网地址检查、禁止重定向和下载大小，但是用户电脑仍需要出站防火墙阻断 DNS 重绑定/内网 IP。不同平台 CDN 域名需先人工核实再加白名单。
- SpendEnvelope 只能由后端可信预算批准服务创建；绝不可让前端输入任意 contract_verified/approved_by_user 等布尔值。需要将批准持久化到安全存储并实现准确真实报价。
- Hypit 工程源码、Profile、Provider 包有指纹验证，但外部 HTTPS 参考素材在存储服务器端被换包仍可能变化，需要 OSS 对象版本/哈希绑定。

四、复测（本开发环境）
cd <本快照目录>
PYTHONPATH=. python -W error -m unittest discover -s tests -q
npm run test:offline  （需要 TypeScript 和 @types/node；已编译 JS 也可直接 node --test dist/tests/*.test.js）

五、下一开发优先级
1. 获取官方 @hypit/hypit@0.3.1 SDK 后改造为真实 Provider，并编译/安装到固定版本 Runtime。
2. 对 OpenLux 已配置账号仅做只读模型、报价/端点验证（LLM 工具调用可能产生 token 费用，需用户授权）；不开通视频自动扣费。
3. 免费 Runtime Build/Studio 编辑与素材复用端到端验收；校验全部成功再发 V5.0.0+ 累积补丁。
