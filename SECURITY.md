# Security and privacy

## Access model

- Native ViP/CTP and local SIP are trusted-LAN protocols. Keep their ports off the public internet.
- Local Loxone phones are admitted by exact IPv4 addresses. This is not per-user cryptographic authentication and does not protect against an attacker with control of the trusted LAN.
- The optional gateway authenticates to HA with `X-Comelit-Gateway`. Its key does **not** identify the external caller.
- **Automatic external mode accepts calls from anyone who knows the Antisip address.** It is disabled by default and requires deliberate opt-in. It can activate entrance audio without a preceding ring.
- Manual mode grants the next valid external call for 30 seconds. The grant is one-use but is not bound to a named Loxone user. It narrows the time window, not the caller identity.
- SIP TLS protects provider signaling/registration. This project does not claim end-to-end encryption of RTP or native ViP audio.
- Door release is a separate HA action. An incoming SIP call does not unlock a door. A HA token used for the Loxone HTTP output has the permissions of its HA user and must be protected accordingly.
- HA browser playback requires an authenticated session and control permission for this integration's call-state entity. Playback starts through an explicit UI action.

One active conversation and bounded durations/buffers limit resource use. Those limits are not a substitute for access control. An anonymous caller may occupy the single conversation slot in automatic mode.

## Data handling

No project telemetry or speech recording is implemented. Audio is processed in memory. State attributes and logs contain operational data such as IPs, ViP addresses, timestamps and call IDs. The optional provider and STUN services see connection metadata; the provider's role must not be confused with a wholly local path.

Gateway keys, HA tokens and SIP passwords belong only in local configuration. HA backups can contain them. Do not upload backups, `/data/options.json`, `.storage`, raw logs with secrets, identifying camera screenshots or audio recordings to an issue. Rotate any credential that has been exposed.

## Reporting a vulnerability

Use this repository's **Security → Report a vulnerability** when private vulnerability reporting is enabled. If it is unavailable, open an issue containing only a request for a private contact channel, with no exploit details or secrets. There is no promised response time or formal security audit. Maintainers should enable GitHub private vulnerability reporting before publication.
