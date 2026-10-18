# Compatibility and validation

## Hardware report

| Combination | Evidence |
| --- | --- |
| UT8020 entrance + TP6842 master, native Ethernet ViP/CTP | One installation reported working on 2026-10-18 |
| Ringing and Loxone UDP forwarding | Reported working |
| Door release, idle and during an owned conversation | Reported working |
| Local Loxone two-way audio and calls without previous ringing | Reported working |
| External Loxone audio via Antisip | Reported working after enabling automatic mode in 0.1.12 |
| Multiple phone users | Configurable allowlist; simultaneous calls are not supported; broader multi-user field validation is outstanding |
| Other panels, monitors or firmware | Unverified |
| Optional TURN service | Configuration support only; not field-verified in the reference installation |
| New community package 0.2.0b1 | Based on the above code; publication/UI changes need separate installation validation |

Firmware revisions, HA version and Loxone app version of the reference installation were not recorded precisely. Do not infer universal compatibility from model names alone. No long-duration soak-test claim is made.

## Scope

One HA config entry, one master identity, one entrance identity, one active conversation. IPv4 LAN and broadcast reachability are required. The TP6842's native UDP/CTP route is used; an ICONA TCP 64100 bridge is not required. No cloud connection is needed for the local bridge; the optional external path uses Antisip and STUN services.

The camera is outside this integration. No camera entity, video transcoder, physical lock-contact sensor, general SIP PBX, PSTN service, browser microphone or automated door unlock is included.

## Automated evidence

The existing test suite covers CTP framing, CRP correlation, registration, ring deduplication, door commands, owned-call release, SIP dialog lifecycle, bidirectional G.711/RTP, malformed input, exact-peer checks, cancellation, bounded audio buffers and options changes. Network tests bind to IPv4 loopback; fake endpoints never actuate a real door.

Three optional tests launch a real Asterisk 20 runtime with a local TLS provider, independently verified Digest registration, a local STUN server and a fake native entrance. They exercise manual rejection, authorized manual audio and automatic audio with door-command/BYE handling. They are skipped unless `COMELIT_ASTERISK_RUNTIME` is supplied. HA API tests use doubles, not a full HA boot. Container/Supervisor build and acoustic hardware behavior are separate checks.

Community packaging retains the core CTP/call/media implementation. New defaults and presentation changes must pass the automated suite before release. See [CONTRIBUTING.md](../CONTRIBUTING.md).
