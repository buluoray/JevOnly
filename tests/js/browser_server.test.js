'use strict';

const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const path = require('node:path');
const readline = require('node:readline');
const test = require('node:test');

const { createRequestTracker, settle } = require('../../src/jevonly/envs/browser/js/actions');
const { describeCandidate, groupTextUnits } = require('../../src/jevonly/envs/browser/js/snapshot');
const { semanticSignature } = require('../../src/jevonly/envs/browser/js/signature');

function request(type) {
  return { resourceType: () => type };
}

test('groupTextUnits keeps one complete unit per short block', () => {
  const entries = [
    { text: '6:20 PM', block: 'flight-1', blockText: '6:20 PM Air Example $1,634' },
    { text: 'Air Example', block: 'flight-1', blockText: '6:20 PM Air Example $1,634' },
    { text: 'Long article fragment', block: null },
    { text: ' x ', block: null },
  ];
  assert.deepEqual(groupTextUnits(entries), ['6:20 PM Air Example $1,634', 'Long article fragment']);
});

test('describeCandidate normalizes text while preserving typed fields', () => {
  assert.deepEqual(
    describeCandidate({
      role: ' button ',
      name: '  Book   now ',
      ctx: ' Fare   row ',
      tag: 'button',
      fam: 'button||primary',
      checked: false,
      expanded: true,
      options: ['One', 'Two'],
      hint: '  Opens   checkout ',
    }),
    {
      role: 'button',
      name: 'Book now',
      ctx: 'Fare row',
      tag: 'button',
      fam: 'button||primary',
      hint: '  Opens   checkout ',
      options: ['One', 'Two'],
      checked: false,
      expanded: true,
    },
  );
});

test('request tracker counts only requests triggered by the action', () => {
  let now = 1_000;
  const tracker = createRequestTracker(() => now);
  const oldFetch = request('fetch');
  const newFetch = request('fetch');
  const eventStream = request('eventsource');
  tracker.start(oldFetch, 800);
  tracker.start(newFetch, 990);
  tracker.start(eventStream, 995);
  assert.equal(tracker.pendingSince(1_000), 1);
  tracker.finish(newFetch);
  assert.equal(tracker.pendingSince(1_000), 0);
});

test('settle observes the floor and stops when action requests finish', async () => {
  let now = 100;
  const tracker = createRequestTracker(() => now);
  const fetchRequest = request('fetch');
  tracker.start(fetchRequest, 100);
  const page = {
    async waitForTimeout(milliseconds) {
      now += milliseconds;
      if (now >= 300) tracker.finish(fetchRequest);
    },
  };
  assert.deepEqual(await settle(page, tracker, 100, 100, 500, () => now), {
    settled_ms: 200,
    settled: true,
  });
});

test('settle reports a capped request without waiting beyond the cap', async () => {
  let now = 100;
  const tracker = createRequestTracker(() => now);
  tracker.start(request('xhr'), 100);
  const page = {
    async waitForTimeout(milliseconds) {
      now += milliseconds;
    },
  };
  assert.deepEqual(await settle(page, tracker, 100, 100, 150, () => now), {
    settled_ms: 150,
    settled: false,
  });
});

test('semanticSignature is self-contained page code: no closures, serialisable by source', () => {
  const src = semanticSignature.toString();
  assert.match(src, /^function semanticSignature\(element\)/);
  // whatever it references must be defined inside it or be a browser global
  assert.ok(!/require\(|module\.|exports/.test(src));
  assert.equal(new Function(`return ${src}`)().name, 'semanticSignature');
});

test('snapshotSettled looks again after a navigation race, and gives up on other errors', async () => {
  const { snapshotSettled } = require('../../src/jevonly/envs/browser/js/protocol');
  const calls = [];
  const racing = {
    waitForLoadState: async () => calls.push('load'),
    waitForTimeout: async () => calls.push('wait'),
    title: async () => {
      calls.push('title');
      if (calls.filter((c) => c === 'title').length === 1) {
        throw new Error('page.title: Execution context was destroyed, most likely because of a navigation');
      }
      return 'Main Page';
    },
    evaluate: async () => ({
      candidates: [],
      headings: [],
      visible_text: '',
      text_units: [],
      modal: null,
      truncated_runs: [],
    }),
    url: () => 'https://en.wikipedia.org/wiki/Main_Page',
  };
  // settleTransitions uses page.evaluate too; the fake answers it with the same object (no animations counted)
  const out = await snapshotSettled(racing, { transition_cap_ms: 0 });
  assert.equal(out.title, 'Main Page');
  assert.deepEqual(calls.filter((c) => c !== 'title').slice(0, 2), ['load', 'wait']);
  const broken = {
    ...racing,
    title: async () => {
      throw new Error('page.title: something else');
    },
  };
  await assert.rejects(() => snapshotSettled(broken, { transition_cap_ms: 0 }), /something else/);
});

const e2e = process.env.JEVONLY_E2E === '1' ? test : test.skip;

e2e('browser child handles snapshot, keyboard, and click through JSON lines', async (t) => {
  const childPath = path.resolve(__dirname, '../../src/jevonly/envs/browser/js/browser_server.js');
  const child = spawn(process.execPath, [childPath], {
    env: process.env,
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  const lines = readline.createInterface({ input: child.stdout });
  const iterator = lines[Symbol.asyncIterator]();
  let stderr = '';
  child.stderr.on('data', (chunk) => {
    stderr += chunk;
  });
  t.after(() => {
    if (!child.killed) child.kill();
  });

  async function command(value) {
    child.stdin.write(`${JSON.stringify(value)}\n`);
    const next = await iterator.next();
    assert.equal(next.done, false, stderr);
    return JSON.parse(next.value);
  }

  const markup = [
    '<!doctype html><title>JevOnly protocol test</title>',
    '<label>Name <input aria-label="Name"></label>',
    "<button onclick=\"document.getElementById('status').textContent='clicked'\">Vote</button>",
    '<p id="status">ready</p>',
    '<button id="drift">One way</button>',
    '<table><tr><th>Spec</th><th>Anker Nano 30W</th><th>Anker 65W</th></tr>',
    '<tr><td>Price</td><td>$18.24</td><td>$39.99</td></tr></table>',
  ].join('');
  assert.equal((await command({ cmd: 'goto', url: `data:text/html,${encodeURIComponent(markup)}` })).ok, true);

  const first = await command({ cmd: 'snapshot', viewport_only: true });
  assert.equal(first.ok, true);
  // a table cell is its own unit, labelled with its row and column headers
  assert.ok(
    first.snapshot.text_units.includes('Price · Anker Nano 30W: $18.24'),
    JSON.stringify(first.snapshot.text_units),
  );
  assert.ok(!first.snapshot.text_units.some((u) => u.includes('$18.24 $39.99')), 'the row is not glued into one unit');
  const inputIndex = first.snapshot.candidates.findIndex((candidate) => candidate.name === 'Name');
  const buttonIndex = first.snapshot.candidates.findIndex((candidate) => candidate.name === 'Vote');
  assert.notEqual(inputIndex, -1);
  assert.notEqual(buttonIndex, -1);

  const typed = await command({ cmd: 'keyboard', focus: true, index: inputIndex, text: 'JevOnly', settle_ms: 1 });
  assert.equal(typed.typed, 'JevOnly');

  // Drift refusal: the control the ballot described is still the same node but no longer says the same
  // thing -> the action is refused unsent. The same node, unchanged, is clicked as usual.
  const driftIndex = first.snapshot.candidates.findIndex((candidate) => candidate.name === 'One way');
  assert.notEqual(driftIndex, -1);
  const driftSig = first.snapshot.candidates[driftIndex].sig;
  assert.ok(driftSig && driftSig.startsWith('button|'), driftSig);
  assert.equal(
    (await command({ cmd: 'act', index: buttonIndex, action: 'click', expect_sig: driftSig, act_settle_ms: 1 })).ok,
    false,
    'a signature that belongs to another control is refused',
  );
  const refused = await command({
    cmd: 'act',
    index: driftIndex,
    action: 'click',
    expect_sig: driftSig.replace('One way', 'Round trip'),
    act_settle_ms: 1,
  });
  assert.equal(refused.ok, false);
  assert.match(refused.error, /^stale candidate: target changed since observation \(was "Round trip", now "One way"\)/);
  assert.equal(
    (await command({ cmd: 'act', index: driftIndex, action: 'click', expect_sig: driftSig, act_settle_ms: 1 })).ok,
    true,
    'the unchanged control passes its own signature',
  );
  // the real Vote click, with its own signature
  const voteSig = first.snapshot.candidates[buttonIndex].sig;
  assert.equal(
    (await command({ cmd: 'act', index: buttonIndex, action: 'click', expect_sig: voteSig, act_settle_ms: 1 })).ok,
    true,
  );

  const second = await command({ cmd: 'snapshot', viewport_only: true });
  assert.match(second.snapshot.visible_text, /clicked/);
  assert.equal((await command({ cmd: 'quit' })).ok, true);
});
