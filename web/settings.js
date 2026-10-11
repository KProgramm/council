/* Model and GitHub saves are independent. Secret values never leave input fields for storage in the browser. */
let savedConnections = null;
let settingsLoading = false;
let settingsProvider = null;
let providerDrafts = {};

function showProviderSettings() {
  if (settingsProvider) providerDrafts[settingsProvider] = { model: $('llm-model').value, base_url: $('llm-base-url').value };
  settingsProvider = $('llm-provider').value;
  const config = providerDrafts[settingsProvider] || savedConnections?.providers[settingsProvider] || {};
  $('llm-model').value = config.model || '';
  $('llm-base-url').value = config.base_url || '';
  $('llm-url-field').hidden = settingsProvider === 'paid';
  $('llm-base-url').required = settingsProvider !== 'paid';
  $('llm-key-state').textContent = savedConnections?.provider_keys_configured?.[settingsProvider] ? '· saved' : '· enter a key';
  $('provider-help').textContent = settingsProvider === 'brev'
    ? 'The team Brev model uses llama3.1:8b. Shared models need the team key; local Ollama can connect without one.'
    : settingsProvider === 'cisco' ? 'Use the model, HTTPS proxy URL, and key from your lab.'
    : 'Use a provider/model name and its API key. Checking this connection makes a small model request.';
}

function renderConnectionSettings(data) {
  savedConnections = data;
  providerDrafts = {};
  settingsProvider = null;
  $('llm-provider').value = data.provider;
  $('llm-concurrency').value = data.max_concurrent_llm_calls;
  showProviderSettings();
  $('github-token-state').textContent = data.github_token_configured ? '· saved' : '· public access';
  $('llm-api-key').value = '';
  $('github-token').value = '';
  $('clear-llm-api-key').checked = $('clear-github-token').checked = false;
  $('connection-summary').textContent = ` · ${data.providers[data.provider]?.model || 'choose a model'} · GitHub ${data.github_token_configured ? 'token saved' : 'public access'}`;
}

async function connectionRequest(payload, path = '') {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), path === '/test' ? 65000 : 10000);
  try {
    const response = await fetch(HTTP_BASE + '/settings' + path, {
      method: payload ? 'POST' : 'GET', cache: 'no-store', signal: controller.signal,
      headers: payload ? { 'Content-Type': 'application/json' } : {},
      ...(payload ? { body: JSON.stringify(payload) } : {}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not update connections.');
    return data;
  } finally { clearTimeout(timeout); }
}

function lockConnections(locked) {
  ['settings-form', 'github-settings-form'].forEach(id => {
    $(id).querySelectorAll('input, select, button').forEach(field => { field.disabled = locked; });
  });
}

async function loadConnectionSettings() {
  if (settingsLoading) return;
  settingsLoading = true;
  lockConnections(true);
  try {
    renderConnectionSettings(await connectionRequest());
    $('settings-status').textContent = 'Saved connection loaded. Blank key fields keep the saved key for this provider.';
  } catch (error) { $('settings-status').textContent = error.message; }
  finally { settingsLoading = false; lockConnections(false); $('save-settings').disabled = !savedConnections; }
}

async function saveConnectionSettings(event) {
  event.preventDefault();
  if (settingsLoading) return;
  if (debateBusy) { $('settings-status').textContent = 'Wait for the review to finish before changing connections.'; return; }
  const payload = {
    provider: $('llm-provider').value, model: $('llm-model').value.trim(),
    base_url: $('llm-base-url').value.trim(), max_concurrent_llm_calls: Number($('llm-concurrency').value),
    llm_api_key: $('llm-api-key').value || null, clear_llm_api_key: $('clear-llm-api-key').checked,
  };
  settingsLoading = true;
  lockConnections(true);
  try {
    const data = await connectionRequest(payload);
    // A model save must not discard an unsaved GitHub token.
    const draft = $('github-token').value, clear = $('clear-github-token').checked;
    renderConnectionSettings(data);
    $('github-token').value = draft; $('clear-github-token').checked = clear;
    $('settings-status').textContent = 'Model saved. Checking the connection…';
    try { $('settings-status').textContent = (await connectionRequest({ target: 'model' }, '/test')).message; }
    catch (error) { $('settings-status').textContent = 'Saved, but connection check failed: ' + error.message; }
  } catch (error) { $('settings-status').textContent = error.message; }
  finally { settingsLoading = false; lockConnections(false); }
}

async function saveGitHubSettings(event) {
  event.preventDefault();
  if (settingsLoading) return;
  settingsLoading = true;
  lockConnections(true);
  try {
    const data = await connectionRequest({ github_token: $('github-token').value || null,
      clear_github_token: $('clear-github-token').checked }, '/github');
    savedConnections.github_token_configured = data.github_token_configured;
    $('github-token-state').textContent = data.github_token_configured ? '· saved' : '· public access';
    $('github-token').value = ''; $('clear-github-token').checked = false;
    $('github-connection-status').textContent = 'GitHub connection saved. Checking repository access…';
    try { $('github-connection-status').textContent = (await connectionRequest({ target: 'github', repository: $('repository').value.trim() }, '/test')).message; }
    catch (error) { $('github-connection-status').textContent = 'Saved, but access check failed: ' + error.message; }
  } catch (error) { $('github-connection-status').textContent = error.message; }
  finally { settingsLoading = false; lockConnections(false); }
}

async function testConnection(target) {
  const id = target === 'model' ? 'settings-status' : 'github-connection-status';
  $(id).textContent = 'Checking saved connection…';
  try { $(id).textContent = (await connectionRequest({ target, repository: $('repository').value.trim() }, '/test')).message; }
  catch (error) { $(id).textContent = error.message; }
}

$('llm-provider').onchange = () => { $('llm-api-key').value = ''; $('clear-llm-api-key').checked = false; showProviderSettings(); };
$('use-team-model').onclick = () => {
  $('llm-provider').value = 'brev';
  showProviderSettings();
  $('llm-model').value = 'llama3.1:8b';
  $('llm-base-url').value = savedConnections.team_model_url;
  $('llm-concurrency').value = 1;
  $('settings-status').textContent = 'Team model selected. Enter your team key and save the model connection.';
};
$('settings-form').onsubmit = saveConnectionSettings;
$('github-settings-form').onsubmit = saveGitHubSettings;
$('test-model').onclick = () => testConnection('model');
$('test-github').onclick = () => testConnection('github');
$('reload-settings').onclick = loadConnectionSettings;
loadConnectionSettings();
