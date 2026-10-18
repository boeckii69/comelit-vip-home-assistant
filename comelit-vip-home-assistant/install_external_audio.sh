#!/bin/sh
# Run inside Studio Code Server / Terminal on the user's HA host.
# Copies code only; does not read tokens, edit .storage, restart HA or place calls.
set -eu
BASE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -d /addons ] && [ -w /addons ]; then
    LOCAL_APPS=/addons
elif [ -d /local_apps ] && [ -w /local_apps ]; then
    LOCAL_APPS=/local_apps
else
    echo "Lokaler Add-on-Ordner nicht erreichbar. In Studio Code Server oder Terminal & SSH ausführen." >&2
    exit 1
fi
if [ ! -d /config ] || [ ! -w /config ]; then
    echo "/config ist nicht erreichbar oder nicht beschreibbar." >&2
    exit 1
fi
test -f "$BASE/custom_components/comelit_native/manifest.json"
test -f "$BASE/addons/comelit_external_audio/Dockerfile"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP="/config/comelit-backups/$STAMP"
mkdir -p "$BACKUP"
if [ -d /config/custom_components/comelit_native ]; then
    cp -a /config/custom_components/comelit_native "$BACKUP/comelit_native"
fi
if [ -d "$LOCAL_APPS/comelit_external_audio" ]; then
    cp -a "$LOCAL_APPS/comelit_external_audio" "$BACKUP/comelit_external_audio"
fi
mkdir -p /config/custom_components/comelit_native "$LOCAL_APPS/comelit_external_audio"
cp -a "$BASE/custom_components/comelit_native/." /config/custom_components/comelit_native/
cp -a "$BASE/addons/comelit_external_audio/." "$LOCAL_APPS/comelit_external_audio/"
echo "Integration 0.2.0b1 und lokales Audio-Add-on kopiert."
echo "Sicherung der bisherigen Dateien: $BACKUP"
echo "Jetzt HA neu starten; danach im Add-on/App-Store nach Aktualisierungen suchen."
