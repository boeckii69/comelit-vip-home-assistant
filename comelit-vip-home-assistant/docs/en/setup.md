# Setup guide

[Overview](../../README.md) · [Compatibility](../COMPATIBILITY.md) · [Security](../../SECURITY.md)

## Requirements

Home Assistant 2026.3+, a UT8020 entrance panel and TP6842 master with known IPv4/ViP addresses, and an unused slave identity. HA must reach the native broadcast domain. HA Container needs an appropriate host-network setup; Docker bridge NAT is not a verified deployment. The optional external-audio app requires Supervisor/HA OS on amd64 or aarch64. One integration instance and one conversation are supported.

Keep an existing working camera configuration. This integration does not create a camera entity. Back up HA and the Loxone project before installing.

## Install the integration

1. Copy this GitHub repository's URL.
2. In **HACS → ⋮ → Custom repositories**, add the URL with category **Integration**.
3. Download **Comelit ViP Native**. Show prereleases if needed for the beta.
4. Restart HA, then **Settings → Devices & services → Add integration → Comelit ViP Native**.

For manual installation, copy `custom_components/comelit_native` into `/config/custom_components/`, restart, then add the integration. `manifest.json` must be directly inside the integration folder. HACS does not install the optional audio app.

## Configure your installation

| Setting | What to enter |
| --- | --- |
| HA LAN IP | Stable IPv4 address of your HA host |
| CRP broadcast IP | Broadcast address of your subnet, not the router address |
| TP6842 LAN IP | Ethernet address of the master |
| UT8020 LAN IP | Entrance-panel address |
| Master ViP | Eight digits, including leading zeros |
| Own slave ViP | Master ViP plus an unused digit 1–9 |
| Entrance ViP | Eight-digit identity of the entrance panel |
| Master/entrance UDP ports | Initial values; CRP resolves the current ports before use |
| Door relay | Correct relay for your installation, 1 or 2 |
| Forward rings to Loxone | Enable only with the correct Miniserver IP and UDP input port |
| SIP / gateway key / automatic mode | Start disabled / empty / disabled |

Do not reuse the identity of another active monitor or PC client. Network fields start empty; addresses and ViP identities must match your installation. The registration sensor must report `registered` after setup. A saved configuration is not proof of connection. The current options page changes relay/audio settings; re-create the entry if fundamental network/ring settings need changing.

## Loxone ringing

Create a virtual UDP input in Loxone Config, for example on port `7777`, with the HA IP as sender. Match `COMELIT_RING=1` and generate a pulse to the door-controller bell input. Enable forwarding in the integration with the Miniserver IP and matching port. One datagram is sent per recognized call; there is no separate zero/reset datagram. Design the input to handle repeated calls as pulses.

## Loxone door release

Find the real door button entity ID under **Developer tools → States**. Create a HA long-lived access token locally and store it only in the Loxone HTTP output.

| Loxone HTTP setting | Value |
| --- | --- |
| Output address | Your actual HA base URL, commonly `http://HA_IP:8123` |
| On method | `POST` |
| On command | `/api/services/button/press` |
| On body | `{"entity_id":"YOUR_DOOR_BUTTON_ENTITY_ID"}` |
| Off command | Empty |

Headers:

```text
Authorization: Bearer YOUR_HA_TOKEN
Content-Type: application/json
```

Where a Loxone version uses a one-line header field, use its `\r\n` line separator. Connect the output to the desired door function and transfer the project to the Miniserver. A command acknowledgment does not measure the physical lock position.

## Local two-way audio

In **Comelit ViP Native → Configure**, enable SIP, choose an unused port (recommended `5062`) and list each phone's current WLAN IPv4 address, comma separated. Use DHCP reservations. The app makes the call; allowing only the Miniserver IP is insufficient.

In the Loxone door-controller/intercom audio settings:

| Setting | Value |
| --- | --- |
| Internal audio host | `HA_IP:5062`, or your chosen port |
| Internal audio user | `comelit` |

The local endpoint uses an exact-IP allowlist, not provider registration. The handset button answers a ringing native call or starts a new entrance call when idle. A second simultaneous caller is busy. Opening the door during an owned conversation remains a separate HA action.

## Optional external audio

The Loxone app's existing remote access for control/video/door release must already work. This project adds audio only.

1. In the HA app/add-on store, add this same repository URL under **⋮ → Repositories**.
2. Install **Comelit ViP · External Audio**. Stop any local copy first; two copies cannot share the host ports.
3. Create your own [Antisip](https://sip.antisip.com/) account and a separate random gateway key. The key must contain 32–128 letters, digits, `_` or `-`. A password manager or `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'` can create one locally.
4. Configure the app:

| Option | Value |
| --- | --- |
| `sip_username` / `sip_password` | Your Antisip credentials; lowercase username |
| `bridge_key` | Your random gateway key |
| `ha_ip` | HA LAN IPv4 address |
| `ha_sip_port` | Same port as the integration, usually 5062 |
| `local_network` | Your IPv4 LAN in CIDR notation |
| `public_media_ip` | Initially empty |
| `turn_server`, `turn_username`, `turn_password` | Initially empty; optional user-provided TURN service |

5. Set the same gateway key in the integration's options. Keep local SIP enabled.
6. Choose the access mode below, then start the app. Look for a successful STUN preflight and **Antisip-Registrierung: Registered**. Enable start-on-boot after successful operation if desired.
7. Set Loxone **external audio host** to `sip.antisip.com` and **external audio user** to your Antisip username. Keep the internal settings. Transfer the configuration.

**Automatic mode:** Enable “Accept external audio automatically”. SIP status must show `remote_audio_mode: automatic`. The normal handset button starts the conversation. Anyone who knows your SIP address can also call and activate audio; callers are not authenticated as individual Loxone users.

**Manual mode (default):** Call HA action `comelit_native.authorize_remote_audio` or press the corresponding button first. The next valid external call must arrive within 30 seconds. This is one-use but not bound to a named caller. From Loxone, the same grant can be an authenticated POST to `/api/services/comelit_native/authorize_remote_audio` with body `{}` and the HA headers above. It does not itself open a door or start audio.

Use mobile data to exercise the external route. If the app reports an old call still active, end it and fully close/reopen the Loxone app. Keep SIP/HA LAN ports private; do not forward them from the internet. Provider registration alone does not prove audio works. STUN/ICE and, where needed, your own TURN service depend on the router/mobile network. This version still requires successful STUN preflight even if TURN is configured. TLS registration does not imply end-to-end audio encryption. One call at a time, approximately two minutes maximum.

## Optional HA playback card

Add dashboard resource `/comelit_native/comelit-call-card.js?v=0.2.0b1` as a JavaScript module. Add a manual card with `type: custom:comelit-native-call`. It detects the single call-state sensor; optionally set `entity` to its actual ID. The card accepts, plays and hangs up; two-way speech uses Loxone.

## Diagnostics, updates and removal

Registration, SIP status and call state provide counters and error details. `403` plus `remote_audio_mode: manual` means a grant is required. `sip_received: 0` means the HA SIP endpoint has not received a request. STUN timeouts concern network/media preparation. `crp_return_path_matches: false` can appear on a working installation. The legacy `hardware_verified: false` attribute is static, not a live connectivity test. Answer/hang-up buttons are unavailable without a matching call state.

Back up before updating. Update the integration and the app separately; restart HA after an integration update. The unchanged domain/unique IDs preserve existing entries. Local and repository app installations have different Supervisor IDs: stop the old one before transferring settings to the new one. Do not run both. Revert to a saved release or HA backup if needed. Before downgrading to a version without automatic mode, disable it; before a version without the gateway, clear its key too.

To remove: stop/uninstall the audio app, delete the integration under Devices & services, remove its HACS/manual files and restart HA. Remove unused Loxone outputs/dashboard resources. No panel configuration is changed by removal.

For reports, include hardware/firmware, software versions, local vs mobile route and sanitized counters. Do not share tokens, passwords, gateway keys, identifying network data or recordings.
