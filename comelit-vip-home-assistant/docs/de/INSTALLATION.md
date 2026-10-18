# Home Assistant einrichten

[Projektübersicht](../../README.de.md) · [Loxone](LOXONE.md) · [Externes Audio](EXTERNES_AUDIO.md)

## Voraussetzungen

- Comelit **UT8020 + TP6842** mit funktionierendem LAN und bekanntem ViP-Adressplan. Andere Modelle sind nicht bestätigt.
- Home Assistant **2026.3 oder neuer**. Für das optionale Audio-Gateway: HA OS/Supervisor auf amd64 oder aarch64.
- HA und ViP-Geräte im selben IPv4-Broadcastnetz. Bei HA Container muss die Netzwerkumgebung den LAN-/Broadcastverkehr ermöglichen; Docker-Bridge-NAT ist kein bestätigter Betriebsweg.
- Feste Geräteadressen oder passende DHCP-Reservierungen, keine doppelt vergebenen IPs.
- Eine freie ViP-Slave-Adresse. Ein anderer Client darf nicht gleichzeitig dieselbe Adresse nutzen.

Das Projekt verändert keine Comelit-Gerätekonfiguration. Es scannt nicht das Netz und installiert keine Windows-Software. Sichere vor der Installation deine HA-Konfiguration.

## 1. Integration installieren

### Über HACS

1. Die GitHub-URL dieses Projekts kopieren.
2. In HA **HACS → Menü ⋮ → Benutzerdefinierte Repositories** öffnen.
3. URL eintragen, Kategorie **Integration** wählen und hinzufügen.
4. **Comelit ViP Native** herunterladen. Für eine Beta gegebenenfalls Vorabversionen anzeigen lassen.
5. Home Assistant neu starten.
6. **Einstellungen → Geräte & Dienste → Integration hinzufügen → Comelit ViP Native**.

### Manuell

1. Release-ZIP entpacken.
2. Den Ordner `custom_components/comelit_native` nach `/config/custom_components/comelit_native` kopieren. Darin muss direkt `manifest.json` liegen; keinen zweiten `comelit_native`-Unterordner anlegen.
3. HA neu starten und die Integration wie oben hinzufügen.

`install_external_audio.sh` ist eine optionale Hilfe für eine lokale Installation von Integration **und** Audio-App. Für die normale Repository-Installation wird es nicht benötigt.

## 2. Eigene Anlagenwerte eintragen

Die IP-Felder sind bei einer neuen Einrichtung absichtlich leer. Trage die Werte deiner Anlage ein; die unten genannten ViP-Adressen sind nur Beispiele.

| Feld | Bedeutung |
| --- | --- |
| Home-Assistant-LAN-IP | Stabile LAN-IP des HA-Hosts |
| LAN-Broadcast-IP für CRP | Broadcast deines Subnetzes, bei einem `/24` oft die Adresse mit `.255` |
| TP6842-LAN-IP | Ethernet-Adresse des Masters |
| UT8020-IP | Adresse der Außenstation |
| Master-ViP-Adresse | Acht Ziffern, z. B. `00000001` |
| Eigene ViP-Slave-Adresse | Master-Adresse plus freie Ziffer 1–9, z. B. `000000011` |
| Außenstation-ViP-Adresse | Acht Ziffern, z. B. `00000002` |
| Master-/Außenstations-Port | Die Ausgangswerte belassen; der tatsächlich verwendete Port wird per CRP abgefragt |
| Türöffner-Relais | Das für deine Anlage richtige Relais, 1 oder 2 |
| Erkannte Rufe an Loxone senden | Nur aktivieren, wenn Loxone-IP und UDP-Eingang eingerichtet sind |
| SIP-Audio | Zunächst aus; später über **Konfigurieren** einschalten |
| Gateway-Schlüssel / Automatik | Zunächst leer / aus; nur für externes Audio |

Nach dem Speichern erscheint das Gerät **Comelit**. Die Entität **Registrierung** muss `registered` melden. Ein gespeichertes Formular allein beweist keine Verbindung. Bei Fehlern siehe [Fehlerhilfe](FEHLERHILFE.md).

## 3. Entitäten und Aktionen

| Entität im Gerät | Aufgabe |
| --- | --- |
| Klingel | Zwei Sekunden aktiv bei einem erkannten Ruf |
| Klingelereignis | HA-Event mit Typ `ring` |
| Registrierung | Verbindungsstatus und Zähler |
| Rufstatus | Native Rufsteuerung und Audiostatus |
| SIP-Status | Loxone-/Gateway-Verbindung |
| Tür öffnen | Expliziter Öffnungsbefehl; kein gemessener Türzustand |
| Ruf annehmen / Auflegen | Nur beim passenden Rufzustand verfügbar |
| Audio extern freigeben | Einmalige Freigabe im optionalen manuellen Modus |

Die tatsächlichen Entity-IDs hängen von Sprache, Namen und vorhandenen Einträgen ab. Kopiere sie aus **Entwicklerwerkzeuge → Zustände**. Das Busereignis heißt `comelit_ring`; die optionale Aktion heißt `comelit_native.authorize_remote_audio`.

## 4. Optionale HA-Hörkarte

Die Karte gibt den Ton wieder; Gegensprechen erfolgt über Loxone. Unter den Dashboard-Ressourcen hinzufügen:

```text
URL: /comelit_native/comelit-call-card.js?v=0.2.0b1
Typ: JavaScript-Modul
```

Danach eine manuelle Karte anlegen:

```yaml
type: custom:comelit-native-call
```

Die Rufstatus-Entität wird bei genau einem Gerät automatisch gefunden. Optional kann `entity: DEINE_RUFSTATUS_ENTITY_ID` angegeben werden. Wiedergabe startet erst durch eine Benutzeraktion.

## Aktualisieren und zurückkehren

Vor einem Update HA-Backup und Loxone-Projekt sichern. Integration über HACS aktualisieren, danach HA neu starten. Die Audio-App separat im App-Store aktualisieren; ein reiner Dateikopiervorgang ersetzt kein bereits gebautes App-Image.

Beim Wechsel von der bestätigten privaten 0.1.12 auf diese Community-Version bleibt die Domain `comelit_native` gleich. Bestehende Konfiguration und eindeutige Entity-IDs werden beibehalten; sichtbare Namen werden übersetzt. Prüfe nach einem Update die in Loxone hinterlegten Entity-IDs. Die veröffentlichte Paketversion ist eine neue Beta und noch keine erneute Hardwarebestätigung.

Eine bereits lokal installierte Audio-App und dieselbe App aus dem GitHub-Repository haben unterschiedliche Supervisor-Identitäten. **Nie beide gleichzeitig starten.** Für einen Wechsel alte App stoppen, Optionen lokal übertragen und erst dann die neue starten. Die bisherige funktionierende Installation muss für die Veröffentlichung nicht umgestellt werden.

Bei Problemen die vorherige Version bzw. das Backup wiederherstellen. Beim Rückgang auf Versionen ohne Automatikoption diese vorher ausschalten; bei Versionen ohne externes Gateway zusätzlich den Gateway-Schlüssel leeren und speichern. App vorher stoppen.

## Entfernen

Audio-App stoppen und entfernen, Comelit-Integration unter Geräte & Dienste löschen, danach in HACS entfernen bzw. den Integrationsordner löschen und HA neu starten. Zugehörige Loxone-Ausgänge und Dashboard-Ressourcen bei Bedarf entfernen. An der Comelit-Anlage wird dabei keine Konfiguration geändert.
