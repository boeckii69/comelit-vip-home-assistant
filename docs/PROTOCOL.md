# Protocol notes

These notes describe the implemented interoperability path, not a complete manufacturer specification. No vendor executables, license libraries or packet recordings are distributed.

## Discovery and registration

CRP uses UDP port 24100 to resolve the current native endpoint for a configured ViP identity. The query advertises HA's CTP return address. The response is correlated with the expected target identity and IP; the observed native path does not always echo the requester suffix. The live CTP port is dynamic and must not be replaced by a historical port from an example.

CTP carries connection identity, sequence/acknowledgment, optional logical source/destination and command data. Registration sends `REGISTER` (`0x0040`) and requires the matching `REGISTER_RES` (`0x0041`). A transport ACK alone is not a registration result. Registration is renewed, and error retries are bounded/backed off by the client.

## Calls, media and the door

An incoming native call generates one HA ring event; duplicate transport packets do not produce duplicate rings. A valid SIP request either answers a pending ring or starts a native entrance call when idle. Native controls and acknowledgments own a specific connection, not a global peer slot.

G.711 A-law/µ-law media uses 8 kHz mono audio. The media request in the current native session supplies the send destination. The receiving socket's peer port is not assumed to be the transmit port. SIP media targets must match the admitted local peer; the external Asterisk app anchors both legs.

The idle door-release path resolves the configured entrance and sends one explicit command. During a call owned by the bridge, it uses that call's existing control connection. Opening the door does not terminate the call and is not automatically retried after a missing confirmation. A protocol acknowledgment is not a physical door-position measurement.

## External bridge

The app registers to Antisip using SIP TLS. A bounded dialplan forwards only to the configured HA endpoint and adds the local gateway key. It exposes no general extension plan, AMI, ARI, recording or PSTN route. Asterisk's STUN dialect is probed before launch; a modern XOR-MAPPED-only response is insufficient for the tested Asterisk 20 legacy STUN engine.

Automatic mode removes the per-call grant, not caller anonymity. Read [SECURITY.md](../SECURITY.md) before exposing the SIP address.

## References

- [jfmlima/comelit-vip](https://github.com/jfmlima/comelit-vip): MIT CTP foundation; retained attribution.
- [nicolas-fricke/ha-component-comelit-intercom](https://github.com/nicolas-fricke/ha-component-comelit-intercom): independent community work consulted for context, not this installation's transport.
- [mnestrud/comelit-man](https://github.com/mnestrud/comelit-man): different bridge path; not a dependency of this project.
- [Asterisk 20.11.1 STUN implementation](https://github.com/asterisk/asterisk/blob/20.11.1/main/stun.c).
- [Asterisk RTP configuration](https://github.com/asterisk/asterisk/blob/20.11.1/configs/samples/rtp.conf.sample).
- [Antisip service information](https://sip.antisip.com/service/faq.html).
- [Loxone intercom documentation](https://www.loxone.com/dede/kb/loxone-intercom/).

Additional compatibility reports should separate packet/control evidence, received audio, actually heard audio and physical door operation.
