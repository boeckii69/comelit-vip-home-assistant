"""Call negotiation with a memory-only CTP peer; no real doorstation traffic."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from test_native import Sink, invite, server_packet
from test_audio import rtp
from protocol.audio import AudioReceiver
from protocol.call import CallError
from protocol.client import NativeClient, Settings
from protocol.ctp import parse_ctp_packet


class FakeTransport(Sink):
    def __init__(self, client):
        super().__init__()
        self.client = client
        self.ack = True

    def close(self):
        pass

    def sendto(self, data, addr=None):
        super().sendto(data, addr)
        packet = parse_ctp_packet(data)
        if packet.body and self.ack:
            self.client.receive_ctp(server_packet(0, 0x4913, packet.acknowledgement,
                (packet.sequence + 1) & 255, b""), addr)


class CallTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.rings = []
        self.client = NativeClient(Settings(), self.rings.append, lambda: None)
        self.client.running = self.client.registered = True
        self.client.state = "registered"
        self.transport = self.client.ctp = FakeTransport(self.client)
        self.client.loxone = Sink()
        self.peer = ("192.0.2.20", 52051)
        self.call = self.client.call
        self.receiver = None
        self.call._open_audio = self.open_audio
        self.timeout = patch("protocol.call.ACK_TIMEOUT", 0.01)
        self.timeout.start()
        self.addCleanup(self.timeout.stop)
        self.invite()

    async def asyncTearDown(self):
        if self.call.owned:
            self.transport.ack = True
            await self.call.hangup()
        self.call.finish("ended", "test cleanup")
        await asyncio.sleep(0)

    async def open_audio(self):
        self.receiver = AudioReceiver(["192.0.2.21"], self.call._on_pcm,
                                      self.call._on_first_audio, self.call._on_audio_error)
        self.receiver.port = 24108
        self.receiver.active = False
        self.call.audio = self.receiver

    def invite(self, call_id=b"\x11\x22\x33\x44", cid=0x4913):
        self.client.receive_ctp(server_packet(0xc0, cid, 6, 255, invite(call_id=call_id)), self.peer)

    def bodies(self):
        return [p.body for data, _ in self.transport.sent if (p := parse_ctp_packet(data)).body]

    async def test_passive_ring_and_hangup_do_not_claim_or_cancel(self):
        self.assertEqual(self.call.state, "ringing")
        self.assertTrue(self.call.can_answer)
        self.assertEqual(self.bodies(), [])
        self.assertEqual(len(self.rings), 1)
        with self.assertRaises(CallError):
            await self.call.hangup()
        self.assertEqual(self.bodies(), [])

    async def test_answer_audio_and_acknowledged_release_preserve_registration(self):
        pcm = []
        unsubscribe = self.call.subscribe_audio(pcm.append)
        await self.call.answer()
        self.assertEqual(self.bodies(), [bytes.fromhex(h) for h in
            ("000a00", "0003490003000000", "000c", "00111800c000020a2c5e7805ee020000")])
        self.assertEqual(self.call.control_acked, 4)
        self.assertEqual(self.call.state, "waiting_audio")
        for n in range(5):
            self.receiver.datagram_received(rtp(n, n * 160), ("192.0.2.21", 24108))
        self.assertEqual(self.call.state, "audio_receiving")
        self.assertEqual(len(pcm), 1)
        with self.assertRaises(CallError):
            await self.call.answer()
        await self.call.hangup()
        self.assertEqual(self.bodies()[-1], b"\0\x0e\0")
        self.assertEqual(self.call.control_acked, 5)
        self.assertFalse(self.receiver.active)
        self.assertEqual(self.call.state, "ended")
        self.assertTrue(self.client.registered)
        self.assertEqual(self.client.state, "registered")
        self.assertNotIn(b"\0\x2d", [body[:2] for body in self.bodies()])
        unsubscribe()

    async def test_wrong_peer_and_ack_cannot_advance_retry_sequence(self):
        self.transport.ack = False
        task = asyncio.create_task(self.call.answer())
        await asyncio.sleep(0)
        self.client.receive_ctp(server_packet(0, 0x4913, 7, 0, b""), (self.peer[0], 12345))
        self.client.receive_ctp(server_packet(0, 0x4913, 7, 255, b""), self.peer)
        with self.assertRaisesRegex(CallError, "0x000a"):
            await task
        controls = [parse_ctp_packet(d) for d, _ in self.transport.sent if parse_ctp_packet(d).body]
        self.assertEqual([p.sequence for p in controls], [255, 255, 255])
        self.assertEqual(self.call.control_acked, 0)
        self.assertFalse(self.receiver.active)
        self.assertTrue(self.client.registered)
        self.assertEqual(self.call.state, "failed")

    async def test_remote_release_during_answer_closes_audio_and_no_late_connect(self):
        self.transport.ack = False
        task = asyncio.create_task(self.call.answer())
        await asyncio.sleep(0)
        self.client.receive_ctp(server_packet(0x60, 0x4913, 7, 0, b"\0\x0e\x0a"), self.peer)
        with self.assertRaises(CallError):
            await task
        self.assertFalse(self.receiver.active)
        self.assertFalse(self.call.owned)
        self.assertNotIn(b"\0\x0c", self.bodies())
        self.assertEqual(self.call.state, "ended")

    async def test_expiry_and_missing_capability_do_not_send_commands(self):
        self.call.capabilities = 4
        with self.assertRaises(CallError):
            await self.call.answer()
        self.assertEqual(self.bodies(), [])
        self.call._expire_ring(self.call.generation)
        self.assertEqual(self.call.state, "missed")
        with self.assertRaises(CallError):
            await self.call.answer()
        self.assertEqual(self.bodies(), [])

    async def test_second_call_does_not_replace_owned_connection(self):
        await self.call.answer()
        conn = self.call.conn
        self.invite(b"\xaa\xbb\xcc\xdd", 0x4914)
        self.assertIs(self.call.conn, conn)
        self.assertEqual(self.call.call_id, b"\x11\x22\x33\x44")

    async def test_cancel_pending_answer_releases_resources_without_reconnect(self):
        self.transport.ack = False
        task = asyncio.create_task(self.call.answer())
        await asyncio.sleep(0)
        await self.call.hangup()
        self.assertTrue(task.cancelled())
        self.assertFalse(self.call.owned)
        self.assertFalse(self.receiver.active)
        self.assertEqual(self.bodies(), [b"\0\x0a\0"])

    async def test_no_audio_timeout_releases_call(self):
        with patch("protocol.call.AUDIO_START_TIMEOUT", 0.01):
            await self.call.answer()
            await asyncio.sleep(1.05)
        self.assertEqual(self.call.state, "failed")
        self.assertIn("Keine G.711", self.call.error)
        self.assertFalse(self.receiver.active)
        self.assertEqual(self.bodies()[-1], b"\0\x0e\0")

    async def test_remote_close_during_socket_creation_does_not_leak_socket(self):
        # Exercise the real _open_audio path with a paused memory-only endpoint.
        self.call._open_audio = type(self.call)._open_audio.__get__(self.call)
        receiver_seen = []
        async def endpoint(factory, **kw):
            receiver = factory()
            receiver_seen.append(receiver)
            self.call.finish("ended", "remote end")
            return None, receiver
        with patch.object(asyncio.get_running_loop(), "create_datagram_endpoint", side_effect=endpoint):
            with self.assertRaises(CallError):
                await self.call.answer()
        self.assertFalse(receiver_seen[0].active)
        self.assertEqual(self.bodies(), [])
