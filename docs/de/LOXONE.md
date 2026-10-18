# Loxone: Klingeln, Türöffner und Audio

Voraussetzung: HA zeigt `registered`. `HA_IP`, `LOXONE_IP` und Entity-IDs unten durch eigene Werte ersetzen.

## Klingeln

1. In Loxone Config einen **virtuellen UDP-Eingang** anlegen, z. B. Port `7777`. Als Absender die HA-IP verwenden.
2. Einen Eingangsbefehl zur Erkennung von `COMELIT_RING=1` anlegen und daraus einen Impuls für den Klingeleingang des Türsteuerungs-/Intercom-Bausteins bilden.
3. In der Comelit-Ersteinrichtung die Weiterleitung an Loxone aktivieren, Loxone-IP und denselben UDP-Port eintragen.

Es wird **ein Telegramm pro Ruf** gesendet, kein separates `COMELIT_RING=0`. Die Loxone-Schaltung muss wiederholte Rufe deshalb als Impulse behandeln. Doppelte native Pakete desselben Rufs werden unterdrückt. Für nachträgliche Änderungen der grundlegenden Netz-/Klingeleinstellungen die Integration mit den korrigierten Werten neu einrichten; die Konfigurieren-Seite enthält derzeit Relais und Audio.

## Tür öffnen

In HA einen langlebigen Zugriffstoken für den verwendeten HA-Benutzer lokal erzeugen und in Loxone hinterlegen. Er ist ein Zugangsdokument zu HA, kein Gateway-Schlüssel. Nicht in GitHub eintragen.

Virtuellen HTTP-Ausgang und dessen Befehl so konfigurieren:

| Feld | Wert |
| --- | --- |
| Adresse des Ausgangs | `http://HA_IP:8123` bzw. deine tatsächlich verwendete HA-Basisadresse |
| HTTP-Methode Ein | `POST` |
| Befehl Ein | `/api/services/button/press` |
| HTTP-Body Ein | `{"entity_id":"DEINE_TUEROEFFNER_ENTITY_ID"}` |
| Header Ein | Siehe unten |
| Befehl Aus | Leer; keinen zweiten Öffnungsbefehl senden |

```text
Authorization: Bearer DEIN_HA_TOKEN
Content-Type: application/json
```

Falls die Loxone-Version ein einzeiliges Headerfeld verwendet, die beiden Header mit dem dort vorgesehenen Zeilentrenner `\r\n` trennen. Den HTTP-Befehl mit der gewünschten Türöffnerfunktion des Türsteuerungsbausteins verbinden und in den Miniserver übertragen. `confirmed` bzw. `transport_acknowledged` bestätigt den Protokollweg, nicht die mechanische Schlossstellung.

## Lokales Gegensprechen

1. HA → **Einstellungen → Geräte & Dienste → Comelit ViP Native → Konfigurieren**.
2. **SIP-Audio aktivieren**, freien Port wählen, empfohlen `5062`.
3. Aktuelle WLAN-IPv4-Adressen der Loxone-App-Geräte eintragen, mit Komma getrennt. **Nicht nur die Miniserver-IP** freigeben: Die App führt das Gespräch.
4. Für mehrere Nutzer alle jeweiligen Geräte aufnehmen. DHCP-Reservierungen verwenden und die WLAN-Adressfunktion der Smartphones berücksichtigen.
5. In Loxone folgende Audiofelder des Türsteuerungs-/Intercom-Bausteins setzen:

| Feld | Eintrag |
| --- | --- |
| Audiohost intern | `HA_IP:5062` (oder dein gewählter Port) |
| Audiobenutzer intern | `comelit` |

Der lokale SIP-Zugang verwendet die IP-Freigabe und keine Provider-Registrierung. Das vorhandene Kamerabild separat beibehalten. Der Hörerknopf nimmt einen klingelnden ViP-Ruf an oder startet bei freier Leitung ein Gespräch zur Außenstation. Ein Gespräch gleichzeitig; ein zweiter Nutzer erhält besetzt. Türöffnen ist eine getrennte Aktion und funktioniert während eines von dieser Brücke geführten Gesprächs.

Externe Audiofelder erst nach [Einrichtung des Gateways](EXTERNES_AUDIO.md) setzen.
