/* One saved configuration drives connection checks and review execution. */
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
  $('use-team-model').hidden = settingsProvider !== 'brev';
  $('advanced-model').open = settingsProvider !== 'brev';
  $('llm-model').placeholder = settingsProvider === 'brev' ? 'e.g. llama3.1:8b'
    : settingsProvider === 'cisco' ? 'Model name from your lab' : 'provider/model, e.g. anthropic/your-model';
  $('llm-key-state').textContent = savedConnections?.provider_keys_configured?.[settingsProvider] ? '· saved' : '· enter a key';
  $('provider-help').textContent = settingsProvider === 'brev'
    ? 'Use your team key. Model and endpoint settings are under Advanced.'
    : settingsProvider === 'cisco' ? 'Enter your lab key, model, and service URL.'
    : 'Enter your provider key and provider/model name.';
  updateConnectionActions();
}

function modelHasChanges() {
  if (!savedConnections) return true;
  const provider = $('llm-provider').value;
  const saved = savedConnections.providers[provider] || {};
  return provider !== savedConnections.provider || $('llm-model').value.trim() !== saved.model
    || (provider !== 'paid' && $('llm-base-url').value.trim() !== saved.base_url)
    || Number($('llm-concurrency').value) !== savedConnections.max_concurrent_llm_calls
    || !!$('llm-api-key').value || $('clear-llm-api-key').checked;
}

function githubHasChanges() {
  return !savedConnections || ($('github-auth-mode').value === 'token') !== savedConnections.github_token_configured
    || !!$('github-token').value || $('clear-github-token').checked;
}

function showGitHubAccess() {
  const publicAccess = $('github-auth-mode').value === 'public';
  $('github-token-field').hidden = publicAccess;
  $('github-token-options').hidden = publicAccess;
  $('github-access-help').textContent = publicAccess
    ? (savedConnections?.github_token_configured ? 'Saving removes the saved token and uses public access.' : 'No token needed. Load a repository to check access.')
    : 'Use a token with Pull requests: Read permission.';
  updateConnectionActions();
}

function showConnectionTab(target, focus = false) {
  const model = target === 'model';
  $('settings-form').hidden = !model;
  $('github-settings-form').hidden = model;
  for (const name of ['model', 'github']) {
    const tab = $('connection-' + name + '-tab');
    const active = name === target;
    tab.setAttribute('aria-selected', String(active));
    tab.tabIndex = active ? 0 : -1;
    if (active && focus) tab.focus();
  }
}

function updateConnectionActions() {
  $('save-settings').textContent = modelHasChanges() ? 'Save & connect' : 'Test connection';
  $('save-github').textContent = githubHasChanges() ? 'Save & connect' : 'Test access';
}

function updateConnectionSummary() {
  if (!savedConnections) return;
  $('connection-summary').textContent = ` · ${savedConnections.providers[savedConnections.provider]?.model || 'choose a model'} · GitHub ${savedConnections.github_token_configured ? 'token saved' : 'public access'}`;
}

function renderConnectionSettings(data) {
  savedConnections = data;
  providerDrafts = {};
  settingsProvider = null;
  $('llm-provider').value = data.provider;
  $('llm-concurrency').value = data.max_concurrent_llm_calls;
  showProviderSettings();
  $('github-token-state').textContent = data.github_token_configured ? '· saved' : '· public access';
  $('github-auth-mode').value = data.github_token_configured ? 'token' : 'public';
  showGitHubAccess();
  $('llm-api-key').value = '';
  $('github-token').value = '';
  $('clear-llm-api-key').checked = $('clear-github-token').checked = false;
  updateConnectionSummary();
}

function connectionRequest(payload, path = '') {
  return requestJSON('/settings' + path, payload, path === '/test' ? 65000 : 10000);
}

function lockConnections(locked = settingsLoading) {
  const busy = debateBusy || (typeof watchState !== 'undefined' && (watchState?.enabled || watchState?.active));
  ['settings-form', 'github-settings-form'].forEach(id => {
    $(id).querySelectorAll('input, select, button').forEach(field => { field.disabled = locked; });
  });
  ['save-settings', 'save-github', 'use-team-model', 'reload-settings'].forEach(id => {
    const needsSave = id === 'save-settings' ? modelHasChanges() : id === 'save-github' ? githubHasChanges() : false;
    $(id).disabled = locked || !savedConnections || (busy && needsSave);
  });
  $('connection-lock-note').textContent = busy
    ? 'Pause reviews and wait for the active review before saving changes.' : '';
  $('connection-lock-note').hidden = !busy;
  updateConnectionActions();
  if (typeof updateGitHubControls === 'function') updateGitHubControls();
}

async function loadConnectionSettings() {
  if (settingsLoading) return;
  settingsLoading = true;
  lockConnections();
  try {
    renderConnectionSettings(await connectionRequest());
    $('settings-status').textContent = 'Saved model ready to test.';
  } catch (error) { $('settings-status').textContent = error.message; }
  finally { settingsLoading = false; lockConnections(); }
}

function canSaveConnection(statusId) {
  if (debateBusy || (typeof watchState !== 'undefined' && (watchState?.enabled || watchState?.active))) {
    $(statusId).textContent = 'Pause reviews and wait for the active review to finish before saving connections.';
    return false;
  }
  return !!savedConnections && !settingsLoading;
}

async function saveConnectionSettings(event) {
  event.preventDefault();
  if (!canSaveConnection('settings-status')) return;
  const payload = {
    provider: $('llm-provider').value, model: $('llm-model').value.trim(),
    base_url: $('llm-base-url').value.trim(), max_concurrent_llm_calls: Number($('llm-concurrency').value),
    llm_api_key: $('llm-api-key').value || null, clear_llm_api_key: $('clear-llm-api-key').checked,
  };
  settingsLoading = true;
  lockConnections();
  try {
    const data = await connectionRequest(payload);
    const draft = $('github-token').value, clear = $('clear-github-token').checked, githubMode = $('github-auth-mode').value;
    renderConnectionSettings(data);
    $('github-token').value = draft; $('clear-github-token').checked = clear;
    $('github-auth-mode').value = githubMode;
    showGitHubAccess();
    $('settings-status').textContent = 'Model saved. Checking the connection…';
    try { $('settings-status').textContent = (await connectionRequest({ target: 'model' }, '/test')).message; }
    catch (error) { $('settings-status').textContent = 'Saved, but connection check failed: ' + error.message; }
  } catch (error) { $('settings-status').textContent = error.message; }
  finally { settingsLoading = false; lockConnections(); }
}

async function saveGitHubSettings(event) {
  event.preventDefault();
  if (!canSaveConnection('github-connection-status')) return;
  const publicAccess = $('github-auth-mode').value === 'public';
  if (!publicAccess && !savedConnections.github_token_configured && !$('github-token').value.trim()) {
    $('github-connection-status').textContent = 'Enter a GitHub token, or choose public access.';
    return;
  }
  settingsLoading = true;
  lockConnections();
  try {
    const data = await connectionRequest({ github_token: publicAccess ? null : $('github-token').value || null,
      clear_github_token: publicAccess || $('clear-github-token').checked }, '/github');
    savedConnections.github_token_configured = data.github_token_configured;
    $('github-token-state').textContent = data.github_token_configured ? '· saved' : '· public access';
    $('github-token').value = ''; $('clear-github-token').checked = false;
    $('github-auth-mode').value = data.github_token_configured ? 'token' : 'public';
    showGitHubAccess();
    updateConnectionSummary();
    const repository = $('repository').value.trim();
    $('github-connection-status').textContent = 'Access saved. Load a repository to check it.';
    if (repository) {
      try { $('github-connection-status').textContent = (await connectionRequest({ target: 'github', repository }, '/test')).message; }
      catch (error) { $('github-connection-status').textContent = 'Saved, but access check failed: ' + error.message; }
    }
  } catch (error) { $('github-connection-status').textContent = error.message; }
  finally { settingsLoading = false; lockConnections(); }
}

async function testConnection(target) {
  if (settingsLoading || !savedConnections) return;
  const id = target === 'model' ? 'settings-status' : 'github-connection-status';
  if (target === 'model' ? modelHasChanges() : githubHasChanges()) {
    $(id).textContent = 'Save your changes first. This check uses the saved connection.';
    return;
  }
  if (target === 'github' && !$('repository').value.trim()) {
    $(id).textContent = 'Enter a repository below, then check access or load open PRs.';
    return;
  }
  settingsLoading = true;
  lockConnections();
  $(id).textContent = 'Checking saved connection…';
  try { $(id).textContent = (await connectionRequest({ target, repository: $('repository').value.trim() }, '/test')).message; }
  catch (error) { $(id).textContent = error.message; }
  finally { settingsLoading = false; lockConnections(); }
}

$('llm-provider').onchange = () => { $('llm-api-key').value = ''; $('clear-llm-api-key').checked = false; showProviderSettings(); };
$('use-team-model').onclick = () => {
  if (!savedConnections) return;
  $('llm-provider').value = 'brev';
  showProviderSettings();
  $('llm-model').value = 'llama3.1:8b';
  $('llm-base-url').value = savedConnections.team_model_url;
  $('llm-concurrency').value = 1;
  $('settings-status').textContent = 'Team model selected. Enter your team key and save the model connection.';
};
$('settings-form').onsubmit = event => {
  event.preventDefault();
  if (modelHasChanges()) saveConnectionSettings(event); else testConnection('model');
};
$('github-auth-mode').onchange = () => {
  $('github-token').value = '';
  $('clear-github-token').checked = false;
  showGitHubAccess();
};
$('github-settings-form').onsubmit = event => {
  event.preventDefault();
  if (githubHasChanges()) saveGitHubSettings(event); else testConnection('github');
};
for (const target of ['model', 'github']) {
  const tab = $('connection-' + target + '-tab');
  tab.onclick = () => showConnectionTab(target);
  tab.onkeydown = event => {
    if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      showConnectionTab(event.key === 'Home' ? 'model' : event.key === 'End' ? 'github' : target === 'model' ? 'github' : 'model', true);
    }
  };
}
for (const id of ['settings-form', 'github-settings-form']) {
  $(id).oninput = () => lockConnections();
  $(id).onchange = () => lockConnections();
}
$('reload-settings').onclick = loadConnectionSettings;
for (const id of ['llm-model', 'llm-base-url', 'llm-concurrency']) {
  $(id).oninvalid = () => { $('advanced-model').open = true; };
}
loadConnectionSettings();
