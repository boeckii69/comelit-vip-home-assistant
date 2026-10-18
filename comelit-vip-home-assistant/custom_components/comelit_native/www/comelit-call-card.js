/* Comelit Native 0.2.0b1: HA playback; two-way audio uses the optional SIP bridge. */
class ComelitNativeCall extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._generation = 0;
    this._sources = new Set();
    this._busy = false;
    this._message = "";
    this.shadowRoot.innerHTML = `
      <style>
        :host{--comelit-accent:#268e7a}
        ha-card{padding:24px;border-radius:20px;overflow:hidden}
        header{display:flex;align-items:center;gap:14px;margin-bottom:22px}
        header svg{width:48px;height:48px;flex-shrink:0}
        h2{margin:0;font-size:20px;letter-spacing:-.3px;font-weight:650}
        .eyebrow{font-size:11px;font-weight:600;letter-spacing:1.6px;margin-bottom:3px;color:var(--secondary-text-color)}
        p{line-height:1.5;margin:8px 0;color:var(--secondary-text-color)}
        #status{font-size:18px;font-weight:600;color:var(--primary-text-color)}
        .controls{display:flex;gap:10px;flex-wrap:wrap;margin-top:20px}
        button{font:inherit;font-size:14px;padding:12px 16px;border-radius:12px;cursor:pointer;
          border:1px solid var(--divider-color);color:var(--primary-text-color);
          background:var(--card-background-color);min-height:44px}
        button.primary{background:var(--comelit-accent);color:#fff;border-color:transparent}
        button:focus-visible{outline:3px solid var(--primary-color);outline-offset:3px}
        button:disabled{opacity:.4;cursor:default}#message{color:var(--error-color)}
        #message:empty{display:none}
        small{display:block;margin-top:20px;padding-top:14px;border-top:1px solid var(--divider-color);
          color:var(--secondary-text-color);line-height:1.5;font-size:12px}
        @media(max-width:380px){ha-card{padding:18px}.controls button{flex:1}}
      </style>
      <ha-card>
        <header><svg viewBox="0 0 1024 1024" aria-hidden="true"><rect width="1024" height="1024" rx="220" fill="#102D38"/>
          <rect x="220" y="190" width="360" height="644" rx="42" fill="#193F4C" stroke="#F2FAF8" stroke-width="30"/>
          <path d="M292 272V752" stroke="#4CE0BB" stroke-width="22"/><circle cx="488" cy="504" r="20" fill="#F2FAF8"/>
          <path d="M680 469V555 M764 389V635 M848 443V581" stroke="#4CE0BB" stroke-width="30" stroke-linecap="round"/></svg>
          <div><div class="eyebrow">COMELIT ViP NATIVE</div><h2 id="title">Door audio</h2></div></header>
        <p id="status" aria-live="polite"></p><p id="detail"></p>
        <div class="controls">
          <button id="answer" class="primary">Answer &amp; listen</button>
          <button id="listen">Listen</button><button id="hangup">Hang up</button>
        </div>
        <p id="message" role="status"></p>
        <small id="hint">Listen here. Talk through Loxone.</small>
      </ha-card>`;
    this.shadowRoot.getElementById("answer").onclick = () => this._answer();
    this.shadowRoot.getElementById("listen").onclick = () => this._listen();
    this.shadowRoot.getElementById("hangup").onclick = () => this._hangup();
  }

  setConfig(config) {
    this._config = config;
    this._render();
  }

  set hass(hass) {
    const before = this._state?.attributes?.can_hangup;
    this._hass = hass;
    this._render();
    if (before && !this._state?.attributes?.can_hangup) this._stopListening();
  }

  getCardSize() { return 3; }
  disconnectedCallback() { this._stopListening(); }

  _t(de, en) {
    return (this._hass?.locale?.language || this._hass?.language || "en").startsWith("de") ? de : en;
  }

  _render() {
    if (!this._hass || !this._config) return;
    const candidates = Object.values(this._hass.states).filter(
      state => state.attributes.comelit_native_call === true);
    this._state = this._config.entity ? this._hass.states[this._config.entity] :
      (candidates.length === 1 ? candidates[0] : undefined);
    const state = this._state;
    const attr = state?.attributes || {};
    const names = {
      idle: this._t("Bereit", "Ready"), ringing: this._t("Es klingelt", "Someone is at the door"),
      answering: this._t("Gespräch wird angenommen …", "Answering …"),
      dialing: this._t("Verbindung wird aufgebaut …", "Calling …"),
      waiting_audio: this._t("Warte auf Ton …", "Waiting for audio …"),
      audio_receiving: this._t("Gespräch aktiv", "Conversation active"),
      ending: this._t("Gespräch wird beendet …", "Ending call …"),
      ended: this._t("Gespräch beendet", "Call ended"),
      missed: this._t("Klingelruf beendet", "Ring ended"),
      failed: this._t("Gespräch fehlgeschlagen", "Call failed")};
    this.shadowRoot.getElementById("title").textContent = this._t("Türgespräch", "Door audio");
    this.shadowRoot.getElementById("hint").textContent = this._t(
      "Hier hörst du den Ton der Außenstation. Gegensprechen erfolgt in Loxone.",
      "Listen to the entrance here. Use Loxone for two-way speech.");
    this.shadowRoot.getElementById("status").textContent = state ?
      (names[state.state] || state.state) : (candidates.length > 1 ?
        this._t("Rufstatus-Entität in der Kartenkonfiguration auswählen.", "Select a call-state entity in the card configuration.") :
        this._t("Keine Rufstatus-Entität gefunden. Integration prüfen.", "No call-state entity found. Check the integration."));
    this.shadowRoot.getElementById("detail").textContent = attr.call_error ||
      (attr.can_hangup ? this._t("Du kannst mithören oder das Gespräch beenden.", "You can listen or end the conversation.") :
        (attr.end_reason || this._t("Einen eingehenden Ruf kannst du hier annehmen.", "You can accept an incoming call here.")));
    this.shadowRoot.getElementById("answer").textContent = this._t("Annehmen & hören", "Answer & listen");
    this.shadowRoot.getElementById("hangup").textContent = this._t("Auflegen", "Hang up");
    this.shadowRoot.getElementById("answer").disabled = this._busy || !attr.can_answer;
    this.shadowRoot.getElementById("listen").disabled = this._busy || !attr.can_hangup;
    this.shadowRoot.getElementById("listen").textContent = this._unsubscribe ? this._t("Ton aus", "Mute") : this._t("Hören", "Listen");
    this.shadowRoot.getElementById("hangup").disabled = this._busy || !attr.can_hangup;
    this.shadowRoot.getElementById("message").textContent = this._message;
  }

  async _startListening() {
    if (this._unsubscribe) {
      await this._context.resume();
      return;
    }
    this._stopListening();
    const generation = this._generation;
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) throw new Error(this._t("Dieser Browser unterstützt keine Web-Audio-Wiedergabe.", "This browser does not support Web Audio playback."));
    // Create/resume directly in the button gesture, including on mobile Safari.
    const context = new AudioContext();
    this._context = context;
    this._nextTime = 0;
    await context.resume();
    if (generation !== this._generation) throw new Error(this._t("Wiedergabe abgebrochen", "Playback cancelled"));
    const unsubscribe = await this._hass.connection.subscribeMessage(
      event => {
        if (generation === this._generation && event.type === "pcm") this._play(event);
      }, { type: "comelit_native/audio/subscribe", entity_id: this._state.entity_id });
    if (generation !== this._generation) {
      await unsubscribe();
      throw new Error(this._t("Wiedergabe abgebrochen", "Playback cancelled"));
    }
    this._unsubscribe = unsubscribe;
  }

  _play(event) {
    const context = this._context;
    if (!context || context.state !== "running" || event.sample_rate !== 8000) return;
    const raw = atob(event.data);
    if (raw.length !== 1600) return;
    const bytes = Uint8Array.from(raw, char => char.charCodeAt(0));
    const view = new DataView(bytes.buffer);
    const buffer = context.createBuffer(1, raw.length / 2, 8000);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < channel.length; i++) channel[i] = view.getInt16(i * 2, true) / 32768;
    // Bound queued audio after stalls/reconnects; never play seconds of old speech.
    if (this._nextTime > context.currentTime + 0.5) return;
    const start = Math.max(this._nextTime, context.currentTime + 0.05);
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    source.onended = () => { this._sources.delete(source); source.disconnect(); };
    this._sources.add(source);
    source.start(start);
    this._nextTime = start + buffer.duration;
  }

  _stopListening() {
    this._generation++;
    const unsubscribe = this._unsubscribe;
    this._unsubscribe = null;
    if (unsubscribe) Promise.resolve().then(unsubscribe).catch(() => {});
    for (const source of this._sources) { try { source.stop(); } catch (_) {} }
    this._sources.clear();
    if (this._context) this._context.close().catch(() => {});
    this._context = null;
    this._render();
  }

  async _answer() {
    if (this._busy || !this._state?.attributes?.can_answer) return;
    this._busy = true; this._message = ""; this._render();
    try {
      const entity_id = this._state.entity_id;
      await this._startListening();
      await this._hass.callWS({type: "comelit_native/call", entity_id, action: "answer"});
    } catch (error) {
      this._stopListening();
      this._message = error.message || String(error);
    } finally { this._busy = false; this._render(); }
  }

  async _listen() {
    if (this._busy || !this._state?.attributes?.can_hangup) return;
    if (this._unsubscribe) { this._stopListening(); return; }
    this._busy = true; this._message = ""; this._render();
    try { await this._startListening(); }
    catch (error) { this._stopListening(); this._message = error.message || String(error); }
    finally { this._busy = false; this._render(); }
  }

  async _hangup() {
    if (this._busy || !this._state?.attributes?.can_hangup) return;
    this._busy = true; this._message = ""; this._stopListening();
    try { await this._hass.callWS({type: "comelit_native/call",
      entity_id: this._state.entity_id, action: "hangup"}); }
    catch (error) { this._message = error.message || String(error); }
    finally { this._busy = false; this._render(); }
  }
}

if (!customElements.get("comelit-native-call")) {
  customElements.define("comelit-native-call", ComelitNativeCall);
  window.customCards = window.customCards || [];
  window.customCards.push({type: "comelit-native-call", name: "Comelit ViP · Door audio",
    description: "Accept a native door call, listen and hang up. Playback only; use Loxone for two-way audio."});
}
