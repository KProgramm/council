/* The server owns watching, deduplication, execution, and durable review results. */
let loadedPR = null;
let watchState = null;
let watchLoading = false;
let selectedReview = null;
let displayedReview = null;
let displayedCount = 0;
let displayedValidity = null;
let refreshTimer = null;
let refreshGeneration = 0;
let refreshLoading = false;
let viewGeneration = 0;
let repositoryPreview = null;
let pickedPRs = new Set();
let scopeInitialized = false;
let repositoryEdited = false;
let queueUnavailable = false;
let scopeEdited = false;

function updateGitHubControls() {
  const automatic = !!watchState?.enabled || !!watchState?.active;
  $('run').disabled = debateBusy || automatic;
  const connecting = typeof settingsLoading !== 'undefined' && settingsLoading;
  const selected = repositoryPreview ? pickedPRs.size > 0
    : !repositoryEdited && watchState?.repository === $('repository').value.trim() && !!watchState?.selected_prs?.length;
  const ready = $('review-mode').value === 'all' || selected;
  $('watch-repository').disabled = watchLoading || debateBusy || connecting || queueUnavailable || !ready;
  $('load-prs').disabled = watchLoading || connecting;
  $('repository').disabled = watchLoading;
  $('review-mode').disabled = watchLoading;
  $('stop-watching').disabled = watchLoading || !watchState?.enabled;
}

async function watchRequest(path = '', payload) {
  return requestJSON('/watch' + path, payload);
}

function updateScope() {
  const all = $('review-mode').value === 'all';
  $('scope-help').textContent = all
    ? 'All open, non-draft PRs will be queued, including future PRs and changed revisions. Ignored PRs stay excluded.'
    : 'Only selected PRs and their changed revisions will be reviewed. New PRs wait for you to select them.';
  $('pr-picker').hidden = all || !repositoryPreview;
  if (repositoryPreview) selectionSummary();
  const resume = watchState?.repository && !watchState.enabled && !repositoryEdited && !repositoryPreview
    && $('review-mode').value === watchState.mode;
  $('watch-repository').textContent = resume ? 'Resume reviews' : all ? 'Start all-PR reviews' : 'Start selected reviews';
  updateGitHubControls();
}

function renderPicker() {
  const list = $('pr-picker');
  list.replaceChildren();
  if (!repositoryPreview) { list.hidden = true; return; }
  for (const pr of repositoryPreview.pull_requests) {
    const item = el('li', 'pr-item');
    const label = el('label', 'github-option');
    const checkbox = el('input');
    checkbox.type = 'checkbox';
    checkbox.checked = pickedPRs.has(pr.number);
    checkbox.disabled = pr.draft;
    checkbox.onchange = () => {
      if (checkbox.checked) pickedPRs.add(pr.number); else pickedPRs.delete(pr.number);
      selectionSummary();
    };
    label.append(checkbox, el('span', '', `#${pr.number} ${pr.title} · ${pr.author}${pr.draft ? ' · draft (skipped)' : ''}`));
    item.append(label);
    list.append(item);
  }
  selectionSummary();
  updateScope();
}

function selectionSummary() {
  if (!repositoryPreview) return;
  const eligible = repositoryPreview.pull_requests.filter(pr => !pr.draft && !(watchState?.repository === repositoryPreview.repository && watchState.ignored_prs?.includes(pr.number))).length;
  $('pr-selection-status').textContent = $('review-mode').value === 'all'
    ? `${repositoryPreview.pull_requests.length} open PRs · ${eligible} non-draft PRs in automatic scope`
    : `${repositoryPreview.pull_requests.length} open PRs · ${pickedPRs.size} selected`;
  $('pr-selection-status').textContent +=
    (repositoryPreview.incomplete ? ' · GitHub list limited to the newest 1,000 PRs.' : '')
    + (!repositoryPreview.pull_requests.length ? ' · No open PRs in this repository.' : '');
  updateGitHubControls();
}

async function loadOpenPRs() {
  if (watchLoading || (typeof settingsLoading !== 'undefined' && settingsLoading)) return;
  if (typeof githubHasChanges === 'function' && githubHasChanges()) {
    $('connection-settings').open = true;
    $('pr-selection-status').textContent = 'Save the GitHub token changes first, then load open PRs.';
    return;
  }
  watchLoading = true;
  updateGitHubControls();
  const repository = $('repository').value.trim();
  $('pr-selection-status').textContent = 'Loading open PRs using saved GitHub access…';
  try {
    const data = await requestJSON('/github/repository', { repository });
    const previous = repositoryPreview?.repository === data.repository ? pickedPRs
      : watchState?.repository === data.repository ? new Set(watchState.selected_prs || []) : new Set();
    repositoryPreview = data;
    pickedPRs = new Set(data.pull_requests.filter(pr => !pr.draft && previous.has(pr.number)).map(pr => pr.number));
    $('repository').value = data.repository;
    renderPicker();
  } catch (error) {
    repositoryPreview = null;
    pickedPRs.clear();
    renderPicker();
    $('pr-selection-status').textContent = error.message;
    if ([401, 403].includes(error.status)) {
      $('connection-settings').open = true;
      $('github-connection-status').textContent = error.message;
      if (typeof showConnectionTab === 'function') showConnectionTab('github');
    }
  } finally { watchLoading = false; updateGitHubControls(); }
}

function renderPRSource() {
  const source = $('pr-source');
  source.replaceChildren();
  source.hidden = !loadedPR;
  if (!loadedPR) return;
  const link = el('a', '', `${loadedPR.repository} #${loadedPR.number}: ${loadedPR.title}`);
  link.href = loadedPR.url;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  source.append(link, el('p', 'github-note', `Revision ${loadedPR.head_sha.slice(0, 7)} · ${loadedPR.author}`));
}

function renderWatch(data) {
  watchState = data;
  if (!scopeInitialized) {
    if (data.repository && !repositoryEdited) $('repository').value = data.repository;
    $('review-mode').value = data.repository ? data.mode || 'all' : 'selected';
    pickedPRs = new Set(data.selected_prs || []);
    scopeInitialized = true;
  }
  if (!repositoryEdited && !repositoryPreview && data.repository) {
    $('repository').value = data.repository;
    if (!scopeEdited) $('review-mode').value = data.mode || 'selected';
  }
  const counts = data.counts;
  $('watch-status').textContent = data.error || (data.enabled
    ? `Watching ${data.repository} · ${data.mode === 'selected' ? 'selected PRs' : 'all open PRs'} · ${counts.queued || 0} queued · ${counts.running || 0} reviewing · ${counts.completed || 0} completed · ${counts.failed || 0} failed · ${counts.ignored || 0} ignored${data.last_checked ? ' · checked ' + new Date(data.last_checked * 1000).toLocaleTimeString() : ''}`
    : data.repository ? `Paused · ${data.repository}. Active reviews finish; queued work waits until you resume.`
    : 'Connect a repository to begin automatic reviews.');
  updateScope();
  const list = $('pr-inbox');
  list.replaceChildren();
  const filter = $('queue-filter').value || 'all';
  const filtered = data.jobs.filter(job => filter === 'all' || (filter === 'pending' ? ['queued', 'running'].includes(job.status)
    : filter === 'attention' ? ['failed', 'skipped'].includes(job.status) : job.status === filter));
  list.hidden = !filtered.length;
  $('queue-summary').textContent = `${filtered.length} shown · Queue shows up to 50 pending reviews and 50 recent results for ${data.repository || 'the selected repository'}.`
    + (data.ignored_prs?.length ? ` ${data.ignored_prs.length} PRs excluded from future reviews.` : '');
  for (const job of filtered) {
    const pr = job.pull_request;
    const item = el('li', 'pr-item');
    if (selectedReview === job.id) item.classList.add('selected');
    const description = el('div');
    description.append(el('div', 'pr-title', `${pr.repository} #${pr.number} ${pr.title}`));
    description.append(el('div', 'github-note', `${pr.head_sha.slice(0, 7)} · ${job.status}${job.outcome ? ' · ' + job.outcome : ''}`));
    if (job.error) description.append(el('div', 'review-error', job.error));
    const view = el('button', '', job.status === 'running' ? 'Watch debate' : 'View review');
    view.type = 'button';
    view.disabled = !['running', 'completed', 'failed'].includes(job.status);
    view.onclick = () => { $('follow-reviews').checked = false; selectedReview = job.id; showReview(job.id); };
    item.append(description, view);
    if (['failed', 'skipped'].includes(job.status)) {
      const retry = el('button', '', 'Retry');
      retry.type = 'button';
      retry.onclick = async () => {
        retry.disabled = true;
        try { renderWatch(await watchRequest('/reviews/' + job.id + '/retry', {})); }
        catch (error) { $('github-status').textContent = error.message; retry.disabled = false; }
      };
      item.append(retry);
    }
    const ignored = (data.ignored_prs || []).includes(pr.number) || job.status === 'ignored';
    const manage = el('button', '', ignored ? 'Include PR' : 'Ignore PR');
    manage.type = 'button';
    manage.onclick = async () => {
      manage.disabled = true;
      try {
        renderWatch(await watchRequest('/reviews/' + job.id + (ignored ? '/include' : '/ignore'), {}));
        if (repositoryPreview?.repository === pr.repository) {
          if (ignored) pickedPRs.add(pr.number); else pickedPRs.delete(pr.number);
          renderPicker();
        }
        $('github-status').textContent = ignored ? 'PR included. Resume reviews if paused.'
          : 'PR ignored, including future revisions. An active debate can finish. Use Include PR to restore it.';
      } catch (error) { $('github-status').textContent = error.message; manage.disabled = false; }
    };
    item.append(manage);
    list.append(item);
  }
  updateGitHubControls();
  if (typeof lockConnections === 'function') lockConnections();
}

async function showReview(jobId) {
  if (debateBusy) return;
  const generation = ++viewGeneration;
  try {
    const data = await watchRequest('/reviews/' + jobId);
    if (generation !== viewGeneration || debateBusy) return;
    if (displayedReview !== jobId || displayedValidity !== data.valid_result || data.messages.length < displayedCount) {
      reset();
      displayedReview = jobId;
      displayedCount = 0;
      displayedValidity = data.valid_result;
      loadedPR = data.pull_request;
      renderPRSource();
    }
    data.messages.slice(displayedCount).filter(message => message.kind !== 'verdict' || data.valid_result !== false).forEach(handle);
    displayedCount = data.messages.length;
    if (data.valid_result === false && data.status === 'failed') {
      $('map').replaceChildren(el('h2', '', 'Review needs attention'), el('div', 'row', data.error || 'The AI output did not pass validation. Retry this review.'));
      $('map').className = 'escalate';
    }
    $('status').textContent = data.status === 'completed' ? 'Review complete. ' + loadedPR.repository + ' #' + loadedPR.number
      : data.error || `PR #${data.pull_request.number} · ${data.status}`;
  } catch (error) { if (generation === viewGeneration) $('github-status').textContent = error.message; }
}

async function refreshWatch() {
  if (refreshLoading) return;
  refreshLoading = true;
  const generation = refreshGeneration;
  try {
    const data = await watchRequest();
    if (generation !== refreshGeneration) return;
    renderWatch(data);
    if ($('follow-reviews').checked && !debateBusy) {
      const current = data.jobs.find(job => job.status === 'running') || data.jobs.find(job => job.status === 'completed') || data.jobs.find(job => job.status === 'failed');
      if (current) selectedReview = current.id;
    }
    if (selectedReview) await showReview(selectedReview);
  } catch (error) {
    if (generation === refreshGeneration) {
      if ([404, 405].includes(error.status)) {
        queueUnavailable = true;
        $('watch-status').textContent = 'This Council server is running an older version without the review queue. Restart the server from this project, then reload this page.';
        updateGitHubControls();
      } else if (error.status === 403) {
        queueUnavailable = true;
        $('watch-status').textContent = error.message;
        updateGitHubControls();
      } else $('watch-status').textContent = error.message + ' Retrying shortly…';
    }
  } finally {
    refreshLoading = false;
    if (generation === refreshGeneration && !queueUnavailable) refreshTimer = setTimeout(refreshWatch, 3000);
  }
}

async function watchRepository() {
  if (watchLoading || debateBusy || (typeof settingsLoading !== 'undefined' && settingsLoading)) return;
  if (typeof modelHasChanges === 'function') {
    if (modelHasChanges() || githubHasChanges()) {
      $('connection-settings').open = true;
      if (typeof showConnectionTab === 'function') showConnectionTab(modelHasChanges() ? 'model' : 'github');
      $('watch-status').textContent = 'Save your connection changes first, then start automatic reviews.';
      return;
    }
  }
  const repository = $('repository').value.trim();
  const mode = $('review-mode').value || 'selected';
  const selected = repositoryPreview ? [...pickedPRs] : !repositoryEdited && watchState?.repository === repository ? watchState.selected_prs || [] : [];
  if (mode === 'selected' && (!selected.length || (repositoryPreview && repositoryPreview.repository !== repository))) {
    $('watch-status').textContent = 'Load open PRs and select at least one non-draft PR first.';
    return;
  }
  watchLoading = true;
  updateGitHubControls();
  $('watch-status').textContent = 'Checking model and repository access…';
  try {
    const data = await watchRequest('', { repository, mode, selected_prs: mode === 'selected' ? selected : [] });
    repositoryEdited = false;
    renderWatch(data);
    $('github-status').textContent = 'Review scope saved. Use Pause to hold the queue, Ignore PR to exclude a PR, or View review to inspect results.';
  } catch (error) { $('watch-status').textContent = error.message; }
  finally { watchLoading = false; updateGitHubControls(); }
}

async function pauseWatching() {
  watchLoading = true;
  updateGitHubControls();
  try { renderWatch(await watchRequest('/pause', {})); }
  catch (error) { $('watch-status').textContent = error.message; }
  finally { watchLoading = false; updateGitHubControls(); }
}

$('repository-form').onsubmit = event => { event.preventDefault(); watchRepository(); };
$('load-prs').onclick = loadOpenPRs;
$('repository').oninput = () => {
  repositoryEdited = true;
  repositoryPreview = null;
  pickedPRs.clear();
  renderPicker();
  $('pr-selection-status').textContent = 'Repository changed. Load open PRs to choose from this repository.';
  updateScope();
};
$('review-mode').onchange = () => { scopeEdited = true; updateScope(); };
$('queue-filter').onchange = () => { if (watchState) renderWatch(watchState); };
$('stop-watching').onclick = pauseWatching;
$('run').addEventListener('click', () => { viewGeneration++; selectedReview = displayedReview = null; $('follow-reviews').checked = false; loadedPR = null; renderPRSource(); });
window.addEventListener('pagehide', () => { refreshGeneration++; clearTimeout(refreshTimer); });
window.addEventListener('pageshow', event => { if (event.persisted) refreshWatch(); });
refreshWatch();
