"""Bounded 20-ms audio bridge between an accepted LAN SIP call and native ViP."""
import asyncio
import secrets
import struct
import time

from .audio import AudioReceiver
from .talk import RtpWriter, encode_g711


class RtcpReceiver(asyncio.DatagramProtocol):
    def __init__(self):
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport


class SipMedia(AudioReceiver):
    def __init__(self, session, call, on_error):
        self.session, self.call = session, call
        self.offer = session.offer
        self.to_phone, self.to_door = bytearray(), bytearray()
        self.writer = RtpWriter()
        self.microphone_seen = False
        self.dropped_bytes = 0
        self.task = None
        self.rtcp = None
        self.rtcp_port = 0
        self.unsubscribe = None
        self.opening = True
        super().__init__([self.offer.ip], self._phone_pcm, self._first_phone_packet, on_error)

    async def open(self, local_ip):
        loop = asyncio.get_running_loop()
        # A paired even/odd port also supports SIP clients ignoring a=rtcp.
        for _ in range(20):
            port = 40000 + 2 * secrets.randbelow(10000)
            try:
                await loop.create_datagram_endpoint(lambda: self, local_addr=(local_ip, port))
                rtcp = RtcpReceiver()
                await loop.create_datagram_endpoint(lambda: rtcp, local_addr=(local_ip, port + 1))
            except OSError:
                if self.transport:
                    self.transport.close()
                    self.transport = None
                continue
            except BaseException:
                self.close()
                if 'rtcp' in locals() and rtcp.transport:
                    rtcp.transport.close()
                raise
            self.rtcp, self.rtcp_port = rtcp.transport, port + 1
            self.opening = False
            self.unsubscribe = self.call.subscribe_audio(self._door_pcm)
            return
        raise OSError("Kein freies RTP/RTCP-Portpaar für Loxone")

    def connection_lost(self, exc):
        if not self.opening:
            super().connection_lost(exc)

    def datagram_received(self, data, addr):
        if self.session.closing or not self.session.acked or self.offer.direction == "recvonly":
            return
        if len(data) < 2 or data[1] & 127 != self.offer.pt:
            return
        super().datagram_received(data, addr)

    def _put(self, target, pcm):
        target.extend(pcm)
        # Keep at most 200 ms of current speech. Never accumulate an audio file.
        if len(target) > 3200:
            excess = len(target) - 3200
            del target[:excess]
            self.dropped_bytes += excess

    def _door_pcm(self, pcm):
        if self.active and self.session.acked and not self.session.closing:
            self._put(self.to_phone, pcm)

    def _phone_pcm(self, pcm):
        if self.active and self.session.acked and not self.session.closing:
            self._put(self.to_door, pcm)

    def _first_phone_packet(self):
        self.microphone_seen = True

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self._pump(), name="comelit_sip_audio")

    @staticmethod
    def _frame(queue):
        if len(queue) < 320:
            return bytes(320)
        result = bytes(queue[:320])
        del queue[:320]
        return result

    async def _pump(self):
        deadline = time.monotonic()
        next_report = deadline + 5
        try:
            while self.active and self.session.acked and not self.session.closing:
                if not self.call.owned or self.call.controller is not self.session:
                    return
                if self.offer.direction != "sendonly":
                    payload = encode_g711(self._frame(self.to_phone), self.offer.pt)
                    self.transport.sendto(self.writer.packet(payload, self.offer.pt),
                                          (self.offer.ip, self.offer.port))
                if self.microphone_seen and self.offer.direction != "recvonly":
                    self.call.send_microphone(self._frame(self.to_door), self.session)
                now = time.monotonic()
                if now >= next_report:
                    self._report()
                    next_report = now + 5
                # A delayed event loop drops old frames instead of a catch-up burst.
                deadline = max(deadline + 0.02, now + 0.005)
                await asyncio.sleep(max(0, deadline - time.monotonic()))
        except (OSError, ValueError) as exc:
            self.on_error(str(exc))

    def _report(self):
        if self.rtcp is None:
            return
        ntp = time.time() + 2208988800
        seconds = int(ntp)
        fraction = int((ntp - seconds) * 2**32)
        sr = struct.pack(">BBHIIIIII", 0x80, 200, 6, self.writer.ssrc, seconds,
                         fraction, self.writer.timestamp, self.writer.packets & 0xffffffff,
                         self.writer.octets & 0xffffffff)
        cname = b"comelit-native"
        chunk = struct.pack(">I", self.writer.ssrc) + bytes((1, len(cname))) + cname + b"\0"
        chunk += bytes(-len(chunk) % 4)
        sdes = struct.pack(">BBH", 0x81, 202, len(chunk) // 4) + chunk
        self.rtcp.sendto(sr + sdes, (self.offer.ip, self.offer.rtcp_port))

    def close(self):
        if self.unsubscribe:
            self.unsubscribe()
            self.unsubscribe = None
        if self.task is not None:
            self.task.cancel()
            self.task = None
        if self.rtcp:
            self.rtcp.close()
            self.rtcp = None
        self.to_phone.clear()
        self.to_door.clear()
        super().close()
