"""SIP lifecycle plus actual loopback RTP in both directions. Never uses the LAN."""
import asyncio
import ipaddress
import struct
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from test_native import invite as native_invite, server_packet
from test_audio import rtp
from protocol.client import NativeClient, Settings
from protocol.ctp import parse_ctp_packet
from protocol.sip import SipBridge, allowed_addresses
from protocol.sip_media import SipMedia
from protocol.sip_wire import SipError, parameter, parse_sdp, parse_sip, response, sdp_answer


def offer(ip="127.0.0.1", port=31000, formats="8 0", proto="RTP/AVP"):
    return (f"v=0\r\no=phone 1 1 IN IP4 {ip}\r\ns=Phone\r\nc=IN IP4 {ip}\r\n"
            f"t=0 0\r\nm=audio {port} {proto} {formats}\r\na=sendrecv\r\na=ptime:20\r\n").encode()


def request(method="INVITE", *, body=None, sip_port=5060, phone_port=5090,
            tag="", cseq=1, call_id="phone-call", branch="z9hG4bK-test", user="comelit", extra=""):
    if body is None:
        body = offer() if method == "INVITE" else b""
    headers = [f"{method} sip:{user}@127.0.0.1:{sip_port} SIP/2.0",
               f"Via: SIP/2.0/UDP 127.0.0.1:{phone_port};branch={branch};rport",
               "From: <sip:phone@127.0.0.1>;tag=phone-tag",
               "To: <sip:comelit@127.0.0.1>" + (";tag=" + tag if tag else ""),
               "Call-ID: " + call_id, f"CSeq: {cseq} {method}", "Max-Forwards: 70",
               f"Contact: <sip:phone@127.0.0.1:{phone_port}>"]
    if body:
        headers.append("Content-Type: application/sdp")
    if extra:
        headers.append(extra)
    headers.append(f"Content-Length: {len(body)}")
    return ("\r\n".join(headers) + "\r\n\r\n").encode() + body


class SipWireTests(unittest.TestCase):
    def test_compact_headers_length_cseq_and_response_route(self):
        data = request().replace(b"Via:", b"v:").replace(b"Content-Length:", b"l:")
        msg = parse_sip(data)
        self.assertEqual(msg.cseq, 1)
        wire = response(msg, 200, ("127.0.0.1", 5091), "local-tag")
        self.assertIn(b";received=127.0.0.1;rport=5091\r\n", wire)
        self.assertEqual(parameter(parse_sip(wire).get("to"), "tag"), "local-tag")
        for malformed in (data[:-1], data.replace(b"1 INVITE", b"1 BYE"),
                          data + b"extra", data.replace(b"CSeq:", b"From: duplicate\r\nCSeq:"),
                          data.replace(b"SIP/2.0/UDP", b"SIP/2.0/TCP"), b"x" * 9000):
            with self.assertRaises(ValueError):
                parse_sip(malformed)

    def test_sdp_pcma_preference_and_rejected_video_line(self):
        data = offer(formats="0 8 101") + b"m=video 32000 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n"
        parsed = parse_sdp(data, "127.0.0.1")
        self.assertEqual(parsed.pt, 8)
        answer = sdp_answer(parsed, "127.0.0.2", 41000, 41001)
        self.assertIn(b"m=audio 41000 RTP/AVP 8\r\n", answer)
        self.assertIn(b"m=video 0 RTP/AVP 96\r\n", answer)
        self.assertEqual(parse_sdp(offer(formats="0"), "127.0.0.1").pt, 0)

    def test_sdp_rejects_redirect_secure_audio_and_incompatible_codec(self):
        for data in (offer(ip="192.0.2.40"), offer(proto="RTP/SAVP"), offer(formats="111"),
                     offer(port=0), offer(formats="8") + b"a=rtpmap:8 opus/48000/2\r\n",
                     offer() + b"a=rtcp:31001 IN IP4 192.0.2.40\r\n",
                     offer() + b"a=maxptime:10\r\n"):
            with self.assertRaises(ValueError):
                parse_sdp(data, "127.0.0.1")

    def test_allowlist_requires_exact_ips_and_upgrade_defaults_disabled(self):
        self.assertEqual(allowed_addresses("192.0.2.50,192.0.2.51"),
                         frozenset(("192.0.2.50", "192.0.2.51")))
        for value in ("0.0.0.0", "192.0.2.0/24", "example.com", "239.0.0.1"):
            with self.assertRaises(ValueError):
                allowed_addresses(value)
        self.assertFalse(Settings().sip_enabled)
        with self.assertRaises(ValueError):
            Settings(sip_enabled=True).validate()


class QueueProtocol(asyncio.DatagramProtocol):
    def __init__(self):
        self.queue = asyncio.Queue()

    def connection_made(self, transport):
        self.transport = transport
        self.port = transport.get_extra_info("sockname")[1]

    def datagram_received(self, data, peer):
        self.queue.put_nowait((data, peer))


class NativeTransport:
    def __init__(self, client, tx_port):
        self.client, self.tx_port = client, tx_port
        self.sent = []

    def sendto(self, data, peer=None):
        self.sent.append(data)
        packet = parse_ctp_packet(data)
        if not packet.body:
            return
        ack = (packet.sequence + 1) & 255
        self.client.receive_ctp(server_packet(0, 0x4913, packet.acknowledgement, ack, b""), peer)
        if packet.opcode == 0x11:
            body = bytes.fromhex("00111800") + ipaddress.IPv4Address("127.0.0.2").packed + struct.pack("<H", self.tx_port)
            self.client.receive_ctp(server_packet(0x40, 0x4913, packet.acknowledgement, ack, body), peer)

    def close(self):
        pass


class SipLoopbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sockets = []
        self.phone = await self.endpoint("127.0.0.1")
        self.phone_audio = await self.endpoint("127.0.0.1")
        self.native_tx = await self.endpoint("127.0.0.2")
        self.native_rx = await self.endpoint("127.0.0.2")
        settings = Settings(local_ip="127.0.0.1", master_ip="127.0.0.2", entrance_ip="127.0.0.3",
                            loxone_enabled=False, sip_enabled=True, sip_allowed_ips="127.0.0.1")
        self.client = NativeClient(settings, lambda event: None, lambda: self.bridge.native_changed())
        self.client.running = self.client.registered = True
        self.client.state = "registered"
        self.client.ctp = NativeTransport(self.client, self.native_tx.port)
        self.bridge = SipBridge(self.client, lambda: None)
        self.bridge.allowed_ips = allowed_addresses(settings.sip_allowed_ips)
        self.bridge.state = "listening"
        await asyncio.get_running_loop().create_datagram_endpoint(lambda: self.bridge, local_addr=("127.0.0.1", 0))
        self.client.settings.sip_port = self.bridge.local_port
        self.peer = ("127.0.0.2", 52051)
        self.client.receive_ctp(server_packet(0xc0, 0x4913, 6, 255, native_invite()), self.peer)
        self.timer = patch("protocol.sip.T1", 0.02)
        self.timer.start()
        self.addCleanup(self.timer.stop)

    async def endpoint(self, ip):
        proto = QueueProtocol()
        await asyncio.get_running_loop().create_datagram_endpoint(lambda: proto, local_addr=(ip, 0))
        self.sockets.append(proto.transport)
        return proto

    async def asyncTearDown(self):
        await self.bridge.stop()
        await self.client.call.stop()
        for transport in self.sockets:
            transport.close()
        await asyncio.sleep(0)

    async def test_options_restart_rebinds_same_udp_port(self):
        port = self.bridge.local_port
        for _ in range(3):
            await self.bridge.stop()
            await self.bridge.start()
            self.assertEqual(self.bridge.state, "listening", self.bridge.error)
            self.assertEqual(self.bridge.local_port, port)
            self.assertTrue(self.client.registered)

    def send(self, method="INVITE", **kw):
        if method == "INVITE" and "body" not in kw:
            kw["body"] = offer(port=self.phone_audio.port)
        data = request(method, sip_port=self.bridge.local_port, phone_port=self.phone.port, **kw)
        self.phone.transport.sendto(data, ("127.0.0.1", self.bridge.local_port))
        return data

    async def recv_sip(self, code=None, method=None):
        async with asyncio.timeout(2):
            while True:
                data, peer = await self.phone.queue.get()
                msg = parse_sip(data)
                if (code is None or msg.status == code) and (method is None or msg.method == method):
                    return msg

    async def connect(self):
        self.send()
        reply = await self.recv_sip(200)
        tag = parameter(reply.get("to"), "tag")
        self.send("ACK", tag=tag, branch="z9hG4bK-ack")
        await asyncio.sleep(0.03)
        self.assertEqual(self.bridge.state, "connected")
        return tag, parse_sdp(reply.body, "127.0.0.1")

    async def test_bidirectional_audio_uses_announced_native_tx_port_and_hangup(self):
        tag, media = await self.connect()
        for index in range(10):
            self.native_rx.transport.sendto(rtp(index, index * 160, payload=b"\xa5" * 160),
                ("127.0.0.1", self.client.call.audio.port))
            self.phone_audio.transport.sendto(rtp(index, index * 160, payload=b"\x55" * 160),
                ("127.0.0.1", media.port))
            await asyncio.sleep(0.02)
        spoke = False
        async with asyncio.timeout(1):
            while not spoke:
                native_data, _ = await self.native_tx.queue.get()
                self.assertEqual(native_data[1] & 127, 8)
                spoke = native_data[12:] == b"\x55" * 160
        self.assertTrue(self.native_rx.queue.empty())  # Do not send to the RX source port.
        heard = False
        while not self.phone_audio.queue.empty():
            data, _ = self.phone_audio.queue.get_nowait()
            heard |= data[12:] == b"\xa5" * 160
        self.assertTrue(heard)
        self.assertGreater(self.client.call.tx_writer.packets, 0)
        self.send("BYE", tag=tag, cseq=2, branch="z9hG4bK-bye")
        await self.recv_sip(200)
        await asyncio.sleep(0.05)
        self.assertFalse(self.client.call.owned)
        self.assertIsNone(self.bridge.session)
        bodies = [parse_ctp_packet(data).body for data in self.client.ctp.sent]
        self.assertIn(b"\0\x0e\0", bodies)
        self.assertNotIn(b"\0\x2d", [body[:2] for body in bodies])
        self.assertTrue(self.client.registered)

    async def test_repeated_invite_reuses_answer_and_never_reanswers_native_call(self):
        data = self.send()
        first = await self.recv_sip(200)
        count = sum(parse_ctp_packet(d).body == b"\0\x0c" for d in self.client.ctp.sent)
        self.phone.transport.sendto(data, ("127.0.0.1", self.bridge.local_port))
        second = await self.recv_sip(200)
        self.assertEqual(first.get("to"), second.get("to"))
        self.assertEqual(first.body, second.body)
        self.assertEqual(sum(parse_ctp_packet(d).body == b"\0\x0c" for d in self.client.ctp.sent), count)
        self.assertEqual(count, 1)

    async def test_no_ack_releases_and_no_microphone_before_ack(self):
        with patch("protocol.sip.ACK_WAIT", 0.12):
            self.send()
            answer = await self.recv_sip(200)
            media = parse_sdp(answer.body, "127.0.0.1")
            for index in range(5):
                self.phone_audio.transport.sendto(rtp(index, index * 160), ("127.0.0.1", media.port))
            await asyncio.sleep(0.22)
        self.assertEqual(self.client.call.tx_writer.packets, 0)
        self.assertFalse(self.client.call.owned)
        self.assertIsNone(self.bridge.session)

    async def test_unregistered_no_sip_call_or_audio_socket(self):
        self.client.call.finish("ended", "No ring")
        self.client.registered = False
        self.send()
        await self.recv_sip(480)
        self.assertIsNone(self.bridge.session)
        self.assertIsNone(self.client.call.audio)

    async def test_unlisted_ip_and_media_redirect_are_rejected_before_answer(self):
        self.bridge.datagram_received(request(), ("127.0.0.9", 5090))
        self.assertEqual(self.bridge.rejected, 1)
        self.assertIsNone(self.bridge.session)
        self.send(body=offer(ip="192.0.2.40"))
        await self.recv_sip(488)
        self.assertFalse(self.client.call.owned)

    async def test_cancel_while_media_socket_opens_never_claims_native_call(self):
        gate = asyncio.Event()
        async def slow_open(media, ip):
            await gate.wait()
        with patch.object(SipMedia, "open", slow_open):
            self.send()
            await self.recv_sip(180)
            self.send("CANCEL")
            await self.recv_sip(200)
            await self.recv_sip(487)
            await asyncio.sleep(0.03)
        self.assertFalse(self.client.call.owned)
        self.assertTrue(self.client.call.can_answer)
        self.assertIsNone(self.bridge.session)

    async def test_wrong_dialog_bye_is_rejected_and_comelit_end_sends_bye(self):
        tag, _ = await self.connect()
        self.send("BYE", tag="wrong", cseq=2, branch="z9hG4bK-wrong")
        await self.recv_sip(481)
        self.assertTrue(self.client.call.owned)
        self.client.call.finish("ended", "Comelit ended")
        bye = await self.recv_sip(method="BYE")
        self.assertEqual(parameter(bye.get("from"), "tag"), tag)
        self.phone.transport.sendto(response(bye, 200, ("127.0.0.1", self.bridge.local_port)),
                                    ("127.0.0.1", self.bridge.local_port))
        await asyncio.sleep(0.03)
        self.assertIsNone(self.bridge.session)
        self.assertEqual(self.bridge.bye_waiters, {})

    async def test_busy_other_invite_does_not_replace_first_call(self):
        await self.connect()
        original = self.bridge.session
        self.send(call_id="second", branch="z9hG4bK-second")
        await self.recv_sip(486)
        self.assertIs(self.bridge.session, original)

    async def test_pcmu_offer_works_and_wrong_codec_never_reaches_microphone(self):
        self.send(body=offer(port=self.phone_audio.port, formats="0"))
        reply = await self.recv_sip(200)
        media = parse_sdp(reply.body, "127.0.0.1")
        self.assertEqual(media.pt, 0)
        self.send("ACK", tag=parameter(reply.get("to"), "tag"), branch="z9hG4bK-ack")
        await asyncio.sleep(0.03)
        for i in range(5):
            self.phone_audio.transport.sendto(rtp(i, i * 160, pt=8), ("127.0.0.1", media.port))
        await asyncio.sleep(0.03)
        self.assertEqual(self.bridge.session.media.packets, 0)

    async def test_socket_failure_keeps_native_registration_and_ring(self):
        with patch.object(SipMedia, "open", side_effect=OSError("ports exhausted")):
            self.send()
            await self.recv_sip(480)
            await asyncio.sleep(0.03)
        self.assertTrue(self.client.registered)
        self.assertTrue(self.client.call.can_answer)
        self.assertIsNone(self.bridge.session)

    async def test_stale_controller_stop_request_and_ended_call_cannot_send_microphone(self):
        tag, _ = await self.connect()
        controller = self.bridge.session
        call = self.client.call
        self.native_rx.transport.sendto(rtp(), ("127.0.0.1", call.audio.port))
        await asyncio.sleep(0.02)
        pcm = bytes(320)
        self.assertFalse(call.send_microphone(pcm, object()))
        self.assertTrue(call.send_microphone(pcm, controller))
        conn = call.conn
        stop = bytes.fromhex("00119800") + bytes(6)
        self.client.receive_ctp(server_packet(0x40, 0x4913, conn.expected, conn.sequence, stop), self.peer)
        self.assertIsNone(call.tx_endpoint)
        self.assertFalse(call.send_microphone(pcm, controller))
        call.finish("ended", "stop")
        self.assertFalse(call.send_microphone(pcm, controller))

    async def test_options_never_answers_and_disabled_bridge_never_binds(self):
        self.send("OPTIONS")
        await self.recv_sip(200)
        self.assertFalse(self.client.call.owned)
        self.assertIsNone(self.bridge.session)
        self.client.settings.sip_enabled = False
        extra = SipBridge(self.client, lambda: None)
        await extra.start()
        self.assertEqual(extra.state, "disabled")
        self.assertIsNone(extra.transport)

    async def test_cancel_second_media_socket_allocation_closes_first_socket(self):
        loop = asyncio.get_running_loop()
        real_endpoint = loop.create_datagram_endpoint
        gate = asyncio.Event()
        opened = []
        session = NS(offer=parse_sdp(offer(port=self.phone_audio.port), "127.0.0.1"),
                     closing=False, acked=False)
        media = SipMedia(session, self.client.call, lambda error: None)
        async def endpoint(factory, **kw):
            if opened:
                await gate.wait()
            result = await real_endpoint(factory, **kw)
            opened.append(result[0])
            return result
        with patch.object(loop, "create_datagram_endpoint", side_effect=endpoint):
            task = asyncio.create_task(media.open("127.0.0.1"))
            for _ in range(20):
                if opened: break
                await asyncio.sleep(0)
            self.assertTrue(opened)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(opened[0].is_closing())
        self.assertIsNone(media.transport)
