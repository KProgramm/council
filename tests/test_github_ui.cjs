/* Exercise async watching, edited-input protection, and cancellation without network calls. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor(tag = 'div') {
    this.tag = tag;
    this.value = '';
    this.checked = true;
    this.children = [];
    this.listeners = {};
    this.classList = { add() {} };
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  querySelectorAll(tag) {
    return this.children.flatMap(child => [
      ...(child.tag === tag ? [child] : []), ...child.querySelectorAll(tag),
    ]);
  }
}

const ids = new Map();
const $ = id => {
  if (!ids.has(id)) ids.set(id, new Element());
  return ids.get(id);
};
const el = (tag, cls, text) => { const result = new Element(tag); result.textContent = text; return result; };
const timers = new Map();
let timerId = 0;
let pulls = [];
let pendingRepository = null;
const calls = [];
const context = vm.createContext({
  $, el, Set, Date, AbortController, console, HTTP_BASE: '', debateBusy: false, messageCount: 0,
  window: { addEventListener() {} },
  setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, delay }); return id; },
  clearTimeout(id) { timers.delete(id); },
  reset() { context.messageCount = 0; },
  renderDiff(diff) { $('diff').textContent = diff; },
  async fetch(path, options) {
    const body = JSON.parse(options.body);
    calls.push({ path, body });
    if (path.endsWith('/repository')) {
      if (pendingRepository) return pendingRepository;
      return { ok: true, json: async () => ({ repository: 'owner/repo', pull_requests: pulls, limit: 30 }) };
    }
    const pr = pulls.find(pr => pr.url === body.url);
    assert(pr, 'Only discovered PRs should be fetched');
    return { ok: true, json: async () => ({ changed: true, diff: `diff for ${pr.number}`, pull_request: {
      ...pr, repository: 'owner/repo', state: 'open', head_ref: 'feature', base_ref: 'main',
      changed_files: 1, additions: 1, deletions: 1,
    } }) };
  },
});
vm.runInContext(fs.readFileSync('web/github.js', 'utf8'), context);
const pr = (number, createdAt) => ({ number, title: `PR ${number}`, author: 'agent',
  url: `https://github.com/owner/repo/pull/${number}`, head_sha: 'a'.repeat(40),
  revision: `base:head-${number}`, created_at: createdAt });
async function poll() {
  const entry = [...timers].find(([, timer]) => timer.delay === 120000);
  assert(entry, 'The watch should schedule another poll');
  timers.delete(entry[0]);
  await entry[1].fn();
}

(async () => {
  $('repository').value = 'owner/repo';
  pulls = [pr(1, '2020-01-01T00:00:00Z')];
  await context.watchRepository();
  assert.equal($('input').value, 'diff for 1', 'Connecting should import the newest PR');

  pulls = [pr(2, new Date(Date.now() + 60000).toISOString()), ...pulls];
  await poll();
  assert.equal($('input').value, 'diff for 2', 'A new PR should automatically replace the idle preview');
  assert.equal($('pr-inbox').querySelectorAll('button').length, 2);

  $('input').value = 'my local edits';
  $('input').listeners.input();
  pulls = [pr(3, new Date(Date.now() + 60000).toISOString()), ...pulls];
  await poll();
  assert.equal($('input').value, 'my local edits', 'Polling must preserve local edits');

  $('input').value = 'diff for 2';
  $('input').listeners.input();
  context.debateBusy = true;
  context.messageCount = 3;
  pulls = [pr(4, new Date(Date.now() + 60000).toISOString()), ...pulls];
  await poll();
  assert.equal($('input').value, 'diff for 2', 'Polling must preserve the diff associated with a debate');
  assert.equal(calls.filter(c => c.path.endsWith('/pull-request')).length, 2);

  context.debateBusy = false;
  await context.watchRepository();
  assert.equal($('input').value, 'diff for 2', 'Connecting must preserve a completed debate');
  context.messageCount = 0;
  $('input').value = 'another manual diff';
  $('input').listeners.input();
  await context.watchRepository();
  assert.equal($('input').value, 'another manual diff', 'Connecting must preserve manually edited input');
  $('input').value = 'diff for 2';
  $('input').listeners.input();
  let resolveRepository;
  pendingRepository = new Promise(resolve => { resolveRepository = resolve; });
  const late = context.watchRepository();
  context.stopWatching();
  resolveRepository({ ok: true, json: async () => ({ repository: 'owner/repo', pull_requests: pulls, limit: 30 }) });
  await late;
  assert.equal($('pr-inbox').children.length, 0, 'A disconnected watch must ignore its late response');
  assert.equal([...timers.values()].filter(t => t.delay === 120000).length, 0);
  assert.equal($('input').value, 'diff for 2');
  console.log('PASS: connect, new-PR diff import, edited/debating diff preservation, and disconnect cancellation.');
})().catch(error => { console.error(error); process.exitCode = 1; });
