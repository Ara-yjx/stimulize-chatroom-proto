const assert = require('node:assert/strict');
const test = require('node:test');
const Module = require('node:module');
const path = require('node:path');
const { buildSync } = require('esbuild');

const compiled = buildSync({ entryPoints: [path.join(__dirname, '../src/data/state.ts')],
  bundle: true, platform: 'node', format: 'cjs', write: false }).outputFiles[0].text;
const loaded = new Module(__filename);
loaded.paths = module.paths;
loaded._compile(compiled, __filename);
const { ChatroomState } = loaded.exports;

const error = {type: 'system', subtype: 'inference_error', role: 'system',
  event_id: 'e1', timestamp: 1, sender: 'System', content: 'Chatroom server error: missing_tool_call'};

test('ordinary participant sees no technical error or diagnostic, including history/ED data', () => {
  const state = new ChatroomState();
  const shown = [];
  state.onError(v => shown.push(v));
  state.onSystemEvent(v => shown.push(v));
  state.onDiagnostic((_key, v) => shown.push(v));
  state._renderEvent(error);
  state._emitDiagnostic({event_id:'d1',timestamp:1,code:'missing_tool_call',ai_name:'Alice'});
  assert.equal(state._eventToMessage(error), null);
  assert.deepEqual(shown, []);
  assert.deepEqual(state.getCurrentEpisodeHistory(), []);
  assert.equal(state.getCurrentEpisodeHistoryText(), '');
});

test('preview coalesces distinct errors, dedupes replay, never adds them to research history', () => {
  const state = new ChatroomState();
  state._initOptions = {debug: true};
  const shown = [];
  state.onDiagnostic((key, text) => shown.push({key,text}));
  const diagnostic = {event_id:'d1', timestamp:1, code:'missing_tool_call', ai_name:'Alice', ai_participant_id:'a'};
  state._emitDiagnostic(diagnostic);
  state._emitDiagnostic(diagnostic);
  state._emitDiagnostic({...diagnostic,event_id:'d2',timestamp:2});
  assert.equal(shown.length, 2);
  assert.equal(shown[0].key, shown[1].key);
  assert.match(shown[1].text, /2 occurrences/);
  assert.deepEqual(state.getHistory(), []);
  assert.equal(state.getHistoryText(), '');
});

test('legacy errors are hidden from backward history and preview errors are sanitized', () => {
  const state = new ChatroomState();
  state._initOptions = {debug: true};
  const shown = [];
  state.onDiagnostic((_key, text) => shown.push(text));
  state._renderEvent({...error,content:'Chatroom server error: secret stack trace'});
  assert.equal(shown[0], 'AI: inference_error');
  assert.equal(state._eventToMessage(error), null);
  assert.deepEqual(state.getHistory(), []);
});

test('API opts into diagnostics only for explicit preview polling and history', async () => {
  const compiledApi = buildSync({stdin: {
    contents: 'export * from "./data/api"; export { _$ } from "./lib/jquery";',
    resolveDir: path.join(__dirname, '../src'), loader: 'ts',
  }, bundle: true, platform: 'node', format: 'cjs', write: false}).outputFiles[0].text;
  const apiModule = new Module(__filename);
  apiModule.paths = module.paths;
  apiModule._compile(compiledApi, __filename);
  const api = apiModule.exports;
  const requests = [];
  api._$.ajax = async request => { requests.push(request); return {}; };
  await api.pollMessages('https://example.test', 'token', 'cursor');
  await api.fetchHistory('https://example.test', 'token', null);
  await api.pollMessages('https://example.test', 'token', 'cursor', true);
  await api.fetchHistory('https://example.test', 'token', 'older', 50, true);
  assert.deepEqual(requests.map(r => new URL(r.url).searchParams.get('debug')), [null, null, '1', '1']);
  assert.ok(requests.every(r => r.headers.Authorization === 'Bearer token'));
});
