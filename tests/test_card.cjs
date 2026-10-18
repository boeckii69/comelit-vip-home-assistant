/* Browser API doubles validate lifecycle/order; this is not a real HA browser. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function setup() {
  const actions = [], definitions = new Map();
  class Element {
    attachShadow() {
      const elements = new Map();
      this.shadowRoot = { innerHTML: '', getElementById(id) {
        if (!elements.has(id)) elements.set(id, {});
        return elements.get(id);
      }};
    }
  }
  class Context {
    constructor() { actions.push('create'); this.currentTime = 0; this.state = 'suspended'; }
    async resume() { actions.push('resume'); this.state = 'running'; }
    async close() { actions.push('close'); this.state = 'closed'; }
    createBuffer(channels, count, rate) {
      this.samples = new Float32Array(count);
      return {duration: count / rate, getChannelData: () => this.samples};
    }
    createBufferSource() {
      return {connect() {}, disconnect() {}, start() { actions.push('play'); }, stop() { actions.push('stop'); }};
    }
  }
  const sandbox = {HTMLElement: Element, window: {AudioContext: Context}, atob,
    customElements: {get: key => definitions.get(key), define: (key, value) => definitions.set(key, value)}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,
    '../custom_components/comelit_native/www/comelit-call-card.js'), 'utf8'), sandbox);
  const card = new (definitions.get('comelit-native-call'))();
  const id = 'sensor.aussenanlage_comelit_rufstatus';
  const hass = {states: {[id]: {entity_id: id, state: 'ringing',
    attributes: {comelit_native_call: true, can_answer: true, can_hangup: false}}},
    connection: {async subscribeMessage(cb, msg) { actions.push('subscribe'); hass.emit = cb;
      assert.equal(msg.entity_id, id); return async () => actions.push('unsubscribe'); }},
    async callWS(msg) { actions.push(msg.action); assert.equal(msg.entity_id, id); }};
  card.setConfig({type: 'custom:comelit-native-call'});
  card.hass = hass;
  return {card, hass, actions, id};
}

test('audio is enabled in the click, subscription precedes answer, dynamic entity is used', async () => {
  const {card, actions} = setup();
  await card._answer();
  assert.deepEqual(actions, ['create', 'resume', 'subscribe', 'answer']);
  card.disconnectedCallback();
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(actions.includes('unsubscribe'));
  assert.ok(actions.includes('close'));
  assert.ok(!actions.includes('hangup'));
});

test('PCM uses signed little-endian samples and queue cannot grow indefinitely', async () => {
  const {card, hass, actions} = setup();
  await card._answer();
  const bytes = Buffer.alloc(1600);
  bytes.writeInt16LE(-32768, 0); bytes.writeInt16LE(16384, 2);
  for (let n = 0; n < 100; n++) hass.emit({type: 'pcm', sample_rate: 8000, data: bytes.toString('base64')});
  assert.equal(card._context.samples[0], -1);
  assert.equal(card._context.samples[1], 0.5);
  assert.ok(actions.filter(x => x === 'play').length <= 6);
  card.disconnectedCallback();
});

test('failed subscription never answers; failed answer closes playback', async () => {
  let {card, hass, actions} = setup();
  hass.connection.subscribeMessage = async () => { throw new Error('No permission'); };
  await card._answer();
  assert.ok(!actions.includes('answer'));
  assert.equal(card._message, 'No permission');
  ({card, hass, actions} = setup());
  hass.callWS = async () => { throw new Error('Call ended'); };
  await card._answer();
  assert.equal(card._context, null);
  assert.equal(card._message, 'Call ended');
});

test('disconnect while subscribing discards late subscription and prevents answer', async () => {
  const {card, hass, actions} = setup();
  let resolve;
  hass.connection.subscribeMessage = () => new Promise(done => { resolve = done; });
  const pending = card._answer();
  await new Promise(done => setImmediate(done));
  card.disconnectedCallback();
  resolve(async () => actions.push('unsubscribe'));
  await pending;
  assert.ok(!actions.includes('answer'));
  assert.ok(actions.includes('unsubscribe'));
});
