"""Native talk-back: advertised endpoint only, G.711, one 20-ms frame at a time."""
import ipaddress
import secrets
import struct


def media_destination(body, allowed_ips):
    """Parse native MEDIAREQ audio; never infer TX from the incoming RTP port."""
    if len(body) not in (10, 16, 26) or body[:2] != b"\0\x11":
        raise ValueError("Ungültige native Medienanforderung")
    if body[2] not in (0x18, 0x98) or body[3] != 0:
        raise ValueError("Keine native IPv4-Audioanforderung")
    if body[2] == 0x98:
        return None
    ip = str(ipaddress.IPv4Address(body[4:8]))
    port = struct.unpack_from("<H", body, 8)[0]
    if ip not in allowed_ips or not port:
        raise ValueError("Audioziel gehört nicht zur konfigurierten Comelit")
    return ip, port


def encode_sample(sample, pt):
    """G.711 integer PCM to A-law / mu-law, independent of removed audioop."""
    if pt == 8:
        value = sample >> 3
        mask = 0xd5 if value >= 0 else 0x55
        if value < 0:
            value = -value - 1
        ends = (0x1f, 0x3f, 0x7f, 0xff, 0x1ff, 0x3ff, 0x7ff, 0xfff)
        segment = next((i for i, end in enumerate(ends) if value <= end), 8)
        if segment == 8:
            return 0x7f ^ mask
        code = segment << 4
        code |= (value >> (1 if segment < 2 else segment)) & 15
        return code ^ mask
    if pt != 0:
        raise ValueError("Nur PCMA/PCMU unterstützt")
    value = sample >> 2
    mask = 0xff if value >= 0 else 0x7f
    if value < 0:
        value = -value
    value = min(value, 8159) + 33
    ends = (0x3f, 0x7f, 0xff, 0x1ff, 0x3ff, 0x7ff, 0xfff, 0x1fff)
    segment = next((i for i, end in enumerate(ends) if value <= end), 8)
    if segment == 8:
        return 0x7f ^ mask
    return ((segment << 4) | ((value >> (segment + 1)) & 15)) ^ mask


def encode_g711(pcm, pt):
    if len(pcm) % 2 or len(pcm) > 3200:
        raise ValueError("Ungültige PCM-Länge")
    return bytes(encode_sample(sample, pt) for (sample,) in struct.iter_unpack("<h", pcm))


class RtpWriter:
    def __init__(self):
        self.ssrc = secrets.randbits(32)
        self.sequence = secrets.randbits(16)
        self.timestamp = secrets.randbits(32)
        self.packets = self.octets = 0

    def packet(self, payload, pt):
        header = struct.pack(">BBHII", 0x80, pt | (0x80 if self.packets == 0 else 0),
                             self.sequence, self.timestamp, self.ssrc)
        self.sequence = (self.sequence + 1) & 65535
        self.timestamp = (self.timestamp + len(payload)) & 0xffffffff
        self.packets += 1
        self.octets += len(payload)
        return header + payload
