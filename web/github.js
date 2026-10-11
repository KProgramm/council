/* Repository watching imports diffs; Run council remains the explicit review action. */
const WATCH_INTERVAL_MS = 120000;
let githubLoading = false;
let loadedPR = null;
let loadedGitHubDiff = "";
let diffEdited = false;
let watchingRepository = null;
let watchGeneration = 0;
let watchTimer = null;
let watchController = null;
let watchLoading = false;
let watchStartedAt = 0;
let seenPRs = new Set();
let inboxPRs = [];
let newPRNumbers = new Set();

function updateGitHubControls() {
  const busy = debateBusy || githubLoading;
  $("run").disabled = busy;
  $("replay").disabled = busy;
  $("input").disabled = githubLoading;
  $("load-pr").disabled = busy;
  $("pr-url").disabled = busy;
  $("watch-repository").disabled = busy || watchLoading;
  $("repository").disabled = busy || watchLoading;
  $("stop-watching").disabled = !watchingRepository;
  $("pr-inbox").querySelectorAll("button").forEach(button => { button.disabled = busy; });
}

async function githubRequest(path, payload, signal) {
  const response = await fetch(HTTP_BASE + "/github/" + path, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload), signal,
  });
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(typeof data.detail === "string" ? data.detail : "Check the GitHub URL and try again.");
    error.status = response.status;
    throw error;
  }
  return data;
}

function renderPRSource() {
  const source = $("pr-source");
  source.replaceChildren();
  source.hidden = !loadedPR;
  if (!loadedPR) return;
  const link = el("a", "", `${loadedPR.repository} #${loadedPR.number}: ${loadedPR.title}`);
  link.href = loadedPR.url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  source.append(link, el("p", "github-note",
    `${loadedPR.state} · ${loadedPR.author} · ${loadedPR.head_ref} → ${loadedPR.base_ref} · ${loadedPR.head_sha.slice(0, 7)} · ${loadedPR.changed_files} files · +${loadedPR.additions} / −${loadedPR.deletions}`));
}

function renderPRInbox() {
  const list = $("pr-inbox");
  list.replaceChildren();
  list.hidden = inboxPRs.length === 0;
  for (const pr of inboxPRs) {
    const item = el("li", "pr-item");
    if (loadedPR && pr.url === loadedPR.url) item.classList.add("selected");
    const description = el("div");
    description.append(el("div", "pr-title", `#${pr.number} ${pr.title}`));
    const labels = [pr.author];
    if (pr.draft) labels.push("Draft");
    if (newPRNumbers.has(pr.number)) labels.push("New since watching");
    if (loadedPR && pr.url === loadedPR.url) labels.push("Loaded");
    description.append(el("div", "github-note", labels.join(" · ")));
    const load = el("button", "", "Load diff");
    load.type = "button";
    load.setAttribute("aria-label", `Load diff for PR #${pr.number}`);
    load.onclick = () => loadGitHubPR(pr.url);
    item.append(description, load);
    list.append(item);
  }
  updateGitHubControls();
}

async function loadGitHubPR(url, { automatic = false, generation = null, knownRevision = null } = {}) {
  // Preserve an edited diff or an existing debate when background polling finds new work.
  if (githubLoading || debateBusy || (automatic && (diffEdited || messageCount > 0))) return false;
  githubLoading = true;
  updateGitHubControls();
  $("github-status").textContent = "Fetching the PR diff from GitHub…";
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 65000);
  try {
    const data = await githubRequest("pull-request", { url, known_revision: knownRevision }, controller.signal);
    if (generation !== null && generation !== watchGeneration) return false;
    loadedPR = data.pull_request;
    if (data.changed) {
      reset();
      loadedGitHubDiff = data.diff;
      diffEdited = false;
      $("input").value = data.diff;
      renderDiff(data.diff);
      $("status").textContent = "GitHub diff loaded. Ready to run the council.";
    }
    $("pr-url").value = loadedPR.url;
    renderPRSource();
    renderPRInbox();
    $("github-status").textContent = data.changed
      ? "Diff loaded. Click Run council to review it."
      : "The loaded PR is up to date.";
    return true;
  } catch (error) {
    if (generation !== null && generation !== watchGeneration) return false;
    $("github-status").textContent = error.name === "AbortError"
      ? "Loading the diff timed out. Try again."
      : error.message;
    return false;
  } finally {
    clearTimeout(timeout);
    githubLoading = false;
    updateGitHubControls();
  }
}

function stopWatching(message = "Repository watch stopped. Your loaded diff is still available.") {
  watchGeneration++;
  clearTimeout(watchTimer);
  watchController?.abort();
  watchController = null;
  watchingRepository = null;
  watchLoading = false;
  $("watch-status").textContent = message;
  updateGitHubControls();
}

async function pollRepository(generation, initial = false) {
  if (generation !== watchGeneration || !watchingRepository) return;
  watchLoading = true;
  watchController = new AbortController();
  const controller = watchController;
  const timeout = setTimeout(() => controller.abort(), 25000);
  updateGitHubControls();
  try {
    const data = await githubRequest("repository", { repository: watchingRepository }, controller.signal);
    if (generation !== watchGeneration) return;
    watchingRepository = data.repository;
    inboxPRs = data.pull_requests;
    const arrivals = inboxPRs.filter(pr => !seenPRs.has(pr.number) && Date.parse(pr.created_at) >= watchStartedAt);
    if (!initial) arrivals.forEach(pr => newPRNumbers.add(pr.number));
    inboxPRs.forEach(pr => seenPRs.add(pr.number));
    renderPRInbox();
    $("watch-status").textContent = `Watching ${data.repository} · ${inboxPRs.length} open PRs${inboxPRs.length === data.limit ? " (newest 30)" : ""} · checked ${new Date().toLocaleTimeString()} · next check in two minutes.`;
    if ($("auto-load-pr").checked) {
      const candidate = initial ? inboxPRs[0] : arrivals[0];
      if (candidate) {
        const loaded = await loadGitHubPR(candidate.url, { automatic: true, generation });
        if (!loaded && (debateBusy || diffEdited || messageCount > 0)) {
          $("github-status").textContent = "A new PR is in the inbox. Select Load diff when you are ready.";
        }
      } else if (loadedPR && !diffEdited && !debateBusy && messageCount === 0) {
        const current = inboxPRs.find(pr => pr.url === loadedPR.url);
        if (current && current.revision !== loadedPR.revision) {
          await loadGitHubPR(current.url, { automatic: true, generation, knownRevision: loadedPR.revision });
        }
      }
    }
  } catch (error) {
    if (generation !== watchGeneration) return;
    if ([401, 403, 404, 422, 429].includes(error.status)) {
      stopWatching(error.message + " Watching paused.");
      return;
    }
    $("watch-status").textContent = (error.name === "AbortError" ? "GitHub check timed out." : error.message) + " Will retry in two minutes.";
  } finally {
    clearTimeout(timeout);
    if (generation === watchGeneration && watchingRepository) {
      watchLoading = false;
      watchController = null;
      updateGitHubControls();
      watchTimer = setTimeout(() => pollRepository(generation), WATCH_INTERVAL_MS);
    }
  }
}

function watchRepository() {
  stopWatching();
  watchingRepository = $("repository").value.trim();
  watchStartedAt = Date.now();
  seenPRs = new Set();
  newPRNumbers = new Set();
  inboxPRs = [];
  renderPRInbox();
  $("watch-status").textContent = "Checking GitHub for open pull requests…";
  return pollRepository(watchGeneration, true);
}

$("repository-form").onsubmit = event => { event.preventDefault(); watchRepository(); };
$("pull-request-form").onsubmit = event => { event.preventDefault(); loadGitHubPR($("pr-url").value.trim()); };
$("stop-watching").onclick = () => stopWatching();
$("input").addEventListener("input", () => {
  diffEdited = $("input").value !== loadedGitHubDiff;
  if (diffEdited) $("github-status").textContent = "Diff edited locally. Automatic loading is paused to preserve your edits.";
});
window.addEventListener("pagehide", () => stopWatching());
updateGitHubControls();
