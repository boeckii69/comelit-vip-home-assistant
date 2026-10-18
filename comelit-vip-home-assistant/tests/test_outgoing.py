"""Explicit outgoing call setup: independent wire vectors and a memory peer."""
import asyncio
import ipaddress
import struct
import unittest
from unittest.mock import AsyncMock, patch

from test_native import Sink, invite, server_packet
from test_door import Transport
from protocol.audio import AudioReceiver
from protocol.call import CallError, CallRefused
from protocol.client import NativeClient, Settings
from protocol.ctp import build_ctp_packet, encode_logaddr, parse_ctp_packet
from protocol.messages import outgoing_call_request


class EntrancePeer:
    def __init__(self, client, media_port=30008):
        self.client, self.media_port = client, media_port
        self.sent = []
        self.ack = self.capabilities = self.connect = True
        self.refusal = None
        self.sequence = 201
        self.reflected_start = True
        self.connection = None
        self.local_ack = 0

    def reply(self, body=b"", flags=0):
        cfg = self.client.settings
        wire = build_ctp_packet(flags=flags, connection=self.connection,
            sequence=self.sequence, acknowledgement=self.local_ack, body=body,
            source=cfg.entrance_address, destination=cfg.source_address)
        if body:
            self.sequence = (self.sequence + 1) & 255
        self.client.receive_ctp(wire, (cfg.entrance_ip, self.client.entrance_port))

    def sendto(self, data, peer=None):
        self.sent.append((data, peer))
        packet = parse_ctp_packet(data)
        if not packet.body or not self.ack:
            return
        self.connection = (int.from_bytes(packet.connection, "big") ^ 0x8000).to_bytes(2, "big")
        self.local_ack = (packet.sequence + 1) & 255
        if packet.opcode == 1 and self.refusal is not None:
            self.reply(b"\0\x0e" + bytes((self.refusal,)), flags=0x60)
            return
        self.reply()
        if packet.opcode == 1 and self.reflected_start:
            self.reply(packet.body, flags=0xc0)
        if packet.opcode == 3:
            if self.capabilities:
                self.reply(bytes.fromhex("000350001b000000"), flags=0x40)
            if self.connect:
                self.reply(b"\0\x0c", flags=0x40)
        if packet.opcode == 0x11:
            body = (bytes.fromhex("00111800")
                    + ipaddress.IPv4Address(self.client.settings.entrance_ip).packed
                    + struct.pack("<H", self.media_port))
            self.reply(body, flags=0x40)

    def close(self):
        pass

    def commands(self):
        return [p for data, _ in self.sent if (p := parse_ctp_packet(data)).body]


class OutgoingWireTests(unittest.TestCase):
    def test_native_start40_fixed_vector(self):
        expected = bytes.fromhex(
            "00013030303030303031310030303030303030320000012011223344"
            "303030303030303131004949")
        body = outgoing_call_request("000000011", "00000002", b"\x11\x22\x33\x44")
        self.assertEqual(body, expected)
        self.assertEqual(len(body), 40)
        for args in (("00000001", "00000002", b"1234"),
                     ("000000010", "00000002", b"1234"),
                     ("000000011", "00000002", b"123"),
                     ("000000011", "00000002", "1234"),
                     ("000000011", "000000021", b"1234")):
            with self.assertRaises(ValueError):
                outgoing_call_request(*args)


class OutgoingCallTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.rings = []
        self.client = NativeClient(Settings(), self.rings.append, lambda: None)
        self.client.running = self.client.registered = True
        self.client.state = "registered"
        self.client.entrance_port = 44721
        self.client.resolve_entrance = AsyncMock()
        self.client.ctp = self.peer = EntrancePeer(self.client)
        self.client.loxone = Sink()
        self.call = self.client.call
        self.call._open_audio = self.open_audio
        self.controller = object()
        self.generations = []
        for name, value in (("ACK_TIMEOUT", 0.01), ("OUTGOING_SETUP_TIMEOUT", 0.12)):
            mocked = patch("protocol.call." + name, value)
            mocked.start()
            self.addCleanup(mocked.stop)

    async def open_audio(self):
        self.call.audio = AudioReceiver([self.client.settings.entrance_ip],
            self.call._on_pcm, self.call._on_first_audio, self.call._on_audio_error)
        self.call.audio.active = False
        self.call.audio.port = 37609
        self.call.audio.transport = Transport()

    async def asyncTearDown(self):
        self.peer.ack = True
        await self.call.stop()
        await asyncio.sleep(0)

    async def start(self):
        await self.call.start(self.controller, self.generations.append)

    async def test_start_accept_speech_media_and_door_share_resolved_panel_connection(self):
        self.client.last_ring = 123
        await self.start()
        commands = self.peer.commands()
        self.assertEqual([p.opcode for p in commands], [1, 3, 0x0c, 0x11])
        self.assertEqual(commands[0].flags, 0xc0)
        self.assertEqual(commands[0].body[22:24], b"\x01\x20")
        self.assertEqual(commands[0].body[38:40], b"II")
        self.assertTrue(all(p.destination == "00000002" for p in commands))
        self.assertTrue(all(peer == ("192.0.2.21", 44721) for _, peer in self.peer.sent))
        self.assertTrue(self.call.start_acknowledged)
        self.assertTrue(self.call.peer_connected)
        self.assertTrue(self.call.connect_acked)
        self.assertEqual(self.call.state, "waiting_audio")
        self.assertEqual(self.call.direction, "outgoing")
        self.assertIsNone(self.call.ring_at)
        self.assertEqual(self.client.last_ring, 123)
        self.assertEqual(self.client.ring_count, 0)
        self.assertEqual(self.rings, [])
        self.assertEqual(self.client.loxone.sent, [])
        self.assertEqual(self.call.tx_endpoint, ("192.0.2.21", 30008))
        self.assertEqual(self.generations, [self.call.generation])
        self.assertFalse(self.call.can_start)
        await self.client.open_door()
        self.assertEqual(self.peer.commands()[-1].opcode, 0x2d)
        self.assertEqual(self.client.door_command_path, "active_call")
        self.assertEqual(self.client.door_state, "transport_acknowledged")
        self.assertTrue(self.call.owned)
        await self.call.hangup(self.controller)
        self.assertEqual(self.peer.commands()[-1].opcode, 0x0e)
        self.assertTrue(self.call.can_start)
        self.assertFalse(self.call.audio.active)
        self.assertTrue(self.client.registered)

    async def test_busy_release_is_reported_without_second_call_or_door_command(self):
        self.peer.refusal = 8
        with self.assertRaises(CallRefused) as error:
            await self.start()
        self.assertEqual(error.exception.cause, 8)
        self.assertEqual(self.call.release_cause, 8)
        self.assertEqual(self.call.state, "failed")
        self.assertEqual([p.opcode for p in self.peer.commands()], [1])
        self.assertFalse(self.call.owned)
        self.assertTrue(self.call.can_start)

    async def test_missing_capabilities_or_connect_times_out_and_releases_own_connection(self):
        for field in ("capabilities", "connect"):
            setattr(self.peer, field, False)
            self.peer.sent.clear()
            with self.subTest(field=field), self.assertRaisesRegex(CallError, "Zeitüberschreitung"):
                await self.start()
            self.assertEqual([p.opcode for p in self.peer.commands()], [1, 3, 0x0e])
            self.assertFalse(self.call.owned)
            self.assertFalse(self.call.audio.active)
            setattr(self.peer, field, True)

    async def test_discovery_failure_never_uses_old_port_or_sends_start(self):
        self.client.resolve_entrance.side_effect = TimeoutError("CRP fehlt")
        with self.assertRaises(CallError):
            await self.start()
        self.assertEqual(self.peer.sent, [])
        self.assertIsNone(self.call.audio)
        self.assertFalse(self.call.owned)

    async def test_missing_start_ack_retries_only_same_request_then_releases(self):
        self.peer.ack = False
        with self.assertRaises(CallError):
            await self.start()
        starts = [p for p in self.peer.commands() if p.opcode == 1]
        self.assertEqual(len(starts), 3)
        self.assertEqual(len({(p.connection, p.sequence, p.body) for p in starts}), 1)
        self.assertFalse(any(p.opcode in (3, 0x0c, 0x11, 0x2d) for p in self.peer.commands()))
        self.assertFalse(self.call.start_acknowledged)
        self.assertFalse(self.call.owned)

    async def test_cancel_during_resolution_cleans_up_without_sending(self):
        gate = asyncio.Event()
        self.client.resolve_entrance.side_effect = gate.wait
        task = asyncio.create_task(self.start())
        await asyncio.sleep(0)
        self.assertEqual(self.call.state, "resolving")
        await self.call.hangup(self.controller)
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.peer.sent, [])
        self.assertFalse(self.call.owned)
        self.assertFalse(self.call._lock.locked())

    async def test_first_peer_sequence_requires_matching_start_ack_and_endpoint(self):
        self.peer.ack = False
        task = asyncio.create_task(self.start())
        await asyncio.sleep(0)
        conn = self.call.conn
        original_expected = conn.expected
        peer_id = (int.from_bytes(conn.local_id, "big") ^ 0x8000).to_bytes(2, "big")
        for peer, ack in ((("192.0.2.21", 12345), conn.sequence),
                          (("192.0.2.21", 44721), (conn.sequence - 1) & 255)):
            wire = build_ctp_packet(flags=0, connection=peer_id, sequence=201,
                acknowledgement=ack, body=b"", source="00000002", destination="000000011")
            self.client.receive_ctp(wire, peer)
        self.assertEqual(conn.expected, original_expected)
        self.assertFalse(conn.peer_confirmed)
        with self.assertRaises(CallError):
            await task
        self.assertFalse(self.call.start_acknowledged)

    async def test_stopping_after_start_ack_releases_setup_without_connect_or_media(self):
        self.peer.connect = False
        task = asyncio.create_task(self.start())
        for _ in range(30):
            if self.call.start_acknowledged:
                break
            await asyncio.sleep(0)
        self.assertTrue(self.call.start_acknowledged)
        await self.call.stop()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual([p.opcode for p in self.peer.commands()], [1, 3, 0x0e])
        self.assertFalse(self.call.owned)
        self.assertFalse(self.call.audio.active)

    async def test_no_start_during_genuine_ring_or_door_transaction(self):
        await self.client.door_lock.acquire()
        with self.assertRaises(CallError):
            await self.start()
        self.client.door_lock.release()
        self.client.receive_ctp(server_packet(0xc0, 0x4913, 6, 255, invite()),
                                ("192.0.2.20", 52051))
        with self.assertRaises(CallError):
            await self.start()
        self.assertTrue(self.call.can_answer)
        self.assertFalse(any(p.opcode == 1 for p in self.peer.commands()))

    async def test_other_user_or_recycled_invite_cannot_steal_owned_connection(self):
        await self.start()
        original = self.call.conn
        with self.assertRaises(CallError):
            await self.call.start(object(), lambda _: None)
        self.peer.reply(outgoing_call_request("000000011", "00000002", b"xxxx"), flags=0xc0)
        self.assertIs(self.client.connections[original.local_id], original)
        self.assertIs(self.call.conn, original)
        self.assertTrue(self.call.owned)

