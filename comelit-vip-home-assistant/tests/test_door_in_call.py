"""Native in-call unlock regression: memory transports, no physical actuation."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from test_native import Sink, invite, server_packet
from test_audio import rtp
from test_call import FakeTransport
from test_door import Transport
from protocol.audio import AudioReceiver
from protocol.client import DoorCommandError, NativeClient, Settings
from protocol.ctp import parse_ctp_packet


class InCallDoorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = NativeClient(Settings(), lambda _: None, lambda: None)
        self.client.running = self.client.registered = True
        self.client.state = "registered"
        self.transport = self.client.ctp = FakeTransport(self.client)
        self.client.loxone = Sink()
        self.client.resolve_entrance = AsyncMock()
        self.peer = ("192.0.2.20", 52051)
        self.call = self.client.call
        self.call._open_audio = self.open_audio
        for name in ("ACK_TIMEOUT", "DOOR_ACK_TIMEOUT"):
            patched = patch("protocol.call." + name, 0.05)
            patched.start()
            self.addCleanup(patched.stop)
        self.client.receive_ctp(server_packet(0xc0, 0x4913, 6, 255,
                                invite(call_id=b"\x11\x22\x33\x44")), self.peer)
        await self.call.answer(controller=object())
        self.transport.sent.clear()

    async def asyncTearDown(self):
        self.call.finish("ended", "test cleanup")
        await asyncio.sleep(0)

    async def open_audio(self):
        self.receiver = AudioReceiver([self.peer[0]], self.call._on_pcm,
                                      self.call._on_first_audio, self.call._on_audio_error)
        self.receiver.port = 37609
        self.receiver.active = False
        self.receiver.transport = Transport()
        self.call.audio = self.receiver

    def packets(self):
        return [parse_ctp_packet(data) for data, _ in self.transport.sent]

    def ack(self, *, port=None, ack=None, body=b"", flags=0):
        self.client.receive_ctp(server_packet(flags, 0x4913, self.call.conn.expected,
            self.call.conn.sequence if ack is None else ack, body),
            self.peer if port is None else (self.peer[0], port))

    async def test_same_call_one_command_no_syn_fin_release_and_audio_continues(self):
        call_conn, controller = self.call.conn, self.call.controller
        before_sequence = call_conn.sequence
        pcm = []
        self.call.subscribe_audio(pcm.append)
        await self.client.open_door()
        packets = self.packets()
        self.assertEqual(len(packets), 1)
        packet = packets[0]
        self.assertEqual(packet.flags, 0x40)
        self.assertEqual(packet.connection, call_conn.local_id)
        self.assertEqual(packet.sequence, before_sequence)
        self.assertEqual(packet.body, bytes.fromhex("002d3030303030303032000001"))
        self.assertEqual(packet.source, "000000011")
        self.assertEqual(packet.destination, "00000001")
        self.assertEqual(self.transport.sent[0][1], self.peer)
        self.client.resolve_entrance.assert_not_awaited()
        self.assertIs(self.call.conn, call_conn)
        self.assertIs(self.call.controller, controller)
        self.assertTrue(self.call.owned)
        self.assertFalse(call_conn.closing)
        self.assertTrue(self.receiver.active)
        for n in range(5):
            self.receiver.datagram_received(rtp(n, n * 160), (self.peer[0], 50076))
        self.assertEqual(len(pcm), 1)
        self.assertEqual(self.call.state, "audio_receiving")
        self.assertEqual(self.client.door_state, "transport_acknowledged")
        self.assertEqual(self.client.door_command_path, "active_call")
        self.assertEqual(self.client.door_call_id, "11223344")
        self.assertEqual(self.client.door_command_peer, "192.0.2.20:52051")
        self.assertTrue(self.client.door_acknowledged)
        self.assertIsNone(self.client.door_release_cause)
        self.assertIsNone(self.client.last_door_confirmation)
        self.assertTrue(self.client.registered)

    async def test_selected_relay_and_sequence_wrap(self):
        self.client.settings.door_relay = 2
        self.call.conn.sequence = 255
        await self.client.open_door()
        self.assertEqual(self.packets()[0].body[-1], 2)
        self.assertEqual(self.packets()[0].sequence, 255)
        self.assertEqual(self.call.conn.sequence, 0)

    async def test_ack_timeout_never_retries_or_opens_a_separate_connection(self):
        self.transport.ack = False
        with self.assertRaises(DoorCommandError):
            await self.client.open_door()
        self.assertEqual(len(self.packets()), 1)
        self.assertEqual(self.client.door_state, "unconfirmed")
        self.assertEqual(self.client.door_command_count, 1)
        self.assertFalse(self.client.door_acknowledged)
        self.assertTrue(self.call.owned)
        self.assertFalse(self.call.conn.closing)
        self.client.resolve_entrance.assert_not_awaited()

    async def test_wrong_peer_and_old_ack_do_not_confirm_command(self):
        self.transport.ack = False
        task = asyncio.create_task(self.client.open_door())
        await asyncio.sleep(0)
        self.ack(port=12345)
        self.ack(ack=(self.call.conn.sequence - 1) & 255)
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.assertFalse(self.client.door_acknowledged)
        self.ack()
        await task
        self.assertTrue(self.client.door_acknowledged)

    async def test_concurrent_hangup_waits_for_door_ack_without_overwriting_waiter(self):
        self.transport.ack = False
        door = asyncio.create_task(self.client.open_door())
        await asyncio.sleep(0)
        hangup = asyncio.create_task(self.call.hangup())
        await asyncio.sleep(0)
        self.assertEqual([p.opcode for p in self.packets()], [0x2d])
        self.transport.ack = True
        self.ack()
        await asyncio.gather(door, hangup)
        self.assertEqual([p.opcode for p in self.packets() if p.body], [0x2d, 0x0e])
        self.assertEqual(self.client.door_state, "transport_acknowledged")
        self.assertFalse(self.call.owned)

    async def test_remote_release_is_call_end_not_unlock_confirmation(self):
        self.transport.ack = False
        task = asyncio.create_task(self.client.open_door())
        await asyncio.sleep(0)
        self.ack(body=b"\0\x0e\x08", flags=0x40)
        with self.assertRaises(DoorCommandError):
            await task
        self.assertFalse(self.call.owned)
        self.assertEqual(self.client.door_state, "unconfirmed")
        self.assertIsNone(self.client.last_door_confirmation)
        self.assertIsNone(self.client.door_release_cause)
        self.assertEqual(len([p for p in self.packets() if p.opcode == 0x2d]), 1)

    async def test_cancelled_request_never_hangs_up_or_retries(self):
        self.transport.ack = False
        task = asyncio.create_task(self.client.open_door())
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(self.call.owned)
        self.assertEqual(self.client.door_state, "unconfirmed")
        self.assertEqual([p.opcode for p in self.packets()], [0x2d])
        self.assertFalse(self.client.door_lock.locked())
        self.assertFalse(self.call._control_lock.locked())

    async def test_unready_call_and_other_entrance_never_fall_back(self):
        for state, origin in (("answering", "00000002"),
                              ("ending", "00000002"), ("waiting_audio", "00000003")):
            self.client._last_door_attempt = None
            self.call.state, self.call.origin = state, origin
            with self.subTest(state=state, origin=origin), self.assertRaises(DoorCommandError):
                await self.client.open_door()
        self.assertEqual(self.packets(), [])
        self.client.resolve_entrance.assert_not_awaited()

    async def test_call_ending_while_door_waits_for_control_lock_sends_nothing(self):
        await self.call._control_lock.acquire()
        task = asyncio.create_task(self.client.open_door())
        await asyncio.sleep(0)
        self.call.finish("ended", "remote end")
        self.call._control_lock.release()
        with self.assertRaises(DoorCommandError):
            await task
        self.assertEqual(self.packets(), [])
        self.assertEqual(self.client.door_state, "failed")
        self.client.resolve_entrance.assert_not_awaited()

    async def test_answer_during_discovery_switches_to_active_call(self):
        self.call.owned = False
        async def answer_during_discovery():
            self.call.owned = True
        self.client.resolve_entrance.side_effect = answer_during_discovery
        await self.client.open_door()
        self.assertEqual(self.client.door_command_path, "active_call")
        self.assertEqual([p.flags for p in self.packets()], [0x40])
        self.assertFalse(self.call.conn.closing)

    async def test_repeated_and_parallel_user_presses_are_bounded(self):
        self.transport.ack = False
        task = asyncio.create_task(self.client.open_door())
        await asyncio.sleep(0)
        with self.assertRaisesRegex(DoorCommandError, "bereits"):
            await self.client.open_door()
        self.ack()
        await task
        with self.assertRaisesRegex(DoorCommandError, "zwei Sekunden"):
            await self.client.open_door()
        self.assertEqual(self.client.door_command_count, 1)

