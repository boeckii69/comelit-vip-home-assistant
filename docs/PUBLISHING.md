# Veröffentlichung / Publishing

Dieses Paket ist für **boeckii69/comelit-vip-home-assistant** vorbereitet. Projektlinks und Codeowner sind gesetzt. Das bedeutet noch nicht, dass das Repository bereits auf GitHub angelegt oder veröffentlicht wurde.

## Einmalig finalisieren

Für dieses Paket ist die Identität bereits eingetragen. Die folgenden Befehle dokumentieren die erfolgte Finalisierung und können mit derselben Identität wiederholt werden:

```sh
python3 scripts/finalize_repository.py boeckii69/comelit-vip-home-assistant
python3 scripts/check_project.py
```

Bei einer Organisation zusätzlich `--maintainer GITHUB_BENUTZER` angeben. Das Skript prüft die Form, ersetzt die konkreten Projektlinks und trägt den Codeowner ein. Es kontaktiert GitHub nicht und veröffentlicht nichts. Vorhandene finale Identitäten werden nicht stillschweigend auf einen anderen Eigentümer geändert.

## Repository und erste Beta

1. Öffentliches GitHub-Repository **boeckii69/comelit-vip-home-assistant** anlegen. Beschreibung: `Native Comelit ViP integration for Home Assistant with Loxone doorbell, door release and two-way audio.`
2. Den **Inhalt dieses Projektordners** hochladen, nicht den Ordner als zusätzliche Ebene und nicht das ZIP als einzige Datei. Die Verzeichnisse `custom_components`, `addons`, `docs` und `.github` müssen erhalten bleiben.
3. Topics vorschlagen: `home-assistant`, `hacs`, `comelit`, `vip`, `loxone`, `intercom`, `sip`, `asterisk`.
4. GitHub **Private vulnerability reporting** einschalten. Keine Geheimnisse oder Live-Anlagenbilder hinzufügen.
5. Actions-Ergebnisse prüfen. Der App-Build läuft auf einem GitHub-Runner; hier lokal wurde kein Docker/Supervisor-Build ausgeführt.
6. Installation des Veröffentlichungspakets in HA überprüfen. Bestehende funktionierende Referenzinstallation nur mit Backup aktualisieren.
7. Ein Release `v0.2.0b1` als **Pre-release** anlegen. Notizen aus `CHANGELOG.md` übernehmen und tatsächliche Prüfergebnisse nennen. Keine allgemeine Freigabe aller ViP-Geräte behaupten.

Die Integration kann danach als benutzerdefiniertes HACS-Repository verwendet werden. Die Audio-App verwendet dieselbe Repository-URL im HA-App-Store. Die Aufnahme in den HACS-Standardkatalog ist ein separater Vorgang und keine Voraussetzung für einen Custom-Repository-Link.

Mit `python3 scripts/package_release.py` entsteht ein geprüftes Archiv unter `dist/`. Das Skript verweigert ein Release mit ungeklärter Repository-Identität. Für die lokale Weitergabe des noch unkonfigurierten Entwurfs ist `--allow-unconfigured` möglich.

## English

This package is already finalized for `boeckii69/comelit-vip-home-assistant`; no remote repository is created by finalization. Run the checks, create/upload the repository, enable private vulnerability reporting, review CI/build results and perform a real HA installation check. Then create an explicit beta prerelease. HACS custom-repository installation and Supervisor app-repository installation are separate mechanisms. No workflow automatically publishes a release.
