/* Verify server-owned queue rendering, live following, result selection, and pause. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag = 'div') { this.tag = tag; this.value = ''; this.checked = true; this.children = []; this.listeners = {}; this.classList = { add() {} }; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  querySelectorAll(tag) { return this.children.flatMap(child => [...(child.tag === tag ? [child] : []), ...child.querySelectorAll(tag)]); }
}
const ids = new Map();
const $ = id => { if (!ids.has(id)) ids.set(id, new Element()); return ids.get(id); };
const el = (tag, cls, text) => { const element = new Element(tag); element.textContent = text; return element; };
const timers = new Map(); let timerId = 0;
let state = { repository: '', enabled: false, active: false, mode: 'selected', selected_prs: [], counts: {}, jobs: [], error: '', last_checked: null };
const pr = { repository: 'o/r', number: 1, title: '<script>not executed</script>', author: 'author', url: 'https://github.com/o/r/pull/1', head_sha: 'a'.repeat(40) };
const jobs = { one: { id: 'one', pull_request: pr, status: 'running', messages: [{ kind: 'diff', text: 'first diff' }], error: '' },
  two: { id: 'two', pull_request: { ...pr, number: 2 }, status: 'completed', messages: [{ kind: 'diff', text: 'second diff' }, { kind: 'verdict', text: '{}' }], error: '' } };
const calls = [], messages = [];
const context = vm.createContext({ $, el, AbortController, Date, HTTP_BASE: '', debateBusy: false,
  Set, modelHasChanges: () => false, githubHasChanges: () => false,
  document: { activeElement: null }, window: { addEventListener() {} },
  setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, delay }); return id; }, clearTimeout(id) { timers.delete(id); },
  reset() { messages.length = 0; }, handle(message) { messages.push(message); },
  async fetch(path, options) {
    calls.push({ path, body: options.body ? JSON.parse(options.body) : null });
    if (path === '/github/repository') return { ok: true, json: async () => ({ repository: 'o/r', pull_requests: [pr, { ...pr, number: 2, draft: true }] }) };
    if (path === '/watch' && options.method === 'POST') state = { ...state, repository: 'o/r', mode: 'selected', selected_prs: [1], enabled: true, counts: { running: 1, completed: 1 }, jobs: Object.values(jobs) };
    if (path === '/watch/pause') state = { ...state, enabled: false };
    const data = path.startsWith('/watch/reviews/') ? jobs[path.split('/').at(-1)] : state;
    return { ok: true, json: async () => data };
  },
});
vm.runInContext(fs.readFileSync('web/connections.js', 'utf8'), context);
vm.runInContext(fs.readFileSync('web/github.js', 'utf8'), context);
(async () => {
  await new Promise(resolve => setImmediate(resolve));
  $('repository').value = 'o/r';
  await context.watchRepository();
  assert(!calls.some(call => call.path === '/watch' && call.body), 'No reviews start until the user chooses PRs');
  await context.loadOpenPRs();
  const choices = $('pr-picker').querySelectorAll('input');
  assert.equal(choices[1].disabled, true, 'Drafts cannot be selected');
  assert.equal(choices[0].checked, false, 'Loading PRs does not implicitly select every PR');
  choices[0].checked = true;
  choices[0].onchange();
  await context.watchRepository();
  assert(calls.some(call => call.path === '/watch' && call.body?.repository === 'o/r'));
  assert.deepEqual(calls.find(call => call.path === '/watch' && call.body).body.selected_prs, [1]);
  assert.equal($('run').disabled, true, 'Automatic reviews own the review queue');
  await context.refreshWatch();
  assert.equal(messages.length, 1);
  assert.equal(messages[0].text, 'first diff', 'Follow mode displays the running review');
  jobs.one.messages.push({ kind: 'assessment', text: 'new assessment' });
  await context.refreshWatch();
  assert.equal(messages.length, 2, 'Refresh only appends new messages');
  await context.refreshWatch();
  assert.equal(messages.length, 2, 'Previously displayed messages are not duplicated');
  const buttons = $('pr-inbox').querySelectorAll('button');
  buttons.find(button => button.textContent === 'View review').onclick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal($('follow-reviews').checked, false);
  assert.equal(messages[0].text, 'second diff', 'Completed results can be inspected without starting a review');
  await context.refreshWatch();
  assert.equal(messages[0].text, 'second diff', 'Background work preserves the selected result');
  jobs.two.status = 'failed';
  jobs.two.valid_result = false;
  jobs.two.error = 'Invalid reviewer output.';
  await context.showReview('two');
  assert.equal(messages.length, 1, 'An invalid stored verdict must not be shown as a successful result');
  assert.equal($('map').children[0].textContent, 'Review needs attention');
  assert(!calls.some(call => call.path === '/reviews' || call.path === '/github/pull-request'), 'The browser never starts diff reviews itself');
  context.modelHasChanges = () => true;
  const before = calls.length;
  await context.watchRepository();
  assert.equal(calls.length, before, 'Unsaved connection changes must not start reviews with old credentials');
  assert($('watch-status').textContent.includes('Save your connection changes'));
  await context.pauseWatching();
  assert.equal($('watch-repository').textContent, 'Start selected reviews');
  assert.equal($('stop-watching').disabled, true);
  $('repository').value = 'another/repo';
  $('repository').oninput();
  await context.refreshWatch();
  assert.equal($('repository').value, 'another/repo', 'Polling must not overwrite a repository the user is entering');
  const pollingBefore = [...timers.values()].filter(timer => timer.delay === 3000).length;
  context.fetch = async () => ({ ok: false, status: 404, json: async () => ({ detail: 'Not Found' }) });
  await context.refreshWatch();
  assert($('watch-status').textContent.includes('older version'));
  assert(!$('watch-status').textContent.includes('Reconnecting'));
  assert.equal([...timers.values()].filter(timer => timer.delay === 3000).length, pollingBefore, 'Missing queue endpoints do not endlessly retry');
  console.log('PASS: server-owned queue, incremental live view, result selection, and pause.');
})().catch(error => { console.error(error); process.exitCode = 1; });
