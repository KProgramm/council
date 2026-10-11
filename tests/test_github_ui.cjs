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
let state = { repository: '', enabled: false, active: false, counts: {}, jobs: [], error: '', last_checked: null };
const pr = { repository: 'o/r', number: 1, title: '<script>not executed</script>', author: 'author', url: 'https://github.com/o/r/pull/1', head_sha: 'a'.repeat(40) };
const jobs = { one: { id: 'one', pull_request: pr, status: 'running', messages: [{ kind: 'diff', text: 'first diff' }], error: '' },
  two: { id: 'two', pull_request: { ...pr, number: 2 }, status: 'completed', messages: [{ kind: 'diff', text: 'second diff' }, { kind: 'verdict', text: '{}' }], error: '' } };
const calls = [], messages = [];
const context = vm.createContext({ $, el, AbortController, Date, HTTP_BASE: '', debateBusy: false,
  document: { activeElement: null }, window: { addEventListener() {} },
  setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, delay }); return id; }, clearTimeout(id) { timers.delete(id); },
  reset() { messages.length = 0; }, handle(message) { messages.push(message); },
  async fetch(path, options) {
    calls.push({ path, body: options.body ? JSON.parse(options.body) : null });
    if (path === '/watch' && options.method === 'POST') state = { ...state, repository: 'o/r', enabled: true, counts: { running: 1, completed: 1 }, jobs: Object.values(jobs) };
    if (path === '/watch/pause') state = { ...state, enabled: false };
    const data = path.startsWith('/watch/reviews/') ? jobs[path.split('/').at(-1)] : state;
    return { ok: true, json: async () => data };
  },
});
vm.runInContext(fs.readFileSync('web/github.js', 'utf8'), context);
(async () => {
  await new Promise(resolve => setImmediate(resolve));
  $('repository').value = 'o/r';
  await context.watchRepository();
  assert(calls.some(call => call.path === '/watch' && call.body?.repository === 'o/r'));
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
  buttons[1].onclick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal($('follow-reviews').checked, false);
  assert.equal(messages[0].text, 'second diff', 'Completed results can be inspected without starting a review');
  await context.refreshWatch();
  assert.equal(messages[0].text, 'second diff', 'Background work preserves the selected result');
  assert(!calls.some(call => call.path === '/reviews' || call.path.startsWith('/github/')), 'The browser never starts diff reviews itself');
  context.savedConnections = { provider: 'brev', providers: { brev: { model: 'model', base_url: 'https://model.example.com' } } };
  const before = calls.length;
  await context.watchRepository();
  assert.equal(calls.length, before, 'Unsaved connection changes must not start reviews with old credentials');
  assert($('watch-status').textContent.includes('Save your connection changes'));
  await context.pauseWatching();
  assert.equal($('watch-repository').textContent, 'Resume reviews');
  assert.equal($('stop-watching').disabled, true);
  console.log('PASS: server-owned queue, incremental live view, result selection, and pause.');
})().catch(error => { console.error(error); process.exitCode = 1; });
