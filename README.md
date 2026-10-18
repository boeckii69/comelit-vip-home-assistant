[README.de.md](https://github.com/user-attachments/files/33634155/README.de.md)
<p align="center"><img src="docs/assets/banner.png" alt="Comelit ViP Native — Home Assistant / Loxone" width="960"></p>

# Comelit ViP Native

**Klingeln, Tür öffnen und direkt über Loxone sprechen – mit Home Assistant als Brücke.**

[English](README.md) · [Installation](docs/de/INSTALLATION.md) · [Loxone](docs/de/LOXONE.md) · [Externes Audio](docs/de/EXTERNES_AUDIO.md) · [Fehlerhilfe](docs/de/FEHLERHILFE.md)

Diese Community-Integration verbindet eine Comelit-ViP-Anlage über ihren nativen UDP/CTP-LAN-Pfad mit Home Assistant. Loxone bleibt die Bedienoberfläche. Für Gespräche von unterwegs gibt es ein optionales Asterisk-Audio-Gateway mit Antisip. Der lokale Weg arbeitet ohne Cloud, Comelit-PC-Software oder USB-Lizenzschlüssel. Externes Audio verwendet dagegen einen SIP-Anbieter und STUN-Dienste.

**Community-Beta:** Der zugrunde liegende Stand 0.1.12 mit Audio-Gateway 0.1.1 wurde am 18. Oktober 2026 an einer UT8020/TP6842-Anlage als lokal und extern funktionierend bestätigt. Dieses neue Veröffentlichungspaket 0.2.0b1 ergänzt Gestaltung, Konfiguration und Dokumentation. Es wurde noch nicht als eigener Versionsstand an Hardware bestätigt. Andere Anlagen und Firmwarestände sind nicht automatisch kompatibel.

| Funktion | Umsetzung |
| --- | --- |
| Klingeln | HA-Ereignis, Klingelsensor und optionale UDP-Weiterleitung an Loxone |
| Tür öffnen | HA-Schaltfläche, auch aus Loxone und während eines eigenen Gesprächs |
| Hören und sprechen | Direkt in der Loxone-App über die HA-SIP-Brücke |
| Gespräch ohne vorheriges Klingeln | Über den Loxone-Hörerknopf bei freier Leitung |
| Audio unterwegs | Optionales Antisip/Asterisk-Gateway; abhängig vom Netz |
| Hören in HA | Zusätzliche Hörkarte mit Annehmen, Hören und Auflegen |
| Kamerabild | Wird separat eingebunden; dieses Projekt liefert keine Kamera-Entität |

## Installation in vier Schritten

1. [Voraussetzungen und HA-Installation](docs/de/INSTALLATION.md)
2. [Klingeln, Türöffner und lokales Audio in Loxone](docs/de/LOXONE.md)
3. Optional: [Audio von unterwegs](docs/de/EXTERNES_AUDIO.md)
4. [Funktionierenden Stand sichern und aktualisieren](docs/de/INSTALLATION.md#aktualisieren-und-zurückkehren)

Die Integration lässt sich nach Veröffentlichung über **HACS → Benutzerdefinierte Repositories → Integration** installieren. Repository-URL: `https://github.com/boeckii69/comelit-vip-home-assistant`. Die optionale Audio-App wird separat über dieselbe URL im HA-App-Store hinzugefügt. Eine Aufnahme in die allgemeine HACS-Liste wird damit nicht behauptet.

## Zugriff bei externem Audio

**Im Automatikmodus kann jeder, der deine Antisip-SIP-Adresse kennt, anrufen und Audio aktivieren. Einzelne Loxone-Nutzer werden nicht authentifiziert.** Der Gateway-Schlüssel sichert nur den lokalen Weg vom Audio-Gateway zu HA. Automatik ist standardmäßig aus. Die manuelle Freigabe gilt 30 Sekunden für den nächsten gültigen externen Ruf; auch sie ist nicht an eine bestimmte Person gebunden.

Es ist ein Gespräch gleichzeitig möglich. Türöffnen bleibt eine eigene HA-Aktion. SIP-TLS bedeutet keine Ende-zu-Ende-Verschlüsselung des Tons. Details: [SECURITY.md](SECURITY.md).

## Gemeinsam weiterentwickeln

Melde gerne bestätigte Geräte-/Firmwarekombinationen, konkrete Fehler und Verbesserungen. [CONTRIBUTING.md](CONTRIBUTING.md) erklärt, welche Angaben helfen. Zugangsdaten, private Kamerabilder und Sprachaufnahmen gehören nicht in öffentliche Issues.

MIT-Lizenz mit erhaltenem Urheberhinweis für den CTP-Codec aus [jfmlima/comelit-vip](https://github.com/jfmlima/comelit-vip). Eigenes Projektsymbol, keine offiziellen Herstellerlogos. Unabhängiges Community-Projekt ohne Verbindung zu den Herstellern.
