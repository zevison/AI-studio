/* Provider configuration: no key is stored in browser storage or exported. */
(() => {
  'use strict';
  let documentState = null;
  let token = '';
  const pendingKeys = Object.create(null);
  const labels = {chat: 'GPT 对话', image: '图片生成', video: '视频生成'};
  const pathLabels = {chat: '聊天请求', responses: '联网响应', models: '模型列表', image: '生成图片', image_edit: '编辑图片', video: '提交视频', video_query: '查询视频（保留 {task_id}）'};
  const runtimeLabels = {
    default_video_duration: ['默认视频时长（秒）', 4, 30],
    max_concurrent_video_tasks: ['视频任务并发上限', 1, 10],
    http_timeout: ['通用请求超时（秒）', 30, 300],
    query_retries: ['只读查询重试次数', 0, 5],
    poll_interval: ['视频状态查询间隔（秒）', 2, 120],
    asset_link_ttl_minutes: ['素材链接有效期（分钟）', 5, 10080],
    cache_retention_days: ['缓存保留天数', 1, 3650]
  };
  function element(tag, text, className) {
    const node = window.document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  async function api(path, body) {
    if (body && !token) token = (await api('/api/hypit/session')).token;
    const response = await fetch(path, {method: body ? 'POST' : 'GET', cache: 'no-store',
      headers: body ? {'Content-Type': 'application/json', 'X-Hypit-Session': token} : {},
      body: body ? JSON.stringify(body) : undefined});
    const value = await response.json();
    if (!response.ok || !value.ok) throw new Error(value.error || '配置请求失败');
    return value.data;
  }
  function field(parent, label, value, onChange, type = 'text') {
    const wrapper = element('label', undefined, 'provider-field');
    wrapper.append(element('span', label));
    const input = element('input'); input.type = type; input.value = value ?? '';
    input.autocomplete = type === 'password' ? 'new-password' : 'off';
    input.addEventListener('input', () => onChange(type === 'number' ? Number(input.value) : input.value));
    wrapper.append(input); parent.append(wrapper); return input;
  }
  function selector(parent, label, choices, value, onChange) {
    const wrapper = element('label', undefined, 'provider-field'); wrapper.append(element('span', label));
    const select = element('select');
    for (const [id, name] of choices) {const option = element('option', name); option.value = id; select.append(option);}
    select.value = value; select.addEventListener('change', () => onChange(select.value));
    wrapper.append(select); parent.append(wrapper); return select;
  }
  function button(parent, label, callback) {
    const node = element('button', label); node.type = 'button'; node.addEventListener('click', callback); parent.append(node); return node;
  }
  function report(text, bad = false) {
    const status = window.document.getElementById('provider-config-status');
    if (status) {status.textContent = text; status.setAttribute('role', bad ? 'alert' : 'status');}
  }
  function announceSaved() {
    const provider = documentState.providers.find(row => row.id === documentState.defaults.chat);
    window.document.dispatchEvent(new CustomEvent('studio-configuration-saved', {
      detail: {revision: documentState.revision, chat_model: provider?.models.chat || ''}
    }));
  }
  function cloneProviderAccount(source) {
    const row = structuredClone(source);
    row.id = 'provider-' + crypto.randomUUID().replaceAll('-', '').slice(0, 16);
    row.name = '新服务账号'; row.key_configured = false; row.legacy_credential = false; row.enabled = true;
    delete row.credential_ref;
    return row;
  }
  function providerCard(provider, cards) {
    const card = element('section', undefined, 'provider-card');
    const heading = element('div', undefined, 'provider-heading');
    heading.append(element('h4', provider.name));
    button(heading, '删除此配置', () => {
      if (Object.values(documentState.defaults).includes(provider.id)) return report('请先把默认服务切换到其他已启用配置，再删除。', true);
      documentState.providers = documentState.providers.filter(x => x.id !== provider.id); delete pendingKeys[provider.id]; render();
    }); card.append(heading);
    const grid = element('div', undefined, 'provider-grid');
    field(grid, '配置名称（可区分账号）', provider.name, value => {provider.name = value;});
    selector(grid, '接口协议', [['openlux', 'OpenLux 图片 / 视频 / 聊天'], ['openai_chat', '自定义 OpenAI 兼容聊天（需自行验证）']], provider.adapter, value => {provider.adapter = value;});
    selector(grid, '状态', [['yes', '已启用'], ['no', '已停用']], provider.enabled ? 'yes' : 'no', value => {provider.enabled = value === 'yes';});
    field(grid, 'Base URL（公网 HTTPS）', provider.base_url, value => {provider.base_url = value;}, 'url');
    const key = field(grid, provider.key_configured ? 'API Key（已保存；留空保持原值）' : 'API Key（保存在此 Windows 用户的安全凭据中）', '', value => {if (value) pendingKeys[provider.id] = value; else delete pendingKeys[provider.id];}, 'password');
    key.dataset.providerSecret = 'true';
    button(grid, '清除此配置的密钥（保存后生效）', () => {
      pendingKeys[provider.id] = ''; provider.key_configured = false; key.value = '';
      report('已标记清除当前配置密钥；点击“保存全部配置”生效。备份版本的加密凭据仍保留用于恢复。');
    });
    const timeout = field(grid, '此账号的请求超时（秒）', provider.timeout, value => {provider.timeout = value;}, 'number'); timeout.min = '5'; timeout.max = '300';
    for (const kind of Object.keys(labels)) field(grid, labels[kind] + '模型 ID', provider.models[kind], value => {provider.models[kind] = value;});
    card.append(grid);
    const advanced = element('details'); advanced.append(element('summary', '高级：接口路径和已适配参数'));
    const paths = element('div', undefined, 'provider-grid');
    for (const [name, label] of Object.entries(pathLabels)) field(paths, label, provider.paths[name], value => {provider.paths[name] = value;});
    advanced.append(paths);
    const params = element('div', undefined, 'provider-grid');
    for (const [name, label, min, max] of [['temperature', '聊天随机性（留空由模型决定）', 0, 2], ['top_p', '聊天采样范围（留空由模型决定）', 0, 1], ['max_tokens', '聊天输出 Token 上限（留空由模型决定）', 1, 32768]]) {
      const input = field(params, label, provider.parameters.chat[name] ?? '', value => {
        if (input.value === '') delete provider.parameters.chat[name]; else provider.parameters.chat[name] = value;
      }, 'number'); input.min = String(min); input.max = String(max); input.step = name === 'max_tokens' ? '1' : '0.1';
    }
    advanced.append(params, element('p', '图片和视频仍使用现有已适配的工作流参数。自定义模型能否使用这些参数，以该模型实际协议为准；保存不会发起付费测试。', 'muted'));
    card.append(advanced, element('p', provider.verification || '协议适配；尚未验证当前账号及模型能力', 'muted')); cards.append(card);
  }
  function render() {
    const host = window.document.getElementById('provider-configuration'); if (!host || !documentState) return;
    host.replaceChildren(element('h3', '模型与服务账号'));
    host.append(element('p', '可以分别设置聊天、图片和视频账号。密钥仅保存在当前 Windows 用户中；更换电脑需重新输入。现有 OSS、热点采集和业务设置仍在下方。', 'muted'));
    host.append(element('p', 'Hypit 官方工作流、余额和 OpenLux 官方报价使用原有官方账号；下方原有 OpenLux 账户区可管理账户查询凭据。自定义聊天或视频接口不代表已通过官方兼容验收，也不共用余额口径。', 'muted'));
    const status = element('p', documentState.error || documentState.credential_error || '配置保存后立即用于新请求；执行中或结果未知的任务会阻止账号切换。'); status.id = 'provider-config-status'; status.setAttribute('aria-live', 'polite'); host.append(status);
    if (documentState.credential_error && !documentState.configuration_error) {
      const recovery = element('details'); recovery.append(element('summary', '凭据无法读取：保留损坏备份并重新授权'));
      recovery.append(element('p', '仅用于损坏或其他 Windows 用户加密的凭据。原加密文件会保留带校验值的独立备份；该凭据文件中的其他账号、OSS 和账户查询密钥需重新填写。正常凭据不会被清空。', 'muted'));
      const fields = element('div', undefined, 'provider-grid'); let vaultKind = 'provider', profileId = documentState.defaults.chat;
      selector(fields, '需要恢复的凭据文件', [['provider', '服务商账号凭据'], ['legacy', '原有 OpenLux / OSS / 账户查询凭据']], vaultKind, value => {vaultKind = value;});
      selector(fields, '服务商凭据恢复到哪个账号（原有凭据恢复时忽略）', documentState.providers.map(row => [row.id, row.name]), profileId, value => {profileId = value;});
      const newKey = field(fields, '重新授权的新 API Key', '', () => {}, 'password'); recovery.append(fields);
      const recover = button(recovery, '保留损坏凭据备份并重新授权', async () => {
        if (!newKey.value.trim()) return report('请先填写新的有效 API Key。', true);
        if (!window.confirm('确认保留损坏加密文件备份，并为所选凭据文件重新授权？该文件中的其他敏感字段需要重新填写。正常凭据不会清空。')) return;
        recover.disabled = true;
        try {
          const values = {[vaultKind === 'provider' ? profileId : 'openlux_api_key']: newKey.value.trim()};
          documentState = await api('/api/configuration/reauthorize', {vault_kind: vaultKind, new_values: values, confirmed: true, expected_revision: documentState.revision});
          newKey.value = ''; for (const key of Object.keys(pendingKeys)) delete pendingKeys[key];
          announceSaved(); render(); report('已重新授权，原损坏凭据已校验备份。请重新填写该凭据文件中的其他账号、OSS 或账户查询字段。');
        } catch (error) {report(error.message, true);} finally {recover.disabled = false;}
      });
      host.append(recovery);
    }
    const defaults = element('div', undefined, 'provider-grid');
    for (const kind of Object.keys(labels)) selector(defaults, labels[kind] + '默认账号', documentState.providers.map(x => [x.id, x.name]), documentState.defaults[kind], value => {documentState.defaults[kind] = value;});
    host.append(defaults);
    const cards = element('div'); for (const provider of documentState.providers) providerCard(provider, cards); host.append(cards);
    const runtime = element('details'); runtime.append(element('summary', '运行参数（留空沿用原有设置）'));
    const runtimeGrid = element('div', undefined, 'provider-grid');
    for (const [key, label] of [['output_directory', '视频输出目录'], ['ffmpeg_path', 'FFmpeg 程序路径'], ['ffprobe_path', 'FFprobe 程序路径'], ['chromium_path', 'Chromium 程序路径']]) {
      field(runtimeGrid, label + '（绝对本地路径；留空自动管理；重启生效）', documentState.runtime[key] || '', value => {
        if (!value) delete documentState.runtime[key]; else documentState.runtime[key] = value;
      });
    }
    for (const [key, [label, min, max]] of Object.entries(runtimeLabels)) {
      const input = field(runtimeGrid, label, documentState.runtime[key] ?? '', value => {
        if (!input.value) delete documentState.runtime[key]; else documentState.runtime[key] = value;
      }, 'number'); input.min = String(min); input.max = String(max);
    }
    runtime.append(runtimeGrid); host.append(runtime);
    const actions = element('div', undefined, 'provider-actions');
    button(actions, '＋ 添加账号', () => {
      const row = cloneProviderAccount(documentState.providers[0]);
      documentState.providers.push(row); render();
    });
    const save = button(actions, '保存全部配置', async () => {
      save.disabled = true;
      try {
        documentState = await api('/api/configuration', {document: documentState, expected_revision: documentState.revision, secret_updates: pendingKeys});
        for (const key of Object.keys(pendingKeys)) delete pendingKeys[key];
        announceSaved();
        render(); report('已安全保存。新请求立即使用新配置，不会自动调用付费模型。');
      } catch (error) {report(error.message, true);} finally {save.disabled = false;}
    });
    button(actions, '重新载入', async () => {try {documentState = await api('/api/configuration'); render();} catch (error) {report(error.message, true);}});
    button(actions, '导出设置（不含密钥）', async () => {
      try {
        const data = await api('/api/configuration/export');
        const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'}));
        const link = element('a'); link.href = url; link.download = 'SeedanceStudio_Configuration.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 5000);
      } catch (error) {report(error.message, true);}
    });
    const file = element('input'); file.type = 'file'; file.accept = '.json,application/json'; file.hidden = true;
    file.addEventListener('change', async () => {
      try {
        if (!file.files[0] || file.files[0].size > 1024 * 1024) throw new Error('请选择小于 1MB 的配置 JSON 文件');
        const imported = JSON.parse(await file.files[0].text());
        documentState = await api('/api/configuration/import', {document: imported, expected_revision: documentState.revision}); announceSaved(); render(); report('已导入非敏感设置；新电脑请重新输入密钥。');
      } catch (error) {report(error.message, true);} finally {file.value = '';}
    });
    button(actions, '导入设置', () => file.click()); actions.append(file); host.append(actions);
  }
  async function mount() {
    const page = window.document.getElementById('page-settings'); if (!page) return;
    if (!window.document.getElementById('provider-configuration')) {
      const host = element('section', undefined, 'card'); host.id = 'provider-configuration'; page.prepend(host);
      const style = element('style');
      style.textContent = '#provider-configuration{padding:22px;margin-bottom:20px}#provider-configuration .provider-card{border:1px solid var(--border,#69758655);border-radius:12px;padding:16px;margin:16px 0}#provider-configuration .provider-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;margin:14px 0}#provider-configuration .provider-field{display:flex;flex-direction:column;gap:7px;font-size:13px}#provider-configuration .provider-field input,#provider-configuration .provider-field select{width:100%;box-sizing:border-box;min-height:38px}#provider-configuration .provider-heading,#provider-configuration .provider-actions{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}#provider-configuration summary{cursor:pointer;padding:10px 0}#provider-configuration .provider-actions{justify-content:flex-start;margin-top:18px}#provider-config-status[role=alert]{color:#e27669}';
      page.append(style);
    }
    try {documentState = await api('/api/configuration'); render();} catch (error) {window.document.getElementById('provider-configuration').textContent = '服务账号设置暂不可用：' + error.message;}
  }
  if (window.document.readyState === 'loading') window.document.addEventListener('DOMContentLoaded', mount); else mount();
})();
