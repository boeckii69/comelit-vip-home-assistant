# Audio von unterwegs

Voraussetzung: Lokales Gegensprechen funktioniert. Der vorhandene Fernzugriff der Loxone-App für Bedienung, Bild und Türöffner wird separat eingerichtet; dieses Gateway ergänzt ausschließlich Audio.

## 1. Audio-App installieren

In HA **Einstellungen → Apps/Add-ons → Store → ⋮ → Repositories** die GitHub-URL dieses Projekts hinzufügen. **Comelit ViP · External Audio** installieren. Sie wird auf dem HA-Host gebaut und benötigt Internet zum Abruf der Alpine-Pakete.

Die App benötigt HA OS/Supervisor, Hostnetzwerk und amd64 oder aarch64. Eine andere lokale Kopie der App zuerst stoppen. HACS installiert diese App nicht mit.

## 2. Konto und Schlüssel

Ein eigenes Konto beim [Antisip-SIP-Dienst](https://sip.antisip.com/) anlegen. Zugangsdaten bleiben in der lokalen App-Konfiguration. Einen unabhängigen zufälligen Gateway-Schlüssel mit 32–128 Zeichen aus `A–Z`, `a–z`, `0–9`, `_`, `-` erzeugen, z. B. mit einem Passwortmanager oder:

```sh
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

| App-Option | Eintrag |
| --- | --- |
| `sip_username` | Antisip-Benutzername, klein geschrieben |
| `sip_password` | Antisip-Passwort |
| `bridge_key` | Der neu erzeugte Gateway-Schlüssel |
| `ha_ip` | Tatsächliche HA-LAN-IP |
| `ha_sip_port` | Derselbe SIP-Port wie in der Integration, üblicherweise `5062` |
| `local_network` | Eigenes IPv4-LAN in CIDR-Schreibweise, z. B. Netzadresse plus `/24` |
| `public_media_ip` | Zunächst leer |
| `turn_server`, `turn_username`, `turn_password` | Zunächst leer; nur mit einem selbst bereitgestellten TURN-Dienst verwenden |

In **Comelit ViP Native → Konfigurieren** denselben Schlüssel unter **Schlüssel des externen Audio-Gateways** eintragen. Lokales SIP muss eingeschaltet sein. HA-Token, SIP-Passwort und Gateway-Schlüssel sind drei unterschiedliche Zugangsdaten.

## 3. Zugriffsmodus wählen

**Automatik:** „Externes Audio automatisch annehmen (ohne Freigabeknopf)“ aktivieren. Danach zeigt der SIP-Status `remote_audio_mode: automatic`. Der normale Loxone-Hörerknopf startet das Gespräch. **Jeder mit Kenntnis der Antisip-SIP-Adresse kann Audio aktivieren. Es gibt keine Authentifizierung einzelner externer Loxone-Nutzer.**

**Manuell, Standard:** Vor dem Gespräch die HA-Aktion `comelit_native.authorize_remote_audio` oder die Schaltfläche **Audio extern freigeben** auslösen. Alternativ per authentifiziertem Loxone-HTTP-POST an `/api/services/comelit_native/authorize_remote_audio`, Body `{}`, gleiche HA-Header wie beim Türöffner. Die nächste gültige externe Verbindung muss innerhalb von 30 Sekunden eintreffen. Die Freigabe ist einmalig und nicht an einen bestimmten Anrufer gebunden. Sie schaltet weder Mikrofon noch Türöffner selbst ein.

## 4. Start und Loxone-Felder

Audio-App starten. Im Protokoll müssen die STUN-Vorabprüfung und danach **Antisip-Registrierung: Registered** erscheinen. Anschließend bei gewünschtem Dauerbetrieb den Start beim Booten einschalten.

| Loxone-Feld | Wert |
| --- | --- |
| Audiohost extern | `sip.antisip.com` |
| Audiobenutzer extern | Eigener Antisip-Benutzername |
| Audiohost intern | Weiterhin `HA_IP:5062` bzw. eigener Port |
| Audiobenutzer intern | `comelit` |

Loxone-Projekt in den Miniserver übertragen. Nach zuvor fehlgeschlagenen Gesprächen die App vollständig schließen und erneut öffnen. Über Mobilfunk den Hörerknopf verwenden. Mehrere Nutzer können dieselbe Tür aufrufen; nur eine Unterhaltung gleichzeitig.

## Netz und Grenzen

Der Integrations-SIP-Port bleibt im LAN. **Keine Weiterleitung des HA-Ports oder von SIP 5062 ins Internet einrichten.** Das Gateway registriert sich ausgehend per TLS bei Antisip; STUN/ICE vermittelt den Medienweg. STUN wird zuerst bei Antisip und gegebenenfalls bei Google versucht. Diese Dienste sehen die öffentliche IP und die nötigen Verbindungsmetadaten.

Lokal benötigt die App zusätzlich `5064/UDP`, `5065/TCP` und `20000–20039/UDP`. Die Integration verwendet für App-RTP dynamische Ports `40000–59999/UDP`; native ViP-Ports werden ausgehandelt. Dieselben Ports dürfen nicht von einer zweiten App belegt sein.

`Registered` allein beweist keinen funktionierenden Ton. Andere Router, CGNAT oder Mobilfunknetze können weitere Maßnahmen bzw. einen passenden TURN-Dienst erfordern. TURN ist ein optionales Konfigurationsfeld, kein mitgelieferter Relay-Dienst und kein bestätigter Weg für jede Netzkonstellation. Auch mit TURN verlangt diese Version eine erfolgreiche STUN-Vorabprüfung.

SIP-TLS schützt die Anmeldung, nicht automatisch den gesamten Audioweg. Keine Sprachaufnahmen werden vom Projekt gespeichert. Begrenzung: etwa zwei Minuten pro Gespräch. Für Symptome siehe [Fehlerhilfe](FEHLERHILFE.md).
