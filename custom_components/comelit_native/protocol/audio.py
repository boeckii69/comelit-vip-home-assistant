"""Receive-only RTP/G.711. No microphone, recording, or audio sender.

RTP parsing follows RFC 3550; static PT 0/8 follow RFC 3551. PCM is signed
16-bit little endian at 8 kHz. A bounded packet window handles reordering;
missing packets expire after 100 ms instead of blocking later speech.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import struct
import time


@dataclass(frozen=True)
class AudioPacket:
    payload_type: int
    sequence: int
    timestamp: int
    ssrc: int
    payload: bytes


def parse_audio_packet(data: bytes) -> AudioPacket:
    if not 12 <= len(data) <= 2048 or data[0] >> 6 != 2:
        raise ValueError("Kein gültiges RTP-v2-Paket")
    # RTCP (including SR/RR) must not be misread as RTP with its marker set.
    if 192 <= data[1] <= 223:
        raise ValueError("RTCP statt Audio-RTP")
    pt = data[1] & 127
    if pt not in (0, 8):
        raise ValueError(f"Audio-Payload-Typ {pt} wird nicht unterstützt")
    offset = 12 + 4 * (data[0] & 15)
    if offset > len(data):
        raise ValueError("RTP-CSRC abgeschnitten")
    if data[0] & 16:
        if offset + 4 > len(data):
            raise ValueError("RTP-Erweiterung abgeschnitten")
        words = struct.unpack_from(">H", data, offset + 2)[0]
        offset += 4 + words * 4
    end = len(data)
    if data[0] & 32:
        padding = data[-1]
        if not padding or padding > end - offset:
            raise ValueError("Ungültiges RTP-Padding")
        end -= padding
    if not 1 <= end - offset <= 1600:
        raise ValueError("Ungültige G.711-Nutzdatenlänge")
    sequence, timestamp, ssrc = struct.unpack_from(">HII", data, 2)
    return AudioPacket(pt, sequence, timestamp, ssrc, data[offset:end])


def _sample(code: int, alaw: bool) -> int:
    if alaw:
        value = code ^ 0x55
        segment = (value >> 4) & 7
        magnitude = (value & 15) << 4
        magnitude += 8 if segment == 0 else 264
        if segment > 1:
            magnitude <<= segment - 1
        return magnitude if value & 128 else -magnitude
    value = code ^ 255
    magnitude = (((value & 15) << 3) + 132) << ((value >> 4) & 7)
    return 132 - magnitude if value & 128 else magnitude - 132


_TABLES = {pt: tuple(struct.pack("<h", _sample(code, pt == 8))
                    for code in range(256)) for pt in (0, 8)}


def decode_g711(payload: bytes, payload_type: int) -> bytes:
    table = _TABLES[payload_type]
    return b"".join(table[value] for value in payload)


class AudioReceiver(asyncio.DatagramProtocol):
    """One ephemeral receive socket belonging to one explicitly answered call."""

    def __init__(self, allowed_ips, on_pcm, on_first_packet, on_error):
        self.allowed_ips = frozenset(allowed_ips)
        self.on_pcm, self.on_first_packet, self.on_error = on_pcm, on_first_packet, on_error
        self.transport = None
        self.active = True
        self.port = 0
        self.peer = None
        self.ssrc = None
        self.payload_type = None
        self.packets = self.rejected = self.lost = self.duplicates = 0
        self.samples = 0
        self.last_packet = None
        self.expected = None
        self.next_timestamp = None
        self.pending = {}
        self.timer = None
        self.pcm = bytearray()

    def connection_made(self, transport):
        self.transport = transport
        self.port = transport.get_extra_info("sockname")[1]

    def datagram_received(self, data, addr):
        if not self.active or addr[0] not in self.allowed_ips:
            return
        try:
            packet = parse_audio_packet(data)
        except (ValueError, struct.error):
            self.rejected += 1
            return
        if self.peer is None:
            self.peer, self.ssrc, self.payload_type = addr, packet.ssrc, packet.payload_type
            self.expected = packet.sequence
        if (addr != self.peer or packet.ssrc != self.ssrc
                or packet.payload_type != self.payload_type):
            self.rejected += 1
            return
        delta = (packet.sequence - self.expected) & 65535
        if delta >= 32768 or packet.sequence in self.pending:
            self.duplicates += 1
            return
        # An impossible leap must not allocate an unbounded reorder buffer.
        if delta > 50:
            if self.last_packet is None or time.monotonic() - self.last_packet < 1:
                self.rejected += 1
                return
            # Recover after a real network outage on this already pinned stream.
            self.lost += delta
            self.pending.clear()
            self.pcm.clear()
            self.next_timestamp = None
            self.expected = packet.sequence
        self.packets += 1
        self.last_packet = time.monotonic()
        self.pending[packet.sequence] = packet
        if self.packets == 1:
            self.on_first_packet()
        self._drain()

    def _drain(self):
        while self.expected in self.pending:
            packet = self.pending.pop(self.expected)
            if self.next_timestamp is not None:
                gap = (packet.timestamp - self.next_timestamp) & 0xffffffff
                if gap < 800:
                    self.pcm.extend(bytes(gap * 2))
            self.pcm.extend(decode_g711(packet.payload, packet.payload_type))
            self.samples += len(packet.payload)
            self.next_timestamp = (packet.timestamp + len(packet.payload)) & 0xffffffff
            self.expected = (self.expected + 1) & 65535
            # 100-ms batches avoid 50 state/websocket updates per second.
            while len(self.pcm) >= 1600:
                chunk = bytes(self.pcm[:1600])
                del self.pcm[:1600]
                self.on_pcm(chunk)
        if self.pending and self.timer is None:
            self.timer = asyncio.get_running_loop().call_later(0.1, self._skip_gap)
        elif not self.pending and self.timer is not None:
            self.timer.cancel()
            self.timer = None

    def _skip_gap(self):
        self.timer = None
        if not self.active or not self.pending:
            return
        sequence = min(self.pending, key=lambda seq: (seq - self.expected) & 65535)
        self.lost += (sequence - self.expected) & 65535
        self.expected = sequence
        self._drain()

    def error_received(self, exc):
        if self.active:
            self.on_error(str(exc))

    def connection_lost(self, exc):
        if self.active:
            self.on_error(str(exc or "Audio-Socket geschlossen"))

    def close(self):
        self.active = False
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        self.pending.clear()
        self.pcm.clear()
        if self.transport is not None:
            self.transport.close()
            self.transport = None
