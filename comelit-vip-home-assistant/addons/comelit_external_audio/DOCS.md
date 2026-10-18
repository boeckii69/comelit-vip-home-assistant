# External Audio / Externes Audio

## Configuration

| Field | Value |
| --- | --- |
| sip_username | Your lowercase Antisip username |
| sip_password | Your Antisip password |
| bridge_key | Random 32–128 characters: A–Z, a–z, 0–9, _ or - |
| ha_ip | Actual HA LAN IPv4 address; required |
| ha_sip_port | Integration SIP port, normally 5062 |
| local_network | Your LAN in CIDR notation; required |
| public_media_ip | Initially empty for STUN discovery |
| turn_server / turn_username / turn_password | Initially empty; optional user-provided service |

Set the **same bridge_key** in Comelit ViP Native's gateway-key option. This is neither a HA token nor your SIP password. Keep credentials local.

In the integration choose manual mode (one-use, 30-second grant) or explicitly enable automatic mode. Automatic mode lets the normal Loxone handset button start audio, but anyone knowing the Antisip address can also call. No individual external-user authentication is provided.

Start the app and look for successful STUN preflight and `Antisip-Registrierung: Registered`. Registration alone does not prove working media. Set the Loxone external audio host to `sip.antisip.com`, external user to your Antisip username. Keep internal host `HA_IP:5062` and user `comelit`.

The app uses host ports 5064/UDP, 5065/TCP and 20000–20039/UDP. Stop any other local/repository copy before starting. Do not forward HA or the native SIP port from the internet. Router/mobile NAT may require a suitable TURN service; no universal NAT compatibility is claimed. This version requires STUN preflight even with TURN configured. SIP TLS is not end-to-end media encryption.

Enable start-on-boot after successful setup if desired. Integration and app updates are separate. No recording, AMI, ARI or general PBX/PSTN dialplan is exposed.

## Deutsche Schrittfolge

1. HA-LAN-IP und eigenes Netz eintragen; keine Beispieladresse übernehmen.
2. Antisip-Zugangsdaten und einen zufälligen Gateway-Schlüssel eintragen.
3. Denselben Schlüssel in der Integration speichern, SIP einschalten.
4. Für den direkten Hörerknopf Automatik bewusst aktivieren. Andernfalls gilt die einmalige Freigabe.
5. App starten, STUN und Registered prüfen, externe Audiofelder in Loxone setzen.

**Automatik erlaubt jedem mit Kenntnis deiner Antisip-Adresse, Audio zu aktivieren.** Einzelne Loxone-Nutzer werden nicht authentifiziert. Ein Gespräch gleichzeitig.

[Deutsche Anleitung](https://github.com/boeckii69/comelit-vip-home-assistant/blob/main/docs/de/EXTERNES_AUDIO.md) · [English guide](https://github.com/boeckii69/comelit-vip-home-assistant/blob/main/docs/en/setup.md)
