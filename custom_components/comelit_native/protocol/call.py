"""Explicit native incoming/outgoing calls, audio and in-call door commands.

Passive ring observation never sends CONNECT, media requests, or RELEASE.
Only answer() starts negotiation on that ring's existing CTP connection.
Outgoing calls require an explicit action and resolve only the configured panel.
No guessed peer, recording, background activation or auto-answer on ring.
"""
from __future__ import annotations

import asyncio
import contextlib
import secrets
import time

from .audio import AudioReceiver
from .ctp import build_ctp_packet
from .messages import audio_media_request, open_door_request, outgoing_call_request
from .talk import RtpWriter, encode_g711, media_destination

RING_LIFETIME = 90.0
MAX_CALL_DURATION = 120.0
AUDIO_START_TIMEOUT = 15.0
AUDIO_SILENCE_TIMEOUT = 10.0
ACK_TIMEOUT = 0.7
ACK_ATTEMPTS = 3
DOOR_ACK_TIMEOUT = 2.1
OUTGOING_SETUP_TIMEOUT = 15.0


class CallError(Exception):
    """A call action failed, independently of registration/door release."""


class CallRefused(CallError):
    def __init__(self, cause):
        self.cause = cause
        super().__init__(f"Außenstation lehnt den aktiven Ruf ab: Code {cause}"
                         + (" (besetzt)" if cause == 8 else ""))


class IncomingCall:
    def __init__(self, owner):
        self.owner = owner
        self.state = "idle"
        self.direction = None
        self.requested_at = None
        self.start_sent = self.start_acknowledged = self.peer_connected = False
        self._capabilities_received = asyncio.Event()
        self._connect_received = asyncio.Event()
        self.error = ""
        self.conn = None
        self.call_id = None
        self.origin = None
        self.ring_at = None
        self.answered_at = None
        self.first_audio_at = None
        self.ended_at = None
        self.end_reason = None
        self.release_cause = None
        self.capabilities = None
        self.last_media_request = None
        self.control_tx = 0
        self.control_acked = 0
        self.connect_sent = False
        self.connect_acked = False
        self.media_sent = False
        self.owned = False
        self.generation = 0
        self.audio = None
        self.audio_listeners = set()
        self._pending = None
        self._pending_ack = None
        self._answer_task = None
        self._watch_task = None
        self._ring_timer = None
        self._lock = asyncio.Lock()
        self._control_lock = asyncio.Lock()
        self._release_lock = asyncio.Lock()
        self.controller = None
        self.tx_endpoint = None
        self.tx_error = ""
        self.tx_writer = RtpWriter()

    @property
    def can_answer(self):
        return (self.state == "ringing" and self.owner.registered
                and self.conn is not None and not self.conn.closing
                and self.ring_at is not None and time.time() - self.ring_at < RING_LIFETIME)

    @property
    def can_start(self):
        return (self.owner.running and self.owner.registered and self.owner.ctp is not None
                and not self.owned and not self._lock.locked()
                and not self.owner.door_lock.locked()
                and self.state in ("idle", "ended", "missed", "failed"))

    def changed(self, state=None, error=None):
        if state is not None:
            self.state = state
        if error is not None:
            self.error = error
        self.owner.on_status()

    def subscribe_audio(self, listener):
        if len(self.audio_listeners) >= 4:
            raise CallError("Maximal vier Audio-Zuhörer gleichzeitig")
        self.audio_listeners.add(listener)
        return lambda: self.audio_listeners.discard(listener)

    def observe_invite(self, conn, origin, call_id):
        if self.call_id == call_id and self.origin == origin:
            return
        # A second call must not steal an explicitly answered conversation.
        if self.owned:
            return
        self._reset(conn, origin, call_id)
        self.direction = "incoming"
        self.ring_at = time.time()
        self.changed("ringing", "")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # offline codec/packet checks need no live loop
            return
        self._ring_timer = loop.call_later(RING_LIFETIME, self._expire_ring, self.generation)

    def _reset(self, conn, origin, call_id):
        self._cancel_ring_timer()
        if self.audio is not None:
            self.audio.close()
        self.generation += 1
        self.conn, self.origin, self.call_id = conn, origin, call_id
        self.ring_at = None
        self.requested_at = None
        self.start_sent = self.start_acknowledged = self.peer_connected = False
        self._capabilities_received.clear()
        self._connect_received.clear()
        self.answered_at = self.first_audio_at = self.ended_at = None
        self.end_reason = self.release_cause = self.capabilities = None
        self.last_media_request = None
        self.audio = None
        self.controller = None
        self.tx_endpoint = None
        self.tx_error = ""
        self.tx_writer = RtpWriter()
        self.control_tx = self.control_acked = 0
        self.connect_sent = self.connect_acked = self.media_sent = False

    def _expire_ring(self, generation):
        self._ring_timer = None
        if generation == self.generation and not self.owned:
            self.finish("missed", "Rufzeit abgelaufen")

    def _cancel_ring_timer(self):
        if self._ring_timer is not None:
            self._ring_timer.cancel()
            self._ring_timer = None

    def receive(self, conn, packet, fresh):
        if conn is not self.conn:
            return
        if (self._pending is not None and not self._pending.done()
                and packet.acknowledgement == self._pending_ack):
            self._pending.set_result(None)
            self.control_acked += 1
        if fresh and packet.opcode == 3 and len(packet.body) == 8:
            self.capabilities = int.from_bytes(packet.body[4:8], "little")
            self._capabilities_received.set()
        if fresh and packet.opcode == 0x0c and len(packet.body) == 2:
            self.peer_connected = True
            self._connect_received.set()
        if fresh and packet.opcode in (0x11, 0x12):
            # Keep signalling metadata only; no audio is logged or stored.
            self.last_media_request = packet.body[:26].hex()
        if (fresh and packet.opcode == 0x11 and len(packet.body) >= 3
                and packet.body[2] in (0x18, 0x98)):
            try:
                self.tx_endpoint = media_destination(packet.body,
                    (self.owner.settings.master_ip, self.owner.settings.entrance_ip))
                self.tx_error = ""
            except ValueError as exc:
                self.tx_endpoint = None
                self.tx_error = str(exc)
        if fresh and packet.opcode == 0x0e and len(packet.body) >= 3:
            self.release_cause = packet.body[2]
            self.finish("ended", f"Gegenstelle beendet den Ruf (Code {packet.body[2]})")
        elif packet.is_fin:
            self.finish("ended", "Gegenstelle hat die Rufverbindung geschlossen")

    def _check_live(self, generation):
        if (generation != self.generation or not self.owned
                or not self.owner.running or self.owner.ctp is None
                or self.conn is None or self.conn.closing
                or self.owner.connections.get(self.conn.local_id) is not self.conn):
            raise CallError("Ruf ist nicht mehr aktiv")

    async def _send_reliable(self, body, generation, *, flags=0x40):
        async with self._control_lock:
            await self._send_control_locked(body, generation, flags=flags)

    async def open_door(self, relay, on_sent):
        """One addressed 0x002D on our answered call, without releasing it.

        A matching CTP ACK only proves transport receipt. Do not wait for
        RELEASE here: that message terminates the ongoing conversation.
        """
        generation = self.generation
        async with self._control_lock:
            self._check_live(generation)
            if (not self.owner.registered
                    or self.state not in ("waiting_audio", "audio_receiving")
                    or not self.connect_acked
                    or self.origin != self.owner.settings.entrance_address):
                raise CallError("Türöffnen benötigt ein aktives Gespräch mit der konfigurierten Außenstation")
            body = open_door_request(self.owner.settings.entrance_address, relay)
            await self._send_control_locked(body, generation, attempts=1,
                                           timeout=DOOR_ACK_TIMEOUT, on_sent=on_sent)

    async def _send_control_locked(self, body, generation, *, attempts=ACK_ATTEMPTS,
                                   timeout=None, on_sent=None, flags=0x40):
        self._check_live(generation)
        if timeout is None:
            timeout = ACK_TIMEOUT
        conn = self.conn
        sequence = conn.sequence
        conn.sequence = (sequence + 1) & 255
        pending = asyncio.get_running_loop().create_future()
        self._pending, self._pending_ack = pending, conn.sequence
        try:
            for _ in range(attempts):
                self._check_live(generation)
                payload = build_ctp_packet(flags=flags, connection=conn.local_id,
                    sequence=sequence, acknowledgement=conn.expected, body=body,
                    source=self.owner.settings.source_address, destination=conn.remote_address)
                self.owner.ctp.sendto(payload, conn.peer)
                self.control_tx += 1
                if on_sent is not None:
                    on_sent()
                try:
                    await asyncio.wait_for(asyncio.shield(pending), timeout)
                    self._check_live(generation)
                    return
                except TimeoutError:
                    continue
            raise CallError(f"Keine CTP-Bestätigung für Rufbefehl 0x{body[:2].hex()}")
        finally:
            if not pending.done():
                pending.cancel()
            if self._pending is pending:
                self._pending = self._pending_ack = None

    async def start(self, controller, on_started):
        """Explicit Loxone action: establish a call to the configured entrance.

        START40 and capabilities establish the session; the peer must accept.
        CONNECT then enables the speech phase, followed by the native audio
        request. A busy/refused panel is never forced or retried on another path.
        """
        if controller is None or not self.can_start:
            raise CallError("Kein freier Anschluss für einen aktiven Ruf")
        async with self._lock:
            self._reset(None, self.owner.settings.entrance_address, secrets.token_bytes(4))
            self.direction = "outgoing"
            self.requested_at = time.time()
            self.owned, self.controller = True, controller
            generation = self.generation
            self._answer_task = asyncio.current_task()
            on_started(generation)
            self.changed("resolving", "")
            try:
                async with asyncio.timeout(OUTGOING_SETUP_TIMEOUT):
                    await self.owner.resolve_entrance()
                    if generation != self.generation or not self.owned:
                        raise CallError("Aktiver Ruf wurde während der Auflösung beendet")
                    self.conn = self.owner.new_call_connection(self.call_id)
                    await self._open_audio()
                    self.changed("dialing")
                    self.start_sent = True
                    await self._send_reliable(outgoing_call_request(
                        self.owner.settings.source_address, self.origin, self.call_id),
                        generation, flags=0xc0)
                    self.start_acknowledged = True
                    await self._send_reliable(bytes.fromhex("0003490003000000"), generation)
                    await self._capabilities_received.wait()
                    self._check_live(generation)
                    if self.capabilities is None or self.capabilities & 3 != 3:
                        raise CallError("Außenstation meldet keine bidirektionale Audio-Fähigkeit")
                    await self._connect_received.wait()
                    self._check_live(generation)
                    self.connect_sent = True
                    await self._send_reliable(b"\x00\x0c", generation)
                    self.connect_acked = True
                    self.answered_at = time.time()
                    self.media_sent = True
                    self.audio.active = True
                    await self._send_reliable(audio_media_request(
                        self.owner.settings.local_ip, self.audio.port), generation)
                self.changed("audio_receiving" if self.first_audio_at is not None else "waiting_audio")
                self._watch_task = asyncio.create_task(self._watch(generation),
                                                       name="comelit_native_audio_watch")
            except asyncio.CancelledError:
                if self.owned and self.generation == generation:
                    await self._release("Aktiver Ruf abgebrochen")
                raise
            except (OSError, ValueError, CallError, TimeoutError) as exc:
                cause = self.release_cause
                error = (CallRefused(cause) if cause is not None else
                         CallError("Zeitüberschreitung beim aktiven Rufaufbau" if isinstance(exc, TimeoutError)
                                   else str(exc)))
                if self.generation == generation:
                    if self.owned:
                        await self._release("Aktiver Rufaufbau fehlgeschlagen")
                    self.changed("failed", str(error))
                raise error from exc
            finally:
                self._answer_task = None

    async def _open_audio(self):
        cfg = self.owner.settings
        generation = self.generation
        receiver = AudioReceiver((cfg.master_ip, cfg.entrance_ip),
            self._on_pcm, self._on_first_audio, self._on_audio_error)
        receiver.active = False
        try:
            await asyncio.get_running_loop().create_datagram_endpoint(
                lambda: receiver, local_addr=(cfg.local_ip, 0))
        except BaseException:
            receiver.close()
            raise
        if not self.owned or generation != self.generation:
            receiver.close()
            raise CallError("Ruf während des Audio-Starts beendet")
        self.audio = receiver

    async def answer(self, controller=None):
        if self._lock.locked():
            raise CallError("Eine Rufaktion läuft bereits")
        async with self._lock:
            if not self.can_answer:
                raise CallError("Kein aktiver Klingelruf zum Annehmen vorhanden")
            if self.capabilities is not None and self.capabilities & 3 != 3:
                raise CallError("Gegenstelle meldet keine bidirektionale Audio-Fähigkeit")
            self._answer_task = asyncio.current_task()
            self.owned = True
            self.controller = controller
            generation = self.generation
            self._cancel_ring_timer()
            self.changed("answering", "")
            try:
                await self._open_audio()
                self._check_live(generation)
                # Original native client: alerting, capabilities, CONNECT,
                # then audio MEDIAREQ. Advertise only audio RX/TX (bits 0/1),
                # not video, since video continues on the existing camera path.
                await self._send_reliable(b"\x00\x0a\x00", generation)
                await self._send_reliable(bytes.fromhex("0003490003000000"), generation)
                self.connect_sent = True
                await self._send_reliable(b"\x00\x0c", generation)
                self.connect_acked = True  # transport ACK, not proof of audible media
                self.answered_at = time.time()
                self.media_sent = True
                self.audio.active = True
                await self._send_reliable(audio_media_request(
                    self.owner.settings.local_ip, self.audio.port), generation)
                if self.first_audio_at is None:
                    self.changed("waiting_audio")
                else:
                    self.changed("audio_receiving")
                self._watch_task = asyncio.create_task(self._watch(generation),
                                                       name="comelit_native_audio_watch")
            except asyncio.CancelledError:
                if self.owned:
                    await self._release("cancelled")
                raise
            except (OSError, ValueError, CallError) as exc:
                if self.owned:
                    await self._release("answer_failed")
                    self.changed("failed", str(exc))
                raise CallError(str(exc)) from exc
            finally:
                self._answer_task = None

    def _on_first_audio(self):
        if self.owned and self.media_sent:
            self.first_audio_at = time.time()
            self.changed("audio_receiving")

    def _on_pcm(self, pcm):
        if self.owned and self.media_sent:
            for listener in tuple(self.audio_listeners):
                listener(pcm)

    def send_microphone(self, pcm, controller):
        """Only the active SIP dialog may send a single 20-ms microphone frame."""
        if (controller is None or controller is not self.controller or not self.owned
                or self.state not in ("waiting_audio", "audio_receiving")
                or not self.tx_endpoint or not self.audio or not self.audio.active
                or not self.audio.transport or self.audio.payload_type not in (0, 8)):
            return False
        if len(pcm) != 320:
            raise ValueError("Mikrofonframe muss 20 ms bei 8 kHz enthalten")
        pt = self.audio.payload_type
        try:
            self.audio.transport.sendto(self.tx_writer.packet(encode_g711(pcm, pt), pt),
                                        self.tx_endpoint)
        except OSError as exc:
            self.tx_error = str(exc)
            return False
        return True

    def _on_audio_error(self, error):
        if self.owned:
            self.changed(error=f"Audio-Empfang: {error}")

    async def _watch(self, generation):
        started = time.monotonic()
        try:
            while self.owned and generation == self.generation:
                await asyncio.sleep(1)
                if not self.owned or generation != self.generation:
                    return
                self.owner.on_status()
                now = time.monotonic()
                reason = None
                if now - started >= MAX_CALL_DURATION:
                    reason = "Gespräch nach zwei Minuten beendet"
                elif self.first_audio_at is None and now - started >= AUDIO_START_TIMEOUT:
                    reason = "Keine G.711-Audiopakete empfangen"
                elif (self.audio is not None and self.audio.last_packet is not None
                      and now - self.audio.last_packet >= AUDIO_SILENCE_TIMEOUT):
                    reason = "Audioübertragung abgebrochen"
                elif not self.owner.registered:
                    reason = "Registrierung nicht mehr aktiv"
                if reason:
                    await self._release(reason)
                    self.changed("failed" if self.first_audio_at is None else "ended", reason)
                    return
        except asyncio.CancelledError:
            raise

    async def _release(self, reason):
        async with self._release_lock:
            if not self.owned:
                return
            conn = self.conn
            generation = self.generation
            if self.audio is not None:
                self.audio.close()
            self.changed("ending")
            try:
                # Only an explicitly claimed call can be released. Wait for
                # RELEASE's transport ACK before FIN, bounded to 2.1 seconds.
                if conn is not None and not conn.closing and self.owner.ctp:
                    if self.connect_sent or self.start_sent:
                        with contextlib.suppress(OSError, ValueError, CallError):
                            await self._send_reliable(b"\x00\x0e\x00", generation)
                    if not conn.closing:
                        with contextlib.suppress(OSError, ValueError):
                            self.owner.send(conn, flags=0x20)
                        conn.closing = True
            finally:
                if self.owned and self.generation == generation:
                    self.finish("ended", reason)

    async def hangup(self, controller=None):
        # A passive ringing event must not cancel somebody else's call.
        if not self.owned:
            raise CallError("Kein von HA angenommenes Gespräch aktiv")
        if controller is not None and controller is not self.controller:
            raise CallError("Gespräch gehört zu einer anderen Sitzung")
        answer = self._answer_task
        if answer is not None and answer is not asyncio.current_task():
            answer.cancel()
            with contextlib.suppress(asyncio.CancelledError, CallError):
                await answer
        if self.owned:
            await self._release("Aufgelegt")

    def finish(self, state, reason):
        self._cancel_ring_timer()
        self.owned = False
        self.ended_at = time.time()
        self.end_reason = reason
        self._capabilities_received.set()
        self._connect_received.set()
        if self.audio is not None:
            self.audio.close()
        if self._pending is not None and not self._pending.done():
            self._pending.set_exception(CallError(reason))
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        if self._watch_task is not None and self._watch_task is not current:
            self._watch_task.cancel()
        self._watch_task = None
        self.changed(state)

    async def stop(self):
        if self.owned:
            await self.hangup()
        else:
            self.finish("idle", "Integration gestoppt")
        self.audio_listeners.clear()

    def diagnostics(self):
        audio = self.audio
        return {
            "call_id": self.call_id.hex() if self.call_id else None,
            "call_direction": self.direction, "can_start": self.can_start,
            "requested_at": self.requested_at,
            "start_sent": self.start_sent, "start_acknowledged": self.start_acknowledged,
            "peer_connected": self.peer_connected,
            "call_source": self.origin, "call_error": self.error,
            "call_peer": f"{self.conn.peer[0]}:{self.conn.peer[1]}" if self.conn else None,
            "can_answer": self.can_answer, "can_hangup": self.owned,
            "ring_at": self.ring_at, "answered_at": self.answered_at,
            "first_audio_at": self.first_audio_at, "ended_at": self.ended_at,
            "end_reason": self.end_reason, "release_cause": self.release_cause,
            "control_tx": self.control_tx, "control_acked": self.control_acked,
            "connect_sent": self.connect_sent, "connect_acknowledged": self.connect_acked,
            "media_requested": self.media_sent,
            "peer_capabilities": f"0x{self.capabilities:08x}" if self.capabilities is not None else None,
            "last_media_request": self.last_media_request,
            "audio_port": audio.port if audio else None,
            "audio_peer": f"{audio.peer[0]}:{audio.peer[1]}" if audio and audio.peer else None,
            "audio_codec": ("PCMA" if audio.payload_type == 8 else "PCMU")
                if audio and audio.payload_type is not None else None,
            "audio_packets": audio.packets if audio else 0,
            "audio_rejected": audio.rejected if audio else 0,
            "audio_lost": audio.lost if audio else 0,
            "audio_duplicates": audio.duplicates if audio else 0,
            "audio_seconds": round(audio.samples / 8000, 2) if audio else 0,
            "audio_listeners": len(self.audio_listeners),
            "microphone_supported": True,
            "microphone_source": "sip" if self.controller is not None else None,
            "audio_tx_endpoint": f"{self.tx_endpoint[0]}:{self.tx_endpoint[1]}" if self.tx_endpoint else None,
            "audio_tx_packets": self.tx_writer.packets,
            "audio_tx_error": self.tx_error,
        }
