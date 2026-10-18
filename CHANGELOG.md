# Changelog

## 0.2.0b1 — Community release candidate

Based on field-confirmed integration 0.1.12 and audio gateway 0.1.1.

- Original local HA integration icon and light/dark wordmarks.
- German and English configuration text, entity names and playback-card labels.
- Neutral new-installation network fields, optional Loxone forwarding, recommended SIP port 5062 and masked gateway-key field.
- General installation, Loxone, external audio, troubleshooting, compatibility and protocol documentation.
- Preserved MIT attribution inside the HACS download, contribution and security guidance.
- HACS custom-repository layout and shared Supervisor repository metadata; audio app 0.1.2 adds presentation and neutral defaults only.
- Offline tests, CI validation and guarded repository finalization/release packaging.

Core CTP framing, native call control and audio codecs remain based on the working implementation. Existing configured addresses, domain and unique IDs are retained. This packaging/UI release needs its own HA installation check; it is not an additional hardware compatibility claim.

## 0.1.12 — 2026-10-18

Fixed SIP UDP socket rebinding after option changes. Added explicit opt-in automatic external audio mode. External Loxone operation reported working.

## 0.1.10–0.1.11

Added starting a native call without a previous ring and optional Antisip/Asterisk external gateway.

## 0.1.5–0.1.9

Established native registration/ringing, door release, HA listening, local Loxone SIP audio and in-call door release on the reference installation.
