/* The server owns watching, deduplication, execution, and durable review results. */
let loadedPR = null;
let watchState = null;
let watchLoading = false;
let selectedReview = null;
let displayedReview = null;
let displayedCount = 0;
let refreshTimer = null;
let refreshGeneration = 0;
let refreshLoading = false;
let viewGeneration = 0;

function updateGitHubControls() {
  const automatic = !!watchState?.enabled || !!watchState?.active;
  $('run').disabled = debateBusy || automatic;
  $('replay').disabled = debateBusy;
  $('watch-repository').disabled = watchLoading || debateBusy;
  $('repository').disabled = watchLoading;
  $('stop-watching').disabled = watchLoading || !watchState?.enabled;
}

async function watchRequest(path = '', payload) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 65000);
  try {
    const response = await fetch(HTTP_BASE + '/watch' + path, {
      method: payload ? 'POST' : 'GET', cache: 'no-store', signal: controller.signal,
      headers: payload ? { 'Content-Type': 'application/json' } : {},
      ...(payload ? { body: JSON.stringify(payload) } : {}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not update the review queue.');
    return data;
  } finally { clearTimeout(timeout); }
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
  if (data.repository && document.activeElement !== $('repository')) $('repository').value = data.repository;
  const counts = data.counts;
  $('watch-status').textContent = data.error || (data.enabled
    ? `Watching ${data.repository} · ${counts.queued || 0} queued · ${counts.running || 0} reviewing · ${counts.completed || 0} completed${data.last_checked ? ' · checked ' + new Date(data.last_checked * 1000).toLocaleTimeString() : ''}`
    : data.repository ? `Paused · ${data.repository}. Active reviews finish; queued work waits until you resume.`
    : 'Connect a repository to begin automatic reviews.');
  $('watch-repository').textContent = data.repository && !data.enabled ? 'Resume reviews' : 'Watch and review';
  const list = $('pr-inbox');
  list.replaceChildren();
  list.hidden = !data.jobs.length;
  for (const job of data.jobs) {
    const pr = job.pull_request;
    const item = el('li', 'pr-item');
    if (selectedReview === job.id) item.classList.add('selected');
    const description = el('div');
    description.append(el('div', 'pr-title', `${pr.repository} #${pr.number} ${pr.title}`));
    description.append(el('div', 'github-note', `${pr.head_sha.slice(0, 7)} · ${job.status}${job.outcome ? ' · ' + job.outcome : ''}`));
    if (job.error) description.append(el('div', 'review-error', job.error));
    const view = el('button', '', job.status === 'running' ? 'Watch debate' : 'View review');
    view.type = 'button';
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
    list.append(item);
  }
  updateGitHubControls();
}

async function showReview(jobId) {
  if (debateBusy) return;
  const generation = ++viewGeneration;
  try {
    const data = await watchRequest('/reviews/' + jobId);
    if (generation !== viewGeneration || debateBusy) return;
    if (displayedReview !== jobId || data.messages.length < displayedCount) {
      reset();
      displayedReview = jobId;
      displayedCount = 0;
      loadedPR = data.pull_request;
      renderPRSource();
    }
    data.messages.slice(displayedCount).forEach(handle);
    displayedCount = data.messages.length;
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
      const current = data.jobs.find(job => job.status === 'running') || data.jobs.find(job => job.status === 'completed');
      if (current) selectedReview = current.id;
    }
    if (selectedReview) await showReview(selectedReview);
  } catch (error) {
    if (generation === refreshGeneration) $('watch-status').textContent = error.message + ' Reconnecting…';
  } finally {
    refreshLoading = false;
    if (generation === refreshGeneration) refreshTimer = setTimeout(refreshWatch, 3000);
  }
}

async function watchRepository() {
  if (typeof savedConnections !== 'undefined' && savedConnections) {
    const provider = $('llm-provider').value;
    const saved = savedConnections.providers[provider] || {};
    if (provider !== savedConnections.provider || $('llm-model').value.trim() !== saved.model
        || (provider !== 'paid' && $('llm-base-url').value.trim() !== saved.base_url)
        || $('llm-api-key').value || $('clear-llm-api-key').checked
        || $('github-token').value || $('clear-github-token').checked) {
      $('connection-settings').open = true;
      $('watch-status').textContent = 'Save your connection changes first, then start automatic reviews.';
      return;
    }
  }
  watchLoading = true;
  updateGitHubControls();
  $('watch-status').textContent = 'Checking model and repository access…';
  try {
    renderWatch(await watchRequest('', { repository: $('repository').value.trim() }));
    $('github-status').textContent = 'Reviews start automatically. You can inspect any result while the queue continues.';
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
$('stop-watching').onclick = pauseWatching;
$('replay').addEventListener('click', () => { viewGeneration++; selectedReview = displayedReview = null; $('follow-reviews').checked = false; loadedPR = null; renderPRSource(); });
$('run').addEventListener('click', () => { viewGeneration++; selectedReview = displayedReview = null; $('follow-reviews').checked = false; loadedPR = null; renderPRSource(); });
window.addEventListener('pagehide', () => { refreshGeneration++; clearTimeout(refreshTimer); });
window.addEventListener('pageshow', event => { if (event.persisted) refreshWatch(); });
refreshWatch();
