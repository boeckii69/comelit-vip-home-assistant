# Fehler gezielt eingrenzen

Verwende die Zustände und Attribute der Entitäten **Registrierung**, **SIP-Status** und **Rufstatus**. Dazu gegebenenfalls die letzten relevanten App-Protokollzeilen. Entferne IPs, ViP-Identitäten, Kontonamen und Zugangsdaten vor einer Veröffentlichung. Ein neuer Paketmitschnitt ist kein regulärer Installationsschritt.

| Beobachtung | Bedeutung / nächster Schritt |
| --- | --- |
| `registration_failed`, `crp_responses: 0` | Adressen, Broadcastnetz und Erreichbarkeit prüfen; nicht wahllos Ports wechseln |
| CRP erfolgreich, keine CTP-Antwort | HA-Rückweg, tatsächliche LAN-IP und doppelte IP-Belegung prüfen |
| `registered`, `crp_return_path_matches: false` | Kann beim bestätigten Protokollpfad normal sein; REGISTER-Bestätigung ist maßgeblich |
| SIP `disabled` | SIP in den Integrationsoptionen einschalten |
| SIP `Address already in use` | Ab 0.1.12 wird das eigene UDP-Socket korrekt geschlossen; andere SIP-Dienste oder eine zweite Gateway-App auf denselben Ports prüfen |
| SIP `403`, „Zuerst Audio extern …“, `remote_audio_mode: manual` | Manueller Modus verlangt eine Freigabe. Für den direkten Hörerknopf Automatik bewusst einschalten |
| SIP `403` mit anderem Text | Gateway-Schlüssel und Zugriffsprüfung anhand von `sip_error` prüfen |
| SIP `488` | Angebotene Codecs/Medienziele passen nicht; PCMA/PCMU und Gateway-Medienbindung prüfen |
| `sip_received: 0` nach externem Anruf | Anfrage erreicht den HA-SIP-Socket nicht; Loxone-Ziel, Gateway-Registrierung und App-Protokoll prüfen |
| `Registered`, aber kein Ton | Anmeldung ist erfolgreich; Medienweg und Audiozähler separat prüfen |
| STUN-Timeout | Ausgehendes UDP/DNS, ausgewählten STUN-Server und App-Version prüfen |
| „A call is already active“ in Loxone | App kann noch eine alte Sitzung halten; Gespräch beenden und App vollständig schließen/öffnen |
| Zweites Handy lokal abgewiesen | Seine aktuelle WLAN-IPv4-Adresse zur Liste erlaubter Geräte hinzufügen |
| Türöffner ohne Funktion | Tatsächliche Entity-ID, HTTP-Methode POST, HA-Berechtigung und Relais prüfen |
| Annehmen/Auflegen `unavailable` | Ohne passenden Rufzustand erwartet |
| Bild fehlt | Kamerabild ist ein separat konfigurierter Weg und wird nicht von dieser Integration geliefert |
| Symbol fehlt | HA-Version ab 2026.3 und vollständigen `brand/`-Ordner prüfen; UI neu laden |
| „Invalid flow specified“ | Veralteten Konfigurationsdialog schließen, HA-Seite neu laden und frisch öffnen |

`hardware_verified: false` ist ein historischer statischer Diagnosewert für den nicht umfassend geprüften Geräteumfang, kein aktueller Verbindungstest. Die konkrete Referenzanlage wurde als funktionierend gemeldet. Eine Protokollbestätigung des Türöffners ist keine Rückmeldung eines Türkontakts.

Bei einem Issue bitte Hardware und Firmware, HA-/Integrations-/App-Version, lokal oder mobil, erwartetes Verhalten und bereinigte Statusdaten nennen. Kein vollständiges HA-Backup und keine Zugangsdaten hochladen.
