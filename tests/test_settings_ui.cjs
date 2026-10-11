/* Verify provider drafts, key handling, and save failures using the actual UI script. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const elements = new Map();
const $ = id => {
  if (!elements.has(id)) elements.set(id, { value: '', checked: false, selectedOptions: [{ textContent: 'Brev / Ollama' }],
    querySelectorAll() { return [...elements.values()]; } });
  return elements.get(id);
};
let data = { provider: 'brev', providers: {
  brev: { model: 'llama3.1:8b', base_url: 'http://127.0.0.1:11434' },
  cisco: { model: 'lab-model', base_url: 'https://lab.example.com' }, paid: { model: 'test/model', base_url: '' },
}, provider_keys_configured: { brev: true, cisco: false, paid: false }, team_model_url: 'https://team.example.com', max_concurrent_llm_calls: 1, github_token_configured: true, llm_api_key_configured: true };
let failSave = false;
const calls = [];
const context = vm.createContext({ $, HTTP_BASE: '', AbortController, setTimeout, clearTimeout, debateBusy: false,
  async fetch(path, options) {
    assert(['/settings', '/settings/github', '/settings/test'].includes(path));
    if (path === '/settings/test') return { ok: true, json: async () => ({ message: 'Connected.' }) };
    calls.push(options);
    if (options.method === 'POST' && failSave) return { ok: false, json: async () => ({ detail: 'Wait for review.' }) };
    return { ok: true, json: async () => data };
  },
});
vm.runInContext(fs.readFileSync('web/settings.js', 'utf8'), context);
(async () => {
  await new Promise(resolve => setImmediate(resolve));
  assert.equal($('llm-model').value, 'llama3.1:8b');
  assert.equal($('github-token').value, '', 'The UI must never load a saved secret into the field');
  assert.equal($('github-token-state').textContent, '· saved');
  $('llm-model').value = 'another-ollama-model';
  $('llm-provider').value = 'paid';
  context.showProviderSettings();
  assert.equal($('llm-url-field').hidden, true);
  assert.equal($('llm-model').value, 'test/model');
  $('llm-provider').value = 'brev';
  context.showProviderSettings();
  assert.equal($('llm-model').value, 'another-ollama-model', 'Switching providers preserves unsaved drafts');
  $('github-token').value = 'test-new-token';
  $('llm-api-key').value = 'test-new-api-key';
  await context.saveConnectionSettings({ preventDefault() {} });
  const saved = JSON.parse(calls.filter(call => call.method === 'POST').at(-1).body);
  assert.equal(saved.github_token, undefined, 'Model saves do not submit the GitHub credential');
  assert.equal(saved.llm_api_key, 'test-new-api-key');
  assert.equal($('github-token').value, 'test-new-token', 'Model saves preserve an unsaved GitHub token');
  assert.equal($('llm-api-key').value, '', 'Successful saves clear the secret fields');
  await context.saveGitHubSettings({ preventDefault() {} });
  assert.equal($('github-token').value, '', 'GitHub saves clear only the submitted GitHub key');
  failSave = true;
  $('github-token').value = 'test-retry-token';
  await context.saveConnectionSettings({ preventDefault() {} });
  assert.equal($('github-token').value, 'test-retry-token', 'Failed saves retain entered values for retry');
  assert.equal($('settings-status').textContent, 'Wait for review.');
  context.debateBusy = true;
  const count = calls.length;
  await context.saveConnectionSettings({ preventDefault() {} });
  assert.equal(calls.length, count, 'An active debate prevents a settings save');
  console.log('PASS: saved-key masking, provider drafts, secret submission/clearing, and safe retries.');
})().catch(error => { console.error(error); process.exitCode = 1; });
