/* Seedance Studio V4.0 - General GPT memory/deletion UI.
   No product/video/ads or background collection calls.
 */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const api = async (route, data) => {
    const opts = data === undefined ? { cache: 'no-store' } : {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data), cache: 'no-store'
    };
    const res = await fetch(route, opts);
    let parsed;
    try { parsed = await res.json(); } catch (_) { throw new Error('本地接口返回异常'); }
    if (!res.ok || !parsed.ok) throw new Error(parsed.error || '操作未完成');
    return parsed.data;
  };
  let activeMemoryId = null;
  function create(tag, text, cls) {
    const e = document.createElement(tag);
    if (text !== undefined) e.textContent = text;
    if (cls) e.className = cls;
    return e;
  }
  function insertMemoryWindow() {
    if ($('gpt-v4-layer') || !$('page-assistant')) return;
    const layer = create('div'); layer.id = 'gpt-v4-layer'; layer.hidden = true;
    layer.setAttribute('role', 'dialog'); layer.setAttribute('aria-modal', 'true'); layer.setAttribute('aria-label', '本地 GPT 记忆管理');
    layer.innerHTML = `<div class="gpt-v4-window">
      <div class="gpt-v4-title"><h3>本地 GPT 记忆</h3><button type="button" id="gpt-v4-close">关闭 ×</button></div>
      <label><input type="checkbox" id="gpt-v4-enabled"> 在普通 GPT 对话中使用已保存记忆</label>
      <p class="gpt-v4-help">只有你明确点击保存的内容才会加入记忆，不会自动扫描聊天记录、商品库或视频任务。记忆保存在原工作台本地数据库，升级后保留。关闭后停止向模型发送记忆，但不会删除已保存内容。</p>
      <textarea id="gpt-v4-content" maxlength="800" placeholder="例如：回答时中文优先；所有英文附中文解释。"></textarea>
      <div class="gpt-v4-actions"><button type="button" class="primary" id="gpt-v4-save">保存记忆</button><button type="button" id="gpt-v4-cancel-edit">取消编辑</button></div>
      <div id="gpt-v4-status" role="status"></div>
      <div class="gpt-v4-list" id="gpt-v4-list"></div>
    </div>`;
    document.body.appendChild(layer);
    $('gpt-v4-close').onclick = () => { layer.hidden = true; };
    layer.addEventListener('click', e => { if (e.target === layer) layer.hidden = true; });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && !layer.hidden) layer.hidden = true; });
    $('gpt-v4-cancel-edit').onclick = () => { activeMemoryId = null; $('gpt-v4-content').value = ''; $('gpt-v4-save').textContent = '保存记忆'; };
    $('gpt-v4-enabled').onchange = async () => {
      try { await api('/api/gpt-v4/memory/config', { enabled: $('gpt-v4-enabled').checked });
        $('gpt-v4-status').textContent = $('gpt-v4-enabled').checked ? '已启用记忆' : '已关闭记忆（保存的内容未删除）';
      } catch (e) { $('gpt-v4-status').textContent = e.message; await refreshMemories(); }
    };
    $('gpt-v4-save').onclick = async () => {
      const content = $('gpt-v4-content').value.trim();
      if (!content) { $('gpt-v4-status').textContent = '请填写记忆内容'; return; }
      try { await api('/api/gpt-v4/memory/save', { content, id: activeMemoryId });
        activeMemoryId = null; $('gpt-v4-content').value = ''; $('gpt-v4-save').textContent = '保存记忆';
        $('gpt-v4-status').textContent = '已保存到本地记忆'; await refreshMemories();
      } catch (e) { $('gpt-v4-status').textContent = e.message; }
    };
  }
  async function refreshMemories() {
    const r = await api('/api/gpt-v4/memory');
    $('gpt-v4-enabled').checked = Boolean(r.enabled);
    const container = $('gpt-v4-list'); container.replaceChildren();
    if (!r.items.length) container.appendChild(create('div', '还没有保存的记忆。你可以在上方手动添加。', 'gpt-v4-help'));
    for (const mem of r.items) {
      const item = create('div', undefined, 'gpt-v4-entry');
      item.appendChild(create('div', mem.content));
      const row = create('div', undefined, 'gpt-v4-row');
      const edit = create('button', '修改');
      edit.onclick = () => { activeMemoryId = mem.id; $('gpt-v4-content').value = mem.content; $('gpt-v4-save').textContent = '保存修改'; $('gpt-v4-content').focus(); };
      const del = create('button', '删除记忆');
      del.onclick = async () => {
        if (!window.confirm('确定永久删除这条记忆吗？')) return;
        try { await api('/api/gpt-v4/memory/delete', { id: mem.id });
          $('gpt-v4-status').textContent = '已删除记忆'; await refreshMemories();
        } catch (e) { $('gpt-v4-status').textContent = e.message; }
      };
      row.append(edit, del); item.append(row); container.appendChild(item);
    }
  }
  async function openMemory(content) {
    insertMemoryWindow();
    if (!$('gpt-v4-layer')) return;
    $('gpt-v4-layer').hidden = false;
    activeMemoryId = null;
    $('gpt-v4-save').textContent = '保存记忆';
    $('gpt-v4-content').value = content || '';
    $('gpt-v4-status').textContent = '';
    try { await refreshMemories(); } catch(e) { $('gpt-v4-status').textContent = '记忆读取失败：' + e.message; }
  }
  function injectSidebar() {
    const side = $('page-assistant')?.querySelector('.gpt-side');
    if (!side || $('gptMemoryManage')) return;
    const btn = create('button', '🧠 管理记忆'); btn.id = 'gptMemoryManage'; btn.type = 'button';
    btn.onclick = () => openMemory();
    const settings = $('aiGotoSettings');
    if (settings) settings.before(btn); else side.appendChild(btn);
  }
  function patchHistoryRows() {
    const container = $('aiConversationList'); if (!container) return;
    const nodes = Array.from(container.querySelectorAll(':scope > .gpt-history[data-chat-id]'));
    for (const element of nodes) {
      const cid = element.dataset.chatId;
      if (!cid) continue;
      const wrap = create('div', undefined, 'gpt-history-row');
      element.before(wrap); wrap.appendChild(element);
      const deleteBtn = create('button', '×', 'gpt-history-delete');
      deleteBtn.type = 'button'; deleteBtn.title = '删除这条对话';
      deleteBtn.setAttribute('aria-label', '删除对话：' + (element.textContent || '未命名'));
      deleteBtn.onclick = async ev => {
        ev.stopPropagation();
        if (!window.confirm('确定永久删除这段聊天记录吗？这不会删除单独保存的长期记忆。')) return;
        deleteBtn.disabled = true;
        try {
          await api('/api/gpt-v4/conversation/delete', { conversation_id: cid, confirmed: true });
          const wasActive = element.classList.contains('active');
          wrap.remove();
          if (wasActive) {
            const another = container.querySelector('.gpt-history[data-chat-id]');
            if (another) another.click();
            else $('aiNew')?.click();
          }
        } catch (e) { window.alert('无法删除对话：' + e.message); deleteBtn.disabled = false; }
      };
      wrap.appendChild(deleteBtn);
    }
  }
  function formatAssistantBold() {
    // Render **bold** safely, without interpreting model text as untrusted HTML.
    const pane = $('aiMessages'); if (!pane) return;
    for (const text of pane.querySelectorAll('.gpt-bubble.assistant .gpt-answer')) {
      if (text.dataset.v4Rendered === '1') continue;
      text.dataset.v4Rendered = '1';
      const original = text.textContent;
      if (!original.includes('**')) continue;
      const parts = original.split(/(\*\*[^*\n]+\*\*)/g);
      text.replaceChildren();
      for (const part of parts) {
        if (!part) continue;
        if (part.startsWith('**') && part.endsWith('**') && part.length > 4)
          text.appendChild(create('strong',part.slice(2,-2)));
        else text.appendChild(document.createTextNode(part));
      }
    }
  }
  function patchRememberButtons() {
    const pane = $('aiMessages'); if (!pane) return;
    formatAssistantBold();
    for (const bubble of pane.querySelectorAll('.gpt-bubble.user')) {
      if (bubble.querySelector('.gpt-v4-save-memory')) continue;
      const content = bubble.querySelector('.gpt-answer'); if (!content) continue;
      const button = create('button', '保存为记忆', 'gpt-v4-save-memory'); button.type = 'button';
      button.onclick = () => openMemory(content.textContent.slice(0, 800));
      bubble.appendChild(button);
    }
  }
  function init() {
    if (!$('page-assistant')) return;
    injectSidebar(); insertMemoryWindow(); patchHistoryRows(); patchRememberButtons();
    const history = $('aiConversationList'), messages = $('aiMessages');
    if (history) new MutationObserver(patchHistoryRows).observe(history, { childList: true });
    if (messages) new MutationObserver(patchRememberButtons).observe(messages, { childList: true });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
