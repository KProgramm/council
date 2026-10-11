/* Verify provider drafts, key handling, and save failures using the actual UI script. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const elements = new Map();
const $ = id => {
  if (!elements.has(id)) elements.set(id, { value: '', checked: false, selectedOptions: [{ textContent: 'Brev / Ollama' }],
    attributes: {}, setAttribute(key, value) { this.attributes[key] = value; }, focus() {},
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
    if (path === '/settings/github') data = { ...data, github_token_configured: !JSON.parse(options.body).clear_github_token };
    return { ok: true, json: async () => data };
  },
});
vm.runInContext(fs.readFileSync('web/connections.js', 'utf8'), context);
vm.runInContext(fs.readFileSync('web/settings.js', 'utf8'), context);
(async () => {
  await new Promise(resolve => setImmediate(resolve));
  assert.equal($('llm-model').value, 'llama3.1:8b');
  assert.equal($('github-token').value, '', 'The UI must never load a saved secret into the field');
  assert.equal($('github-token-state').textContent, '· saved');
  assert.equal($('advanced-model').open, false, 'Default team setup hides detailed model settings');
  context.showConnectionTab('github');
  assert.equal($('settings-form').hidden, true);
  assert.equal($('github-settings-form').hidden, false);
  assert.equal($('connection-github-tab').attributes['aria-selected'], 'true');
  context.showConnectionTab('model');
  assert.equal($('settings-form').hidden, false);
  $('llm-model').value = 'another-ollama-model';
  $('llm-provider').value = 'paid';
  context.showProviderSettings();
  assert.equal($('llm-url-field').hidden, true);
  assert.equal($('advanced-model').open, true, 'API providers expose required model details');
  assert.equal($('llm-model').value, 'test/model');
  $('llm-provider').value = 'brev';
  context.showProviderSettings();
  assert.equal($('llm-model').value, 'another-ollama-model', 'Switching providers preserves unsaved drafts');
  const checksBefore = calls.length;
  await context.testConnection('model');
  assert.equal(calls.length, checksBefore, 'Checking with unsaved fields must not test a different saved model');
  assert($('settings-status').textContent.includes('Save your changes first'));
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
  context.debateBusy = false;
  $('github-token').value = '';
  $('repository').value = '';
  await context.testConnection('github');
  assert($('github-connection-status').textContent.includes('Enter a repository'));
  $('llm-concurrency').value = 2;
  assert(context.modelHasChanges(), 'Concurrency edits must also count as unsaved changes');
  failSave = false;
  $('github-auth-mode').value = 'public';
  context.showGitHubAccess();
  assert.equal($('github-token-field').hidden, true);
  assert(context.githubHasChanges(), 'Switching to public access is an explicit saved change');
  await context.saveGitHubSettings({ preventDefault() {} });
  const publicSave = JSON.parse(calls.filter(call => call.method === 'POST').at(-1).body);
  assert.equal(publicSave.clear_github_token, true);
  assert.equal(publicSave.github_token, null, 'Public mode never submits a leftover token');
  assert.equal($('llm-concurrency').value, 2, 'GitHub changes preserve the model draft');
  console.log('PASS: saved-key masking, provider drafts, secret submission/clearing, and safe retries.');
})().catch(error => { console.error(error); process.exitCode = 1; });
