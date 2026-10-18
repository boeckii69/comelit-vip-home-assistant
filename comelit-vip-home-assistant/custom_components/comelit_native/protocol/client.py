"""Native UDP registration, rings, door release, and explicit call answering.

Door release is invoked only by open_door(), never by startup, ring handling,
registration, or a retry loop. No vendor execution or automatic call answering.
"""
from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
import ipaddress
import logging
import secrets
import socket
import struct
import time
from typing import Callable

from .ctp import build_ctp_packet, parse_ctp_packet, parse_invite
from .messages import crp_reply, crp_request, crp_result, register_request, register_result, open_door_request
from .call import IncomingCall, CallError
from .sip import allowed_addresses
from .remote_gate import valid_gateway_key

LOG = logging.getLogger(__name__)
DOOR_RESPONSE_TIMEOUT = 5.0
DOOR_COOLDOWN = 2.0


class DoorCommandError(Exception):
    """The door command could not be confirmed; not a physical door state."""


def route_source_ip(peer: tuple[str, int]) -> str:
    """Ask the OS which local IPv4 it would use; send no UDP datagram."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(peer)
        return sock.getsockname()[0]


@dataclass
class Settings:
    local_ip: str = "192.0.2.10"
    crp_broadcast_ip: str = "192.0.2.255"
    master_ip: str = "192.0.2.20"
    master_port: int = 52051
    entrance_ip: str = "192.0.2.21"
    entrance_port: int = 44721
    master_address: str = "00000001"
    source_address: str = "000000011"
    entrance_address: str = "00000002"
    loxone_enabled: bool = False
    loxone_ip: str = "192.0.2.40"
    loxone_port: int = 7777
    door_relay: int = 1
    sip_enabled: bool = False
    sip_port: int = 5062
    sip_allowed_ips: str = ""
    remote_gateway_key: str = ""
    remote_auto_enabled: bool = False

    def validate(self):
        if type(self.remote_auto_enabled) is not bool:
            raise ValueError("Ungültiger externer Audio-Modus")
        if not valid_gateway_key(self.remote_gateway_key):
            raise ValueError("Gateway-Schlüssel: 32–128 Buchstaben, Ziffern, _ oder -")
        if type(self.sip_enabled) is not bool or type(self.sip_port) is not int or not 1024 <= self.sip_port <= 65535:
            raise ValueError("Ungültige SIP-Einstellungen")
        if not allowed_addresses(self.sip_allowed_ips) and self.sip_enabled:
            raise ValueError("Für SIP mindestens eine Geräte-IP der Loxone-App freigeben")
        if type(self.door_relay) is not int or self.door_relay not in (1, 2):
            raise ValueError("Türöffner-Relais muss 1 oder 2 sein")
        addresses = [self.local_ip, self.master_ip, self.entrance_ip]
        if self.loxone_enabled or self.loxone_ip:
            addresses.append(self.loxone_ip)
        for value in addresses:
            ip = ipaddress.IPv4Address(value)
            if ip.is_unspecified or ip.is_multicast or int(ip) == 0xffffffff:
                raise ValueError("Use a unicast IPv4 address")
        broadcast = ipaddress.IPv4Address(self.crp_broadcast_ip)
        if broadcast.is_unspecified or broadcast.is_multicast:
            raise ValueError("Invalid CRP broadcast IPv4 address")
        for port in (self.master_port, self.entrance_port, self.loxone_port):
            if not isinstance(port, int) or not 1 <= port <= 65535:
                raise ValueError("Invalid UDP port")
        for address in (self.master_address, self.entrance_address):
            if len(address) != 8 or not address.isascii() or not address.isdigit():
                raise ValueError("ViP base addresses must have eight digits")
        if (len(self.source_address) != 9 or not self.source_address.isascii()
                or not self.source_address.isdigit()
                or not self.source_address.startswith(self.master_address)
                or self.source_address[-1] == "0"):
            raise ValueError("Use an eight-digit apartment address plus a slave digit 1-9")


@dataclass
class Connection:
    local_id: bytes
    peer: tuple[str, int]
    remote_address: str
    sequence: int
    expected: int
    last_seen: float = field(default_factory=time.monotonic)
    closing: bool = False
    call_id: bytes | None = None
    peer_confirmed: bool = False
    request_acknowledged: bool = False


class Receiver(asyncio.DatagramProtocol):
    def __init__(self, owner, kind):
        self.owner, self.kind = owner, kind

    def datagram_received(self, data, addr):
        if not self.owner.running:
            return
        if self.kind == "ctp":
            self.owner.receive_ctp(data, addr)
        else:
            self.owner.receive_crp(data, addr)

    def error_received(self, exc):
        self.owner.last_udp_error = str(exc)
        self.owner.on_status()
        LOG.warning("Comelit UDP: %s", exc)

    def connection_lost(self, exc):
        if self.owner.running:
            self.owner.registered = False
            self.owner.set_state("socket_error", str(exc or "UDP socket closed"))
            if self.owner.pending is not None and not self.owner.pending.done():
                self.owner.pending.set_exception(ConnectionError(self.owner.error))
            self.owner.fail_door_waiters(self.owner.error)


class NativeClient:
    def __init__(self, settings: Settings, on_ring: Callable, on_status: Callable):
        settings.validate()
        self.settings = settings
        self.on_ring, self.on_status = on_ring, on_status
        self.ctp = self.crp = self.loxone = None
        self.task = None
        self.running = False
        self.registered = False
        self.state = "stopped"
        self.error = ""
        self.local_port = 0
        self.ctp_bind_endpoint = None
        self.ctp_route_source = None
        self.connections: dict[bytes, Connection] = {}
        self.seen_calls: dict[tuple, float] = {}
        self.pending = None
        self.registration_id = None
        self.request_id = None
        self.last_registration = None
        self.last_ring = None
        self.last_loxone_send = None
        self.ring_count = 0
        self.renew_requested = asyncio.Event()
        self.registration_attempt = 0
        self.registration_tx = 0
        self.master_udp_received = 0
        self.master_ctp_accepted = 0
        self.registration_ctp_received = 0
        self.registration_acknowledged = False
        self.rejected_packets = 0
        self.last_rejection = ""
        self.last_ctp = None
        self.last_udp_error = ""
        self.master_port = settings.master_port
        self.crp_state = "not_resolved"
        self.crp_requests = 0
        self.crp_responses = 0
        self.crp_return_path_matches = None
        self.last_crp_error = ""
        self.crp_pending = None
        self.crp_query = None
        self.entrance_port = settings.entrance_port
        self.entrance_crp_pending = None
        self.entrance_crp_query = None
        self.entrance_crp_error = ""
        self.door_lock = asyncio.Lock()
        self.door_pending = None
        self.door_connection_id = None
        self.door_state = "idle"
        self.door_error = ""
        self.door_acknowledged = False
        self.door_release_cause = None
        self.door_command_path = None
        self.door_command_peer = None
        self.door_call_id = None
        self.last_door_send = None
        self.last_door_confirmation = None
        self.last_door_relay = None
        self.door_command_count = 0
        self._last_door_attempt = None
        self.call = IncomingCall(self)

    def fail_door_waiters(self, message):
        for future in (self.entrance_crp_pending, self.door_pending):
            if future is not None and not future.done():
                future.set_exception(ConnectionError(message))

    def set_door_state(self, state, error=""):
        self.door_state, self.door_error = state, error
        self.on_status()

    def new_call_connection(self, call_id):
        """Allocate a native outgoing connection after strict entrance CRP."""
        if not self.running or not self.registered or self.ctp is None:
            raise ConnectionError("Comelit ist nicht angemeldet")
        self.cleanup()
        if len(self.connections) >= 64:
            raise ConnectionError("CTP-Verbindungslimit erreicht")
        local = struct.pack(">H", secrets.randbelow(0x7ffe) + 1)
        while local in self.connections:
            local = struct.pack(">H", secrets.randbelow(0x7ffe) + 1)
        conn = Connection(local, (self.settings.entrance_ip, self.entrance_port),
                          self.settings.entrance_address, secrets.randbelow(256),
                          secrets.randbelow(256), call_id=call_id)
        self.connections[local] = conn
        return conn

    async def resolve_entrance(self):
        """Resolve the configured entrance on demand, independently of registration."""
        cfg = self.settings
        self.entrance_crp_query = crp_request(
            cfg.source_address, cfg.local_ip, self.local_port, cfg.entrance_address)
        self.entrance_crp_pending = asyncio.get_running_loop().create_future()
        self.entrance_crp_error = ""
        try:
            if self.crp is None:
                raise ConnectionError("CRP-Socket ist nicht gestartet")
            for _ in range(3):
                self.crp.sendto(self.entrance_crp_query, (cfg.crp_broadcast_ip, 24100))
                try:
                    self.entrance_port = await asyncio.wait_for(
                        asyncio.shield(self.entrance_crp_pending), 0.5)
                    return
                except TimeoutError:
                    continue
            raise TimeoutError(self.entrance_crp_error or
                f"Keine CRP-Antwort der Außenstation {cfg.entrance_address} "
                f"an {cfg.entrance_ip}; kein Türöffnerbefehl gesendet")
        finally:
            if self.entrance_crp_pending is not None and not self.entrance_crp_pending.done():
                self.entrance_crp_pending.cancel()
            self.entrance_crp_pending = None
            self.entrance_crp_query = None

    async def open_door(self):
        """Send one addressed request, using our existing call when active.

        Standalone requests require CSP RELEASE(cause=0). In-call commands
        report only their transport ACK, never a fabricated release result.
        Neither path retries an opening command or falls back to another path.
        """
        if not self.running or not self.registered or self.ctp is None:
            raise DoorCommandError("Comelit ist nicht angemeldet; kein Türöffnerbefehl gesendet")
        if self.door_lock.locked():
            raise DoorCommandError("Ein Türöffnerbefehl läuft bereits")
        if (self._last_door_attempt is not None
                and time.monotonic() - self._last_door_attempt < DOOR_COOLDOWN):
            raise DoorCommandError("Bitte zwei Sekunden zwischen Türöffnerbefehlen warten")
        async with self.door_lock:
            relay = self.settings.door_relay
            self._last_door_attempt = time.monotonic()
            self.door_acknowledged = False
            self.door_release_cause = None
            self.door_command_path = "standalone"
            self.door_command_peer = None
            self.door_call_id = None
            if self.call.owned:
                return await self._open_door_on_call(relay)
            self.set_door_state("resolving")
            conn = None
            sent = False
            try:
                await self.resolve_entrance()
                if not self.running or not self.registered or self.ctp is None:
                    raise ConnectionError("Comelit-Verbindung beendet; kein Türöffnerbefehl gesendet")
                # Answering can finish while entrance discovery is pending.
                if self.call.owned:
                    return await self._open_door_on_call(relay)
                cfg = self.settings
                body = open_door_request(cfg.entrance_address, relay)
                self.cleanup()
                if len(self.connections) >= 64:
                    raise ConnectionError("CTP-Verbindungslimit erreicht; kein Türöffnerbefehl gesendet")
                local = struct.pack(">H", secrets.randbelow(0x7ffe) + 1)
                while local in self.connections:
                    local = struct.pack(">H", secrets.randbelow(0x7ffe) + 1)
                initial_seq = secrets.randbelow(256)
                conn = Connection(local, (cfg.entrance_ip, self.entrance_port),
                    cfg.entrance_address, (initial_seq + 1) & 255, secrets.randbelow(256))
                self.connections[local] = conn
                self.door_command_peer = f"{conn.peer[0]}:{conn.peer[1]}"
                self.door_connection_id = local
                self.door_pending = asyncio.get_running_loop().create_future()
                packet = build_ctp_packet(flags=0xc0, connection=local,
                    sequence=initial_seq, acknowledgement=conn.expected, body=body,
                    source=cfg.source_address, destination=cfg.entrance_address)
                self.ctp.sendto(packet, conn.peer)
                sent = True
                self.last_door_send = time.time()
                self.last_door_relay = relay
                self.door_command_count += 1
                self.set_door_state("waiting")
                cause = await asyncio.wait_for(
                    asyncio.shield(self.door_pending), DOOR_RESPONSE_TIMEOUT)
                self.door_release_cause = cause
                if cause != 0:
                    self.set_door_state("refused", f"Außenstation meldet Ablehnung: Code {cause}")
                    raise DoorCommandError(self.door_error)
                self.last_door_confirmation = time.time()
                self.set_door_state("confirmed")
            except TimeoutError as exc:
                if sent:
                    detail = ("CTP bestätigt, aber keine erfolgreiche Befehlsantwort erhalten"
                              if self.door_acknowledged else "Keine Befehlsbestätigung erhalten")
                    self.set_door_state("unconfirmed", detail +
                        "; Türzustand unbekannt, keine automatische Wiederholung")
                else:
                    self.set_door_state("failed", str(exc))
                raise DoorCommandError(self.door_error) from exc
            except (OSError, ValueError) as exc:
                self.set_door_state("unconfirmed" if sent else "failed", str(exc))
                raise DoorCommandError(self.door_error) from exc
            except asyncio.CancelledError:
                if self.door_command_path != "active_call":
                    self.set_door_state("unconfirmed" if sent else "cancelled",
                        "Vorgang beendet; keine automatische Wiederholung")
                raise
            finally:
                if self.door_pending is not None and not self.door_pending.done():
                    self.door_pending.cancel()
                self.door_pending = None
                self.door_connection_id = None
                if conn is not None:
                    if self.running and self.ctp and conn.peer_confirmed and not conn.closing:
                        with contextlib.suppress(OSError):
                            self.send(conn, flags=0x20)
                    conn.closing = True
                self.on_status()

    async def _open_door_on_call(self, relay):
        """Use the native in-call path. Caller holds door_lock throughout."""
        call = self.call
        self.door_command_path = "active_call"
        self.door_call_id = call.call_id.hex() if call.call_id else None
        self.door_command_peer = f"{call.conn.peer[0]}:{call.conn.peer[1]}" if call.conn else None
        self.set_door_state("sending")
        sent = False

        def on_sent():
            nonlocal sent
            sent = True
            self.last_door_send = time.time()
            self.last_door_relay = relay
            self.door_command_count += 1
            self.set_door_state("waiting")

        try:
            await call.open_door(relay, on_sent)
        except (CallError, OSError, ValueError) as exc:
            self.set_door_state("unconfirmed" if sent else "failed", str(exc))
            raise DoorCommandError(self.door_error) from exc
        except asyncio.CancelledError:
            self.set_door_state("unconfirmed" if sent else "cancelled",
                                "Vorgang beendet; keine automatische Wiederholung")
            raise
        else:
            self.door_acknowledged = True
            # No standalone RELEASE is expected or sent on the active call.
            # Keep last_door_confirmation as the historical standalone result.
            self.set_door_state("transport_acknowledged")

    def reject(self, reason):
        self.rejected_packets += 1
        self.last_rejection = reason
        self.on_status()

    def registration_timeout(self):
        if self.registration_acknowledged:
            detail = "CTP-Anfrage bestätigt; passende REGISTER_RES fehlt"
        elif self.registration_ctp_received:
            detail = "CTP-Antwort zur Anmeldung empfangen; passende REGISTER_RES fehlt"
        elif self.master_udp_received:
            detail = "UDP von TP6842 empfangen; keine passende CTP-Anmeldeantwort"
        else:
            detail = "Keine UDP-Antwort der TP6842 am CTP-Socket empfangen"
        if self.last_rejection:
            detail += f"; zuletzt verworfen: {self.last_rejection}"
        if self.last_udp_error:
            detail += f"; UDP-Fehler: {self.last_udp_error}"
        return detail

    def set_state(self, state: str, error: str = ""):
        changed = self.state != state or self.error != error
        self.state, self.error = state, error
        if changed:
            LOG.info("Comelit native: %s%s", state, f" ({error})" if error else "")
        self.on_status()

    async def start(self):
        loop = asyncio.get_running_loop()
        self.running = True
        try:
            # Native ctp_listen uses a wildcard receive socket. A bind to the
            # single local IP misses LAN broadcasts. Keep the advertised HA
            # address and verify that normal routing uses that same source.
            self.ctp_route_source = await asyncio.to_thread(route_source_ip,
                (self.settings.master_ip, self.master_port))
            if self.ctp_route_source != self.settings.local_ip:
                raise OSError(
                    f"CTP-Route verwendet {self.ctp_route_source}; "
                    f"konfigurierte HA-IP ist {self.settings.local_ip}")
            self.ctp, _ = await loop.create_datagram_endpoint(
                lambda: Receiver(self, "ctp"), local_addr=("0.0.0.0", 0),
                allow_broadcast=True)
            self.local_port = self.ctp.get_extra_info("sockname")[1]
            self.ctp_bind_endpoint = f"0.0.0.0:{self.local_port}"
            # Wildcard binding is necessary for incoming LAN broadcast queries.
            # Exclusive bind avoids competing responders on an existing service.
            self.crp, _ = await loop.create_datagram_endpoint(
                lambda: Receiver(self, "crp"), local_addr=("0.0.0.0", 24100),
                allow_broadcast=True)
            if self.settings.loxone_enabled:
                self.loxone, _ = await loop.create_datagram_endpoint(
                    asyncio.DatagramProtocol, local_addr=(self.settings.local_ip, 0),
                    remote_addr=(self.settings.loxone_ip, self.settings.loxone_port))
        except BaseException:
            await self.stop()
            raise
        self.task = asyncio.create_task(self.run(), name="comelit_native_registration")

    async def stop(self):
        await self.call.stop()
        self.running = False
        self.registered = False
        self.fail_door_waiters("Comelit-Verbindung beendet")
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        # The call controller releases only a call explicitly answered by us.
        for transport in (self.ctp, self.crp, self.loxone):
            if transport:
                transport.close()
        self.ctp = self.crp = self.loxone = None
        self.ctp_bind_endpoint = None
        self.connections.clear()
        self.set_state("stopped")

    async def resolve_master(self):
        """Resolve only the configured master ViP via native LAN broadcast.

        Use the broadcast destination from the existing installation, as in
        the native stack. Replies must still match the configured master IP
        and ViP identity before selecting a CTP port. The reply suffix is
        informational, not a required echo of our return path.
        """
        cfg = self.settings
        self.crp_state = "resolving"
        self.set_state("resolving")
        self.crp_query = crp_request(cfg.source_address, cfg.local_ip,
                                     self.local_port, cfg.master_address)
        self.crp_pending = asyncio.get_running_loop().create_future()
        try:
            if self.crp is None:
                raise ConnectionError("CRP-Socket ist nicht gestartet")
            for _attempt in range(3):
                self.crp.sendto(self.crp_query, (cfg.crp_broadcast_ip, 24100))
                self.crp_requests += 1
                try:
                    port = await asyncio.wait_for(asyncio.shield(self.crp_pending), 0.5)
                    self.master_port = port
                    self.crp_state = "resolved"
                    self.on_status()
                    return
                except TimeoutError:
                    continue
            self.crp_state = "failed"
            if self.last_crp_error:
                detail = f"CRP-Antwort nicht verwendbar: {self.last_crp_error}"
            else:
                detail = (f"Keine passende CRP-Antwort von {cfg.master_ip}:24100 "
                          f"auf Broadcast an {cfg.crp_broadcast_ip}:24100")
            if self.last_udp_error:
                detail += f"; UDP-Fehler: {self.last_udp_error}"
            raise TimeoutError(detail + "; CTP-Anmeldung noch nicht gesendet")
        except (OSError, ValueError):
            self.crp_state = "failed"
            raise
        finally:
            if self.crp_pending is not None and not self.crp_pending.done():
                self.crp_pending.cancel()
            self.crp_pending = None
            self.crp_query = None

    def send(self, conn, flags=0, body=b""):
        payload = build_ctp_packet(
            flags=flags, connection=conn.local_id, sequence=conn.sequence,
            acknowledgement=conn.expected, body=body,
            source=self.settings.source_address, destination=conn.remote_address)
        if self.ctp:
            self.ctp.sendto(payload, conn.peer)

    async def register(self):
        cfg = self.settings
        self.registered = False
        self.registration_attempt += 1
        self.registration_tx = 0
        self.master_udp_received = 0
        self.master_ctp_accepted = 0
        self.registration_ctp_received = 0
        self.registration_acknowledged = False
        self.rejected_packets = 0
        self.last_rejection = ""
        self.last_ctp = None
        self.last_udp_error = ""
        self.registration_id = None
        self.crp_requests = 0
        self.crp_responses = 0
        self.crp_return_path_matches = None
        self.last_crp_error = ""
        await self.resolve_master()
        self.set_state("registering")
        local = struct.pack(">H", secrets.randbelow(0x7ffe) + 1)
        while local in self.connections:
            local = struct.pack(">H", secrets.randbelow(0x7ffe) + 1)
        conn = Connection(local, (cfg.master_ip, self.master_port), cfg.master_address,
                          secrets.randbelow(256), secrets.randbelow(256))
        self.connections[local] = conn
        self.registration_id = local
        # Separate application request ID, little endian in CSP, as in SubUnit.
        self.request_id = secrets.randbelow(65536)
        body = register_request(self.request_id, cfg.source_address)
        self.pending = asyncio.get_running_loop().create_future()
        initial_seq = conn.sequence
        conn.sequence = (initial_seq + 1) & 255
        try:
            # Only this registration is retransmitted. No probing of addresses,
            # ports, command variants or alternate slave identities.
            for _attempt in range(6):
                # Native _receive_ctp_message advances the send queue on ACK.
                # Do not resend an application request the peer already ACKed.
                if not conn.request_acknowledged:
                    request = build_ctp_packet(
                        flags=0x40 if conn.peer_confirmed else 0xc0,
                        connection=local, sequence=initial_seq,
                        acknowledgement=conn.expected, body=body,
                        source=cfg.source_address, destination=cfg.master_address)
                    self.ctp.sendto(request, conn.peer)
                    self.registration_tx += 1
                try:
                    result = await asyncio.wait_for(asyncio.shield(self.pending), 0.5)
                    break
                except TimeoutError:
                    continue
            else:
                raise TimeoutError(self.registration_timeout())
            if result:
                raise ConnectionError(f"Registrierung abgelehnt: Code {result}")
            self.registered = True
            self.last_registration = time.time()
            self.set_state("registered")
            self.send(conn, flags=0x20)
            conn.closing = True
        finally:
            if self.pending and not self.pending.done():
                self.pending.cancel()
            self.pending = None

    async def run(self):
        delay = 10
        while self.running:
            self.renew_requested.clear()
            try:
                await self.register()
                delay = 10
                # Original SubUnit refreshes at 3,300,000 ms (55 minutes).
                try:
                    await asyncio.wait_for(self.renew_requested.wait(), 3300)
                except TimeoutError:
                    pass
            except (OSError, ValueError, ConnectionError, TimeoutError) as exc:
                self.registered = False
                self.set_state("registration_failed", str(exc))
                await asyncio.sleep(delay)
                delay = min(delay * 2, 300)
            self.cleanup()

    def cleanup(self):
        now = time.monotonic()
        self.connections = {k: v for k, v in self.connections.items() if now - v.last_seen < 180}
        self.seen_calls = {k: v for k, v in self.seen_calls.items() if now - v < 180}

    def receive_crp(self, data, addr):
        if addr[0] not in (self.settings.master_ip, self.settings.entrance_ip):
            return
        if (self.entrance_crp_pending is not None and not self.entrance_crp_pending.done()
                and addr[0] == self.settings.entrance_ip and data[:4] == b"\0\0\0\x02"):
            try:
                if addr[1] != 24100:
                    raise ValueError("CRP-Antwort der Außenstation hat falschen Absenderport")
                port = crp_result(data, self.entrance_crp_query, self.settings.entrance_ip)
            except ValueError as exc:
                self.entrance_crp_error = str(exc)
            else:
                self.entrance_crp_error = ""
                self.entrance_crp_pending.set_result(port)
            return
        if (self.crp_pending is not None and not self.crp_pending.done()
                and addr[0] == self.settings.master_ip
                and data[:4] == b"\0\0\0\x02"):
            self.crp_responses += 1
            try:
                if addr[1] != 24100:
                    raise ValueError(f"CRP-Absenderport {addr[1]}, erwartet 24100")
                port = crp_result(data, self.crp_query, self.settings.master_ip)
            except ValueError as exc:
                self.last_crp_error = str(exc)
            else:
                self.last_crp_error = ""
                self.crp_return_path_matches = data[20:36] == self.crp_query[4:20]
                self.crp_pending.set_result(port)
            self.on_status()
            return
        reply = crp_reply(data, self.settings.source_address, self.settings.local_ip, self.local_port)
        if reply is not None and self.crp:
            # The native stack replies to CRP port 24100, not the CTP port in
            # the query. Reply only to the configured sender; no broadcast scan.
            self.crp.sendto(reply, (addr[0], 24100))

    def receive_ctp(self, data, addr):
        cfg = self.settings
        if addr[0] not in (cfg.master_ip, cfg.entrance_ip):
            return
        if addr[0] == cfg.master_ip:
            self.master_udp_received += 1
        peers = {(cfg.master_ip, self.master_port), (cfg.entrance_ip, self.entrance_port)}
        if addr not in peers:
            self.reject(f"Unerwarteter Absenderport {addr[0]}:{addr[1]}")
            return
        try:
            packet = parse_ctp_packet(data)
        except (ValueError, struct.error) as exc:
            self.reject(str(exc))
            return
        self.last_ctp = {"peer": f"{addr[0]}:{addr[1]}",
                         "flags": f"0x{packet.flags:02x}",
                         "connection": packet.connection.hex(),
                         "sequence": packet.sequence,
                         "acknowledgement": packet.acknowledgement,
                         "opcode": f"0x{packet.opcode:04x}" if packet.opcode is not None else None,
                         "body_length": len(packet.body),
                         "trailer_length": packet.trailer_length,
                         "source": packet.source, "destination": packet.destination}
        local_id = struct.pack(">H", struct.unpack(">H", packet.connection)[0] ^ 0x8000)
        if (int.from_bytes(local_id, "big") & 0x7fff) in (0, 0x7fff):
            self.reject("Ungültige CTP-Verbindungs-ID")
            return
        self.cleanup()
        conn = self.connections.get(local_id)
        if packet.trailer_length == 0:
            # Native peers can omit addresses. Accept only an existing connection
            # on its exact configured endpoint, never create an unbound identity.
            if conn is None or conn.peer != addr:
                self.reject("Adressloses CTP-Paket ohne bekannte Verbindung")
                return
            packet.source = conn.remote_address
            packet.destination = cfg.source_address
        if packet.destination != cfg.source_address:
            self.reject(f"CTP-Zieladresse {packet.destination!r} passt nicht")
            return
        expected_source = cfg.master_address if addr[0] == cfg.master_ip else cfg.entrance_address
        if packet.source != expected_source:
            self.reject(f"CTP-Quelladresse {packet.source!r} passt nicht")
            return
        if conn and (conn.peer != addr or conn.remote_address != packet.source):
            self.reject("CTP-Verbindung passt nicht zum Absender")
            return
        incoming_call_id = None
        if packet.is_syn and packet.opcode == 0x0001 and len(packet.body) in (28, 40, 41):
            incoming_call_id = packet.body[24:28]
            if conn is not None and conn.call_id != incoming_call_id:
                if conn is self.call.conn and self.call.owned:
                    self.reject("Neue Ruf-ID auf einer noch belegten CTP-Verbindung")
                    return
                # A later call may reuse a CTP ID from an earlier, ended call.
                conn = None
        if conn is None:
            if not packet.is_syn or len(self.connections) >= 64:
                self.reject("Unbekannte CTP-Verbindung ohne SYN oder Verbindungslimit")
                return
            conn = Connection(local_id, addr, packet.source,
                              packet.acknowledgement, packet.sequence)
            conn.call_id = incoming_call_id
            self.connections[local_id] = conn
        conn.last_seen = time.monotonic()
        if addr[0] == cfg.master_ip:
            self.master_ctp_accepted += 1
        if local_id == self.registration_id:
            self.registration_ctp_received += 1
            if packet.acknowledgement == conn.sequence:
                conn.request_acknowledged = True
                self.registration_acknowledged = True
        if local_id == self.door_connection_id and packet.acknowledgement == conn.sequence:
            conn.request_acknowledged = True
            self.door_acknowledged = True
        if (conn is self.call.conn and self.call.owned and self.call.direction == "outgoing"
                and self.call.start_sent and not conn.peer_confirmed):
            # The first reply establishes the peer's initial sequence. Pin it
            # only on this new connection with the exact START acknowledgement.
            if packet.acknowledgement != conn.sequence:
                self.reject("Aktiver Ruf: erste Antwort bestätigt START nicht")
                return
            conn.expected = packet.sequence
            conn.request_acknowledged = True
        fresh = bool(packet.body) and packet.sequence == conn.expected
        if fresh:
            conn.expected = (conn.expected + 1) & 255
        if fresh or conn.request_acknowledged:
            conn.peer_confirmed = True
        # Empty DATA is a keepalive. It needs an ACK but consumes no sequence.
        if packet.flags & 0x40 or packet.body:
            self.send(conn)
        if fresh and local_id == self.registration_id and self.pending is not None:
            result = register_result(packet.body, self.request_id, cfg.source_address)
            if result is not None and not self.pending.done():
                self.pending.set_result(result)
            elif packet.opcode == 0x0041:
                self.reject("REGISTER_RES: Länge, Anfrage-ID oder Slave-Adresse passt nicht")
        elif packet.body and not fresh and local_id == self.registration_id:
            self.reject(f"CTP-Sequenz {packet.sequence}, erwartet {conn.expected}")
        if (fresh and local_id == self.door_connection_id and self.door_pending is not None
                and not self.door_pending.done() and conn.request_acknowledged
                and packet.acknowledgement == conn.sequence
                and packet.opcode == 0x000e and len(packet.body) == 3):
            self.door_pending.set_result(packet.body[2])
        if fresh and packet.opcode == 0x0001:
            self.handle_invite(packet, conn)
        self.call.receive(conn, packet, fresh)
        # REGISTER_RENEW is a CSP broadcast (0x0042), handled only when a
        # supported packet reaches us. Periodic refresh does not depend on it.
        if fresh and packet.opcode == 0x0042 and addr[0] == cfg.master_ip:
            self.renew_requested.set()
        if packet.is_fin and not conn.closing:
            self.send(conn, flags=0x20)
            conn.closing = True
        self.on_status()

    def handle_invite(self, packet, conn):
        cfg = self.settings
        # A successful registration is necessary before publishing any ring.
        if not self.registered or len(packet.body) not in (28, 40, 41):
            return
        try:
            origin, destination, call_id, tag = parse_invite(packet.body)
        except ValueError:
            return
        if origin != cfg.entrance_address or destination not in (cfg.master_address, cfg.source_address):
            return
        if tag and tag != b"PP":
            return
        key = (origin, destination, call_id)
        if key in self.seen_calls:
            return
        self.seen_calls[key] = time.monotonic()
        self.call.observe_invite(conn, origin, call_id)
        self.last_ring = time.time()
        self.ring_count += 1
        event = {"source": origin, "destination": destination, "call_id": call_id.hex()}
        if self.loxone:
            try:
                self.loxone.sendto(b"COMELIT_RING=1")
                self.last_loxone_send = time.time()
            except OSError as exc:
                self.error = f"Loxone UDP: {exc}"
        self.on_ring(event)
        self.on_status()
