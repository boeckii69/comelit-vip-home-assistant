"""Direct LAN SIP user agent for explicit Loxone answer/start actions.

No SIP registration/proxy, Internet routing, door commands, or autonomous
microphone capture. An allowed Loxone app sends INVITE to answer or start a call.
"""
from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
import ipaddress
import re
import secrets
import time

from .call import CallError, CallRefused
from .sip_media import SipMedia
from .remote_gate import RemoteGate
from .sip_wire import SipError, parameter, parse_sdp, parse_sip, response, sdp_answer, uri

ACK_WAIT = 32.0
T1 = 0.5


def allowed_addresses(value):
    if not isinstance(value, str):
        raise ValueError("SIP-Geräte müssen als IPv4-Adressen angegeben werden")
    result = set()
    for item in value.replace(";", ",").split(","):
        if not item.strip():
            continue
        ip = ipaddress.IPv4Address(item.strip())
        if ip.is_unspecified or ip.is_multicast or int(ip) == 0xffffffff:
            raise ValueError("Nur einzelne Unicast-IPv4-Adressen für SIP zulässig")
        result.add(str(ip))
    if len(result) > 16:
        raise ValueError("Maximal 16 SIP-Geräte")
    return frozenset(result)


@dataclass(eq=False)
class Session:
    invite: object
    peer: tuple
    offer: object
    remote_target: str
    generation: int
    outgoing: bool = False
    external: bool = False
    tag: str = field(default_factory=lambda: secrets.token_hex(12))
    ack_event: asyncio.Event = field(default_factory=asyncio.Event)
    closing: bool = False
    acked: bool = False
    answered: bool = False
    media: object = None
    setup_task: object = None
    ack_task: object = None
    close_task: object = None
    last_cseq: int = 0


class SipBridge(asyncio.DatagramProtocol):
    def __init__(self, client, on_status):
        self.client, self.on_status = client, on_status
        self.transport = None
        self.session = None
        self.state = "disabled"
        self.error = ""
        self.local_port = 0
        self.allowed_ips = frozenset()
        self.tasks = set()
        self.cache = {}
        self.rate = {}
        self.bye_waiters = {}
        self.received = self.rejected = self.invites = 0
        self.last_peer = self.last_method = self.last_response = None
        self.last_rejected_peer = None
        self.last_media = {}
        self.last_call_mode = None
        self.stopping = False
        self.remote_gate = RemoteGate()
        self.remote_gate_timer = None
        self.transport_closed = None

    def grant_remote_audio(self):
        if self.remote_gate_timer:
            self.remote_gate_timer.cancel()
        self.remote_gate.grant()
        self.remote_gate_timer = asyncio.get_running_loop().call_later(30, self.on_status)
        self.on_status()

    def changed(self, state=None, error=None):
        if state is not None:
            self.state = state
        if error is not None:
            self.error = error
        self.on_status()

    def spawn(self, coroutine, name):
        task = asyncio.create_task(coroutine, name=name)
        self.tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task):
        self.tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            self.changed("error", f"SIP: {task.exception()}")

    async def start(self):
        self.stopping = False
        cfg = self.client.settings
        self.allowed_ips = allowed_addresses(cfg.sip_allowed_ips)
        if not cfg.sip_enabled:
            self.changed("disabled", "")
            return
        if not self.allowed_ips:
            self.changed("error", "Mindestens eine Geräte-IP für die Loxone-App freigeben")
            return
        try:
            await asyncio.get_running_loop().create_datagram_endpoint(
                lambda: self, local_addr=(cfg.local_ip, cfg.sip_port))
        except OSError as exc:
            self.changed("error", f"SIP-Port nicht verfügbar: {exc}")
        else:
            self.changed("listening", "")

    def connection_made(self, transport):
        self.transport = transport
        self.transport_closed = asyncio.get_running_loop().create_future()
        self.local_port = transport.get_extra_info("sockname")[1]

    def connection_lost(self, exc):
        if self.transport_closed is not None and not self.transport_closed.done():
            self.transport_closed.set_result(None)

    def error_received(self, exc):
        self.changed(error=f"SIP-UDP: {exc}")

    @property
    def contact(self):
        return f"sip:comelit@{self.client.settings.local_ip}:{self.local_port}"

    def _send(self, data, peer):
        if self.transport:
            self.transport.sendto(data, peer)

    def reply(self, msg, code, peer, *, tag="", body=b"", extra=()):
        data = response(msg, code, peer, tag, body, self.contact if code < 300 else "", extra)
        key = (peer, msg.key)
        if len(self.cache) >= 128 and key not in self.cache:
            self.cache.pop(next(iter(self.cache)))
        self.cache[key] = (time.monotonic() + 64, data)
        self._send(data, peer)
        self.last_response = code
        return data

    def _matches_dialog(self, msg, peer, session):
        return (session is not None and peer == session.peer
                and msg.get("call-id") == session.invite.get("call-id")
                and parameter(msg.get("from"), "tag") == parameter(session.invite.get("from"), "tag")
                and parameter(msg.get("to"), "tag") == session.tag)

    def datagram_received(self, data, peer):
        if self.stopping or self.transport is None:
            return
        gateway = bool(self.client.settings.remote_gateway_key) and peer[0] == self.client.settings.local_ip
        if not gateway and peer[0] not in self.allowed_ips:
            self.rejected += 1
            if self.last_rejected_peer != peer[0]:
                self.last_rejected_peer = peer[0]
                self.on_status()
            return
        now = time.monotonic()
        count, since = self.rate.get(peer[0], (0, now))
        count = count + 1 if now - since < 1 else 1
        since = since if now - since < 1 else now
        self.rate[peer[0]] = count, since
        if count > 40:
            return
        if data in (b"\r\n", b"\r\n\r\n"):
            return
        self.received += 1
        try:
            msg = parse_sip(data)
        except (ValueError, UnicodeError):
            self.rejected += 1
            return
        self.last_peer = f"{peer[0]}:{peer[1]}"
        self.last_method = msg.method or f"response_{msg.status}"
        if msg.status:
            self._response(msg, peer)
            return
        self.cache = {key: value for key, value in self.cache.items() if value[0] > now}
        cached = self.cache.get((peer, msg.key))
        if cached and msg.method != "ACK":
            self._send(cached[1], peer)
            return
        try:
            self._request(msg, peer)
        except (SipError, ValueError, UnicodeError) as exc:
            code = exc.code if isinstance(exc, SipError) else 400
            self.reply(msg, code, peer, tag=secrets.token_hex(12))
            self.error = str(exc)
        self.on_status()

    def _request(self, msg, peer):
        if msg.method == "OPTIONS":
            self.reply(msg, 200, peer, tag=secrets.token_hex(12))
            return
        if msg.method == "INVITE":
            self._invite(msg, peer)
            return
        session = self.session
        if msg.method == "CANCEL":
            matches = (session is not None and peer == session.peer
                       and msg.get("call-id") == session.invite.get("call-id")
                       and msg.cseq == session.invite.cseq and msg.branch == session.invite.branch
                       and msg.get("from") == session.invite.get("from"))
            self.reply(msg, 200 if matches else 481, peer, tag=session.tag if matches else "")
            if matches and not session.answered:
                self.reply(session.invite, 487, peer, tag=session.tag)
                self._schedule_close(session, "Loxone hat vor der Annahme abgebrochen", send_bye=False)
            return
        if msg.method in ("ACK", "BYE"):
            if not self._matches_dialog(msg, peer, session):
                if msg.method == "BYE":
                    self.reply(msg, 481, peer)
                return
            if msg.method == "ACK":
                if session.answered and msg.cseq == session.invite.cseq and not session.closing:
                    session.acked = True
                    session.ack_event.set()
                    session.media.start()
                    self.changed("connected", "")
            elif msg.cseq > session.last_cseq:
                session.last_cseq = msg.cseq
                self.reply(msg, 200, peer, tag=session.tag)
                self._schedule_close(session, "In Loxone aufgelegt", send_bye=False)
            else:
                self.reply(msg, 500, peer, tag=session.tag)
            return
        self.reply(msg, 405, peer, tag=secrets.token_hex(12))

    def _invite(self, msg, peer):
        self.invites += 1
        external = (bool(self.client.settings.remote_gateway_key)
                    and peer[0] == self.client.settings.local_ip)
        if external and not RemoteGate.authenticate(msg, self.client.settings.remote_gateway_key):
            raise SipError("Gateway-Schlüssel fehlt oder ist falsch", 403)
        if not external and msg.get("x-comelit-gateway"):
            raise SipError("Externes Gateway muss auf dem HA-Host laufen", 403)
        if not parameter(msg.get("from"), "tag"):
            raise SipError("From-Tag fehlt")
        if msg.get("require"):
            self.reply(msg, 420, peer, tag=secrets.token_hex(12),
                       extra=("Unsupported: " + msg.get("require"),))
            self.error = "Erforderliche SIP-Erweiterung wird nicht unterstützt"
            return
        if msg.get("record-route") or msg.get("route"):
            raise SipError("Nur direkte LAN-Verbindungen ohne SIP-Proxy unterstützt", 488)
        target = re.fullmatch(r"sip:comelit@([^;:]+)(?::([0-9]+))?(?:;transport=udp)?", msg.target, re.I)
        if (not target or target[1] != self.client.settings.local_ip
                or int(target[2] or 5060) != self.local_port):
            raise SipError("SIP-Ziel muss comelit an der HA-IP sein", 404)
        if self.session is not None:
            if self._matches_dialog(msg, peer, self.session):
                raise SipError("Neu-Aushandlung eines laufenden Rufes nicht unterstützt", 488)
            same_call = msg.get("call-id") == self.session.invite.get("call-id")
            self.reply(msg, 482 if same_call else 486, peer, tag=secrets.token_hex(12))
            return
        if parameter(msg.get("to"), "tag"):
            raise SipError("Kein vorhandener SIP-Dialog", 481)
        call = self.client.call
        outgoing = not call.can_answer
        if outgoing and not call.can_start:
            self.reply(msg, 486 if self.client.call.owned else 480, peer, tag=secrets.token_hex(12))
            self.error = "Comelit ist nicht bereit oder bereits belegt"
            return
        if msg.get("content-type").split(";", 1)[0].lower() != "application/sdp":
            raise SipError("INVITE benötigt ein SDP-Audioangebot", 415)
        offer = parse_sdp(msg.body, peer[0])
        remote_target = uri(msg.get("contact"))
        # No request routing to a third-party Contact address.
        remote_host = re.fullmatch(r"sip:(?:[^@;]+@)?([^;:]+)(?::([0-9]+))?(?:;transport=udp)?", remote_target, re.I)
        if (not remote_host or remote_host[1] != peer[0]
                or not 1 <= int(remote_host[2] or 5060) <= 65535):
            raise SipError("Contact muss zur anrufenden LAN-IP gehören", 488)
        # Consume only after complete SIP/SDP validation, before native activity.
        # Retransmissions are served by the existing transaction cache.
        if external and not self.client.settings.remote_auto_enabled and not self.remote_gate.consume():
            raise SipError("Zuerst Audio extern in Loxone freigeben (30 Sekunden)", 403)
        session = Session(msg, peer, offer, remote_target, self.client.call.generation,
                          outgoing=outgoing, external=external, last_cseq=msg.cseq)
        self.session = session
        self.last_media = {}
        self.last_call_mode = "outgoing" if outgoing else "incoming"
        self.reply(msg, 100, peer)
        self.reply(msg, 180, peer, tag=session.tag)
        self.changed("dialing" if outgoing else "answering", "")
        session.setup_task = self.spawn(self._answer(session), "comelit_sip_answer")

    async def _answer(self, session):
        try:
            session.media = SipMedia(session, self.client.call, lambda err: self.changed(error=err))
            await session.media.open(self.client.settings.local_ip)
            if session.closing or self.client.call.generation != session.generation:
                raise CallError("Rufzustand hat sich während des Verbindungsaufbaus geändert")
            if session.outgoing:
                await self.client.call.start(controller=session,
                    on_started=lambda generation: setattr(session, "generation", generation))
            else:
                await self.client.call.answer(controller=session)
            if session.closing or not self.client.call.owned:
                raise CallError("Ruf während der Annahme beendet")
            body = sdp_answer(session.offer, self.client.settings.local_ip,
                              session.media.port, session.media.rtcp_port)
            session.answered = True
            wire = self.reply(session.invite, 200, session.peer, tag=session.tag, body=body)
            self.changed("waiting_ack", "")
            session.ack_task = self.spawn(self._wait_ack(session, wire), "comelit_sip_ack")
        except asyncio.CancelledError:
            raise
        except (OSError, ValueError, CallError) as exc:
            if not session.closing:
                self.reply(session.invite, 486 if isinstance(exc, CallRefused) and exc.cause == 8 else 480,
                           session.peer, tag=session.tag)
                self.changed("error", str(exc))
                self._schedule_close(session, str(exc), send_bye=False)
        finally:
            session.setup_task = None

    async def _wait_ack(self, session, wire):
        deadline, interval = time.monotonic() + ACK_WAIT, T1
        while not session.closing and not session.acked:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._schedule_close(session, "Loxone bestätigt die SIP-Annahme nicht", send_bye=True)
                return
            try:
                await asyncio.wait_for(session.ack_event.wait(), min(interval, remaining))
                return
            except TimeoutError:
                if not session.closing:
                    self._send(wire, session.peer)
                interval = min(interval * 2, 4)

    def native_changed(self):
        session = self.session
        if session is None or session.closing:
            return
        call = self.client.call
        if (call.generation != session.generation
                or call.controller is session and not call.owned and session.setup_task is None):
            self._schedule_close(session, call.end_reason or "Comelit-Ruf beendet",
                                 send_bye=session.answered)

    def _schedule_close(self, session, reason, send_bye):
        if session.closing:
            return
        session.closing = True
        if session.media:
            session.media.close()
        session.close_task = self.spawn(self._close(session, reason, send_bye), "comelit_sip_close")

    async def _close(self, session, reason, send_bye):
        current = asyncio.current_task()
        for task in (session.setup_task, session.ack_task):
            if task and task is not current:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, CallError):
                    await task
        # A socket can finish opening while setup is being cancelled.
        if session.media:
            session.media.close()
            self.last_media = self._media_stats(session)
        if not session.answered and self.cache.get((session.peer, session.invite.key), (0, b""))[1].startswith(b"SIP/2.0 1"):
            self.reply(session.invite, 487, session.peer, tag=session.tag)
        call = self.client.call
        if call.owned and call.controller is session:
            with contextlib.suppress(CallError):
                await call.hangup(controller=session)
        if send_bye and session.answered:
            self.spawn(self._send_bye(session), "comelit_sip_bye")
        if self.session is session:
            self.session = None
            self.changed("listening" if self.transport and not self.stopping else "disabled", reason)

    async def _send_bye(self, session):
        branch = "z9hG4bK" + secrets.token_hex(12)
        number = secrets.randbelow(2**30) + 1
        msg = session.invite
        local_from = msg.get("to") + ";tag=" + session.tag
        lines = [f"BYE {session.remote_target} SIP/2.0",
                 f"Via: SIP/2.0/UDP {self.client.settings.local_ip}:{self.local_port};branch={branch};rport",
                 "Max-Forwards: 70", "From: " + local_from, "To: " + msg.get("from"),
                 "Call-ID: " + msg.get("call-id"), f"CSeq: {number} BYE", "Content-Length: 0"]
        wire = ("\r\n".join(lines) + "\r\n\r\n").encode()
        key = (session.peer, msg.get("call-id"), number, branch)
        future = asyncio.get_running_loop().create_future()
        self.bye_waiters[key] = future
        try:
            for interval in (T1, T1 * 2, T1 * 4):
                self._send(wire, session.peer)
                try:
                    await asyncio.wait_for(asyncio.shield(future), interval)
                    return
                except TimeoutError:
                    continue
        finally:
            self.bye_waiters.pop(key, None)
            if not future.done():
                future.cancel()

    def _response(self, msg, peer):
        if msg.cseq_method != "BYE" or msg.status < 200:
            return
        pending = self.bye_waiters.get((peer, msg.get("call-id"), msg.cseq, msg.branch))
        if pending is not None and not pending.done():
            pending.set_result(msg.status)

    async def stop(self):
        self.stopping = True
        self.remote_gate.revoke()
        if self.remote_gate_timer:
            self.remote_gate_timer.cancel()
            self.remote_gate_timer = None
        if self.session:
            self._schedule_close(self.session, "SIP-Brücke gestoppt", send_bye=True)
            if self.session and self.session.close_task:
                await self.session.close_task
        # Let a shutdown BYE leave the socket, then cancel its bounded retries.
        await asyncio.sleep(0)
        for task in tuple(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        if self.transport:
            self.transport.close()
            # asyncio closes the UDP socket in its scheduled connection_lost
            # callback. Wait for it before an options update binds the same port.
            if self.transport_closed is not None:
                await asyncio.shield(self.transport_closed)
            self.transport = None
        self.cache.clear()
        self.rate.clear()
        self.changed("disabled", "")

    def _media_stats(self, session):
        media = session.media
        if media is None:
            return {}
        return {"sip_codec": "PCMA" if session.offer.pt == 8 else "PCMU",
                "sip_rtp_port": media.port, "sip_rtcp_port": media.rtcp_port,
                "sip_rtp_destination": f"{session.offer.ip}:{session.offer.port}",
                "sip_microphone_peer": f"{media.peer[0]}:{media.peer[1]}" if media.peer else None,
                "sip_audio_sent": media.writer.packets,
                "sip_microphone_received": media.packets,
                "sip_microphone_lost": media.lost,
                "sip_microphone_rejected": media.rejected,
                "sip_audio_dropped_bytes": media.dropped_bytes}

    def diagnostics(self):
        session = self.session
        return {"sip_error": self.error,
                "remote_gateway_enabled": bool(self.client.settings.remote_gateway_key),
                "remote_audio_mode": "automatic" if self.client.settings.remote_auto_enabled else "manual",
                "sip_external_call": session.external if session else False,
                **self.remote_gate.diagnostics(),
                "sip_call_mode": self.last_call_mode,
                "sip_endpoint": f"{self.client.settings.local_ip}:{self.local_port}" if self.transport else None,
                "sip_user": "comelit", "sip_allowed_ips": sorted(self.allowed_ips),
                "sip_received": self.received, "sip_rejected": self.rejected,
                "sip_invites": self.invites, "sip_last_peer": self.last_peer,
                "sip_last_method": self.last_method, "sip_last_response": self.last_response,
                "sip_last_rejected_ip": self.last_rejected_peer,
                "sip_acknowledged": session.acked if session else False,
                **(self._media_stats(session) if session else self.last_media)}
