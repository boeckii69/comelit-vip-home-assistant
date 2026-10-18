"""Loxone-style no-ring SIP calls with real loopback audio, no home LAN."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import test_sip as sip_fixture
from test_outgoing import EntrancePeer
from test_native import invite, server_packet
from test_audio import rtp
from protocol.client import NativeClient, Settings
from protocol.sip import SipBridge, allowed_addresses
from protocol.sip_media import SipMedia
from protocol.sip_wire import parameter, parse_sdp


class OutgoingSipTests(unittest.IsolatedAsyncioTestCase):
    endpoint = sip_fixture.SipLoopbackTests.endpoint
    send = sip_fixture.SipLoopbackTests.send
    recv_sip = sip_fixture.SipLoopbackTests.recv_sip
    connect = sip_fixture.SipLoopbackTests.connect

    async def asyncSetUp(self):
        self.sockets = []
        self.phone = await self.endpoint("127.0.0.1")
        self.phone_audio = await self.endpoint("127.0.0.1")
        self.native_tx = await self.endpoint("127.0.0.3")
        self.native_rx = await self.endpoint("127.0.0.3")
        settings = Settings(local_ip="127.0.0.1", master_ip="127.0.0.2", entrance_ip="127.0.0.3",
                            loxone_enabled=False, sip_enabled=True, sip_allowed_ips="127.0.0.1")
        self.rings = []
        self.client = NativeClient(settings, self.rings.append, lambda: self.bridge.native_changed())
        self.client.running = self.client.registered = True
        self.client.state = "registered"
        self.client.resolve_entrance = AsyncMock()
        self.client.ctp = self.native_peer = EntrancePeer(self.client, self.native_tx.port)
        self.bridge = SipBridge(self.client, lambda: None)
        self.bridge.allowed_ips = allowed_addresses(settings.sip_allowed_ips)
        self.bridge.state = "listening"
        await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: self.bridge, local_addr=("127.0.0.1", 0))
        self.client.settings.sip_port = self.bridge.local_port
        for name, value in (("protocol.sip.T1", 0.01), ("protocol.call.ACK_TIMEOUT", 0.01),
                            ("protocol.call.OUTGOING_SETUP_TIMEOUT", 0.3)):
            patched = patch(name, value)
            patched.start()
            self.addCleanup(patched.stop)

    async def asyncTearDown(self):
        await self.bridge.stop()
        await self.client.call.stop()
        for transport in self.sockets:
            transport.close()
        await asyncio.sleep(0)

    async def test_no_ring_two_way_audio_door_and_hangup(self):
        tag, media = await self.connect()
        self.assertEqual(self.client.call.direction, "outgoing")
        self.assertEqual(self.bridge.session.generation, self.client.call.generation)
        self.assertEqual(self.rings, [])
        self.assertEqual(self.client.ring_count, 0)
        self.assertIsNone(self.client.last_ring)
        for index in range(10):
            self.native_rx.transport.sendto(rtp(index, index * 160, payload=b"\xa5" * 160),
                                           ("127.0.0.1", self.client.call.audio.port))
            self.phone_audio.transport.sendto(rtp(index, index * 160, payload=b"\x55" * 160),
                                             ("127.0.0.1", media.port))
            await asyncio.sleep(0.02)
        async with asyncio.timeout(1):
            while True:
                data, _ = await self.native_tx.queue.get()
                if data[12:] == b"\x55" * 160:
                    break
        self.assertTrue(self.native_rx.queue.empty())
        heard = False
        while not self.phone_audio.queue.empty():
            data, _ = self.phone_audio.queue.get_nowait()
            heard |= data[12:] == b"\xa5" * 160
        self.assertTrue(heard)
        await self.client.open_door()
        self.assertEqual(self.client.door_state, "transport_acknowledged")
        self.assertEqual(self.bridge.state, "connected")
        self.send("BYE", tag=tag, cseq=2, branch="z9hG4bK-bye")
        await self.recv_sip(200)
        await asyncio.sleep(0.03)
        self.assertIsNone(self.bridge.session)
        self.assertTrue(self.client.call.can_start)
        self.assertTrue(self.client.registered)

    async def test_busy_panel_maps_to_sip_486_and_does_not_redial(self):
        self.native_peer.refusal = 8
        self.send()
        await self.recv_sip(486)
        await asyncio.sleep(0.03)
        self.assertIsNone(self.bridge.session)
        self.assertEqual(self.client.call.release_cause, 8)
        self.assertEqual([p.opcode for p in self.native_peer.commands()], [1])
        self.assertTrue(self.client.call.can_start)

    async def test_cancel_during_native_setup_releases_only_our_session(self):
        self.native_peer.connect = False
        self.send()
        await self.recv_sip(180)
        for _ in range(100):
            if self.client.call.start_acknowledged:
                break
            await asyncio.sleep(0.001)
        self.assertTrue(self.client.call.start_acknowledged)
        self.send("CANCEL")
        await self.recv_sip(200)
        await self.recv_sip(487)
        await asyncio.sleep(0.03)
        self.assertIsNone(self.bridge.session)
        self.assertFalse(self.client.call.owned)
        self.assertFalse(self.client.call.audio.active)
        self.assertEqual([p.opcode for p in self.native_peer.commands()], [1, 3, 0x0e])

    async def test_duplicate_invite_and_second_phone_do_not_start_second_native_call(self):
        data = self.send()
        answer = await self.recv_sip(200)
        self.phone.transport.sendto(data, ("127.0.0.1", self.bridge.local_port))
        repeated = await self.recv_sip(200)
        self.assertEqual(answer.body, repeated.body)
        self.send(call_id="second", branch="z9hG4bK-second")
        await self.recv_sip(486)
        self.assertEqual(sum(p.opcode == 1 for p in self.native_peer.commands()), 1)

    async def test_no_ack_closes_outgoing_call_and_never_sends_microphone(self):
        with patch("protocol.sip.ACK_WAIT", 0.08):
            self.send()
            answer = await self.recv_sip(200)
            media = parse_sdp(answer.body, "127.0.0.1")
            for index in range(5):
                self.phone_audio.transport.sendto(rtp(index, index * 160), ("127.0.0.1", media.port))
            await asyncio.sleep(0.15)
        self.assertEqual(self.client.call.tx_writer.packets, 0)
        self.assertFalse(self.client.call.owned)
        self.assertIsNone(self.bridge.session)

    async def test_real_ring_while_media_opens_is_preserved_and_not_replaced(self):
        gate = asyncio.Event()
        async def slow_open(media, ip):
            await gate.wait()
        with patch.object(SipMedia, "open", slow_open):
            self.send()
            await self.recv_sip(180)
            self.client.receive_ctp(server_packet(0xc0, 0x4913, 6, 255, invite()),
                                    ("127.0.0.2", 52051))
            await self.recv_sip(487)
            await asyncio.sleep(0.03)
        self.assertTrue(self.client.call.can_answer)
        self.assertEqual(self.client.call.direction, "incoming")
        self.assertEqual(len(self.rings), 1)
        self.assertEqual(self.native_peer.commands(), [])

    async def test_options_invalid_offer_and_disallowed_device_never_start_call(self):
        self.send("OPTIONS")
        await self.recv_sip(200)
        self.bridge.datagram_received(sip_fixture.request(), ("127.0.0.9", 5090))
        self.send(body=sip_fixture.offer(ip="192.0.2.40"))
        await self.recv_sip(488)
        self.assertEqual(self.native_peer.commands(), [])
        self.assertFalse(self.client.call.owned)
        self.assertIsNone(self.client.call.audio)

    async def test_failed_native_setup_leaves_station_registered_and_no_media_socket(self):
        self.client.resolve_entrance.side_effect = OSError("CRP failed")
        self.send()
        await self.recv_sip(480)
        await asyncio.sleep(0.03)
        self.assertIsNone(self.bridge.session)
        self.assertEqual(self.native_peer.commands(), [])
        self.assertTrue(self.client.registered)
        self.assertIsNone(self.client.call.audio)

