"""CSP registration and CRP response layouts checked against PC Intercom 2.3.1.

Request IDs and leases are little endian; CTP IDs and body lengths are big
endian. These are separate fields. See PROTOCOL_EVIDENCE.txt in the archive.
"""
import ipaddress
import struct

from .ctp import encode_logaddr


def crp_request(source: str, local_ip: str, local_port: int, target: str) -> bytes:
    """Resolve one logical address and advertise our return CTP endpoint."""
    return (b"\x00\x00\x00\x01" + encode_logaddr(source)
            + struct.pack(">H", local_port) + ipaddress.IPv4Address(local_ip).packed
            + encode_logaddr(target) + b"\0" * 6)


def crp_result(data: bytes, query: bytes, expected_ip: str) -> int:
    """Resolve the pending ViP identity as the native CRP cache does.

    Native _crp_input matches the advertised identity at offset 4 and reads
    its endpoint at 14..19. It does not compare the reply's 20..35 suffix to
    the requester's endpoint. Broadcast answers may populate a resolver's
    pending entry without echoing that resolver's own request. We additionally
    require the configured IP; the caller validates the UDP sender and port.
    """
    if len(data) != 36 or data[:4] != b"\0\0\0\x02":
        raise ValueError("Keine gültige CRP-Antwort (36 Bytes, Typ 2)")
    if data[4:14] != query[20:30]:
        raise ValueError("CRP-Antwort nennt eine andere ViP-Adresse")
    advertised_ip = str(ipaddress.IPv4Address(data[16:20]))
    if advertised_ip != expected_ip:
        raise ValueError(f"CRP meldet IP {advertised_ip}, erwartet {expected_ip}")
    port = struct.unpack_from(">H", data, 14)[0]
    if not port:
        raise ValueError("CRP meldet ungültigen CTP-Port 0")
    return port


def register_request(request_id: int, source: str, lease: int = 3600) -> bytes:
    return b"\x00\x40" + struct.pack("<H", request_id) + encode_logaddr(source) + struct.pack("<HB", lease, 0)


def open_door_request(target: str, relay: int) -> bytes:
    """Native csp_send_opendoor_onconn: opcode + logaddr_t + relay byte.

    The original stack allocates exactly 13 bytes. No hold duration, password,
    or 32-bit relay value is part of this CSP body.
    """
    if len(target) != 8 or not target.isascii() or not target.isdigit():
        raise ValueError("Türöffnerziel muss eine achtstellige ViP-Adresse sein")
    if type(relay) is not int or relay not in (1, 2):
        raise ValueError("Türöffner-Relais muss 1 oder 2 sein")
    return b"\x00\x2d" + encode_logaddr(target) + bytes((relay,))


def audio_media_request(local_ip: str, port: int, *, stop: bool = False) -> bytes:
    """Native csp_send_mediareq16 / Call::sendMediaReq(channel=audio).

    The type is 0x18 (or 0x98 to stop), flags 0 = IPv4/UDP, followed by
    IPv4 in network order and port, max payload, bitrate in LITTLE endian.
    These are real UDP endpoints, not the ICONA bridge's channel handles.
    """
    ip = ipaddress.IPv4Address(local_ip)
    if ip.is_unspecified or ip.is_multicast or int(ip) == 0xffffffff:
        raise ValueError("Ungültige Audio-IP")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Ungültiger Audio-Port")
    return (b"\x00\x11" + bytes((0x98 if stop else 0x18, 0))
            + ip.packed + struct.pack("<HHI", port, 1400, 750))


def outgoing_call_request(source: str, target: str, call_id: bytes) -> bytes:
    """CSP START40 for explicit self-activation, not a visitor ring.

    Native csp_create_start40 layout; 01/20 and II match the documented
    community self-activation request. IPv4/UDP transport remains native.
    """
    if (len(source) != 9 or not source.isascii() or not source.isdigit()
            or source[-1] == "0" or len(target) != 8
            or not target.isascii() or not target.isdigit()
            or not isinstance(call_id, bytes) or len(call_id) != 4):
        raise ValueError("Ungültige Adresse oder Ruf-ID für aktiven Ruf")
    return (b"\x00\x01" + encode_logaddr(source) + encode_logaddr(target)
            + b"\x01\x20" + call_id + encode_logaddr(source) + b"II")


def register_result(body: bytes, request_id: int, source: str) -> int | None:
    if len(body) != 16 or body[:2] != b"\x00\x41":
        return None
    if body[2:4] != struct.pack("<H", request_id) or body[4:14] != encode_logaddr(source):
        return None
    return body[14]


def crp_reply(query: bytes, source: str, local_ip: str, local_port: int) -> bytes | None:
    # Reply only to an exact query for our own slave; never claim the master.
    if len(query) != 36 or query[:4] != b"\x00\x00\x00\x01":
        return None
    if query[20:30] != encode_logaddr(source):
        return None
    return (
        b"\x00\x00\x00\x02" + encode_logaddr(source)
        + struct.pack(">H", local_port) + ipaddress.IPv4Address(local_ip).packed
        + query[4:20]
    )
