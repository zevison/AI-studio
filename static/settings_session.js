/* Session-scoped configuration writes. Tokens stay in memory, never localStorage. */
(() => {
  const original = window.fetch.bind(window);
  const protectedPaths = new Set([
    '/api/configuration', '/api/configuration/import', '/api/configuration/reauthorize', '/api/settings', '/api/settings/reset',
    '/api/openlux/account-credentials', '/api/openlux/account-clear', '/api/oss/config',
    '/api/oss/clear-credentials', '/api/bright-tiktok/settings/save', '/api/shutdown',
    '/api/desktop/migrate',
    '/api/updates/check', '/api/updates/download', '/api/updates/install', '/api/updates/cancel',
    '/api/components/download', '/api/components/cancel', '/api/components/preferences',
    '/api/components/need', '/api/components/import', '/api/components/repair',
    '/api/components/uninstall', '/api/components/export', '/api/components/check', '/api/components/update',
    '/api/components/clear-cache', '/api/components/rollback',
  ]);
  let session;
  async function token() {
    if (!session) session = original('/api/hypit/session').then(async response => {
      const body = await response.json();
      if (!response.ok || !body.ok) throw new Error('无法取得本机设置会话，请重新打开软件');
      return body.data.token;
    }).catch(error => { session = undefined; throw error; });
    return session;
  }
  window.fetch = async function(input, options) {
    const target = new URL(typeof input === 'string' ? input : input.url, window.location.href);
    const method = (options?.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    if (target.origin === window.location.origin && method === 'POST' && protectedPaths.has(target.pathname)) {
      const headers = new Headers(options?.headers || (input instanceof Request ? input.headers : undefined));
      headers.set('X-Hypit-Session', await token());
      options = {...options, headers};
    }
    return original(input, options);
  };
})();
