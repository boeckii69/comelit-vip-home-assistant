"""Authenticated, permission-checked live audio and explicit call controls."""
from __future__ import annotations

import base64
from pathlib import Path

from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.components import websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .protocol.call import CallError

CARD_URL = "/comelit_native/comelit-call-card.js"


def _call(hass, connection, msg):
    """Resolve only this integration's call sensor, with control permission."""
    entity_id = msg.get("entity_id")
    if not isinstance(entity_id, str):
        raise CallError("Rufstatus-Entität fehlt")
    user = connection.user
    if user is None or not user.is_active or not user.permissions.check_entity(entity_id, POLICY_CONTROL):
        raise PermissionError("Keine Berechtigung für diese Rufstatus-Entität")
    entity = er.async_get(hass).async_get(entity_id)
    if entity is None or entity.platform != DOMAIN or not entity.config_entry_id:
        raise CallError("Keine Comelit-Native-Rufstatus-Entität")
    entry = hass.config_entries.async_get_entry(entity.config_entry_id)
    if (entry is None or entry.domain != DOMAIN
            or entity.unique_id != f"{entry.entry_id}_call_state"):
        raise CallError("Keine Comelit-Native-Rufstatus-Entität")
    hub = getattr(entry, "runtime_data", None)
    if hub is None or not hub.client.running:
        raise CallError("Comelit Native ist nicht gestartet")
    return hub.client.call


@websocket_api.websocket_command({"type": "comelit_native/call", "entity_id": str, "action": str})
@websocket_api.async_response
async def ws_call(hass, connection, msg):
    try:
        call = _call(hass, connection, msg)
        action = msg.get("action")
        if action == "answer":
            await call.answer()
        elif action == "hangup":
            await call.hangup()
        else:
            raise CallError("Unbekannte Rufaktion")
    except PermissionError as exc:
        connection.send_error(msg["id"], "unauthorized", str(exc))
    except CallError as exc:
        connection.send_error(msg["id"], "call_failed", str(exc))
    else:
        connection.send_result(msg["id"], {"state": call.state})


@websocket_api.websocket_command({"type": "comelit_native/audio/subscribe", "entity_id": str})
@callback
def ws_audio(hass, connection, msg):
    try:
        call = _call(hass, connection, msg)

        @callback
        def forward(pcm):
            # Recheck permission for every batch, including after a role change.
            user = connection.user
            if (user is not None and user.is_active
                    and user.permissions.check_entity(msg["entity_id"], POLICY_CONTROL)):
                connection.send_event(msg["id"], {
                    "type": "pcm", "sample_rate": 8000,
                    "data": base64.b64encode(pcm).decode("ascii"),
                })

        unsubscribe = call.subscribe_audio(forward)
    except PermissionError as exc:
        connection.send_error(msg["id"], "unauthorized", str(exc))
    except CallError as exc:
        connection.send_error(msg["id"], "call_failed", str(exc))
    else:
        # HA calls this on unsubscribe and when the authenticated socket closes.
        connection.subscriptions[msg["id"]] = unsubscribe
        connection.send_result(msg["id"])


async def async_setup_api(hass):
    data = hass.data.setdefault(DOMAIN, {})
    if data.get("call_api_registered"):
        return
    await hass.http.async_register_static_paths([
        StaticPathConfig(CARD_URL, str(Path(__file__).parent / "www" / "comelit-call-card.js"), False)
    ])
    websocket_api.async_register_command(hass, ws_call)
    websocket_api.async_register_command(hass, ws_audio)
    data["call_api_registered"] = True
