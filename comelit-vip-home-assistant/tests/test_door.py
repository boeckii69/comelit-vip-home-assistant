"""Door-release wire and lifecycle checks. All transports are memory sinks."""
import asyncio
import struct
import unittest
from unittest.mock import AsyncMock, patch

from test_native import ENTRANCE, MASTER, OWN, Sink, invite, server_packet
from protocol.client import DoorCommandError, NativeClient, Settings
from protocol.messages import open_door_request


class Transport(Sink):
    def close(self):
        pass


class DoorWireTests(unittest.TestCase):
    def test_native_thirteen_byte_body(self):
        # Independent fixed vector from native csp_send_opendoor_onconn layout.
        self.assertEqual(open_door_request("00000002", 1),
                         bytes.fromhex("002d3030303030303032000001"))
        self.assertEqual(open_door_request("00000002", 2)[-1], 2)
        for relay in (0, 3, 256, -1, True, "1"):
            with self.subTest(relay=relay), self.assertRaises(ValueError):
                open_door_request("00000002", relay)

    def test_old_entry_defaults_without_migration(self):
        self.assertEqual(Settings(**{"local_ip": "192.0.2.10"}).door_relay, 1)


class DoorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.rings = []
        self.client = NativeClient(Settings(), self.rings.append, lambda: None)
        self.client.running = self.client.registered = True
        self.client.state = "registered"
        self.client.local_port = 43017
        self.client.ctp = Transport()
        self.client.crp = Transport()
        self.client.loxone = Transport()
        self.client.entrance_port = 54321
        self.peer = ("192.0.2.21", 54321)
        self.client.resolve_entrance = AsyncMock()
        self.random = patch("protocol.client.secrets.randbelow", side_effect=[0x1233, 255, 7])
        self.random.start()
        self.addCleanup(self.random.stop)
        self.timeout = patch("protocol.client.DOOR_RESPONSE_TIMEOUT", 0.05)
        self.timeout.start()
        self.addCleanup(self.timeout.stop)

    async def start_command(self):
        task = asyncio.create_task(self.client.open_door())
        await asyncio.sleep(0)
        return task

    def answer(self, body=b"\0\x0e\0", *, flags=0x60, ack=0, seq=7,
               cid=0x9234, source=ENTRANCE, peer=None):
        self.client.receive_ctp(server_packet(flags, cid, seq, ack, body,
                               source=source), peer or self.peer)

    def commands(self):
        return [(data, peer) for data, peer in self.client.ctp.sent if data[8:10] == b"\0\x2d"]

    async def test_one_unicast_request_and_matching_success_with_sequence_wrap(self):
        task = await self.start_command()
        expected = (bytes.fromhex("c0181234ff07000d002d3030303030303032000001")
                    + bytes(3) + b"\xff" * 4 + OWN + ENTRANCE)
        self.assertEqual(self.commands(), [(expected, self.peer)])
        self.answer(body=b"", flags=0)
        self.assertTrue(self.client.door_acknowledged)
        self.assertFalse(task.done())
        self.answer()
        await task
        self.assertEqual(self.client.door_state, "confirmed")
        self.assertEqual(self.client.door_release_cause, 0)
        self.assertIsNotNone(self.client.last_door_confirmation)
        self.assertEqual(self.client.door_command_count, 1)
        self.assertEqual(len(self.commands()), 1)
        self.assertEqual([d[0] for d, _ in self.client.ctp.sent].count(0x20), 1)
        self.assertEqual(self.client.state, "registered")
        self.assertEqual(self.client.error, "")

    async def test_refusal_does_not_lose_registration_or_try_other_relay(self):
        task = await self.start_command()
        self.answer(b"\0\x0e\x05")
        with self.assertRaisesRegex(DoorCommandError, "Code 5"):
            await task
        self.assertEqual(self.client.door_state, "refused")
        self.assertTrue(self.client.registered)
        self.assertIsNone(self.client.last_door_confirmation)
        self.assertEqual(len(self.commands()), 1)

    async def test_ack_only_times_out_without_repeating_open(self):
        task = await self.start_command()
        self.answer(body=b"", flags=0)
        with self.assertRaisesRegex(DoorCommandError, "CTP bestätigt"):
            await task
        self.assertEqual(self.client.door_state, "unconfirmed")
        self.assertIsNone(self.client.door_release_cause)
        self.assertEqual(len(self.commands()), 1)

    async def test_unrelated_and_malformed_responses_cannot_confirm(self):
        task = await self.start_command()
        self.answer(peer=("192.0.2.21", 44721))
        self.answer(source=MASTER)
        self.answer(cid=0x9235)
        self.answer(seq=8)
        self.answer(ack=255)
        # The last packet advances the peer sequence but has not ACKed our command.
        self.answer(b"\0\x0e", seq=8)
        self.answer(b"\0\x0e\0\0", seq=9)
        with self.assertRaises(DoorCommandError):
            await task
        self.assertEqual(self.client.door_state, "unconfirmed")
        self.assertIsNone(self.client.last_door_confirmation)

    async def test_unregistered_command_sends_nothing(self):
        self.client.registered = False
        with self.assertRaisesRegex(DoorCommandError, "nicht angemeldet"):
            await self.client.open_door()
        self.client.resolve_entrance.assert_not_awaited()
        self.assertEqual(self.client.ctp.sent, [])

    async def test_crp_failure_sends_no_door_command_and_keeps_ring_registration(self):
        self.client.resolve_entrance.side_effect = TimeoutError("Keine Antwort der Außenstation")
        with self.assertRaisesRegex(DoorCommandError, "Keine Antwort"):
            await self.client.open_door()
        self.assertEqual(self.commands(), [])
        self.assertEqual(self.client.door_state, "failed")
        self.assertTrue(self.client.registered)

    async def test_parallel_press_and_immediate_second_press_do_not_duplicate(self):
        task = await self.start_command()
        with self.assertRaisesRegex(DoorCommandError, "bereits"):
            await self.client.open_door()
        self.answer()
        await task
        with self.assertRaisesRegex(DoorCommandError, "zwei Sekunden"):
            await self.client.open_door()
        self.assertEqual(len(self.commands()), 1)

    async def test_ring_during_door_command_is_delivered_without_unlock_retry(self):
        task = await self.start_command()
        packet = server_packet(0xc0, 0x4321, 32, 16, invite())
        self.client.receive_ctp(packet, ("192.0.2.20", 52051))
        self.client.receive_ctp(packet, ("192.0.2.20", 52051))
        self.assertEqual(len(self.rings), 1)
        self.assertEqual(self.client.loxone.sent, [(b"COMELIT_RING=1", None)])
        self.assertEqual(len(self.commands()), 1)
        self.assertFalse(task.done())
        self.answer()
        await task

    async def test_stop_during_door_command_unblocks_without_replay(self):
        task = await self.start_command()
        transport = self.client.ctp
        await self.client.stop()
        with self.assertRaises(DoorCommandError):
            await task
        self.assertIsNone(self.client.door_pending)
        self.assertFalse(self.client.door_lock.locked())
        self.assertEqual(len([d for d, _ in transport.sent if d[8:10] == b"\0\x2d"]), 1)

    async def test_ring_alone_never_opens_door(self):
        self.client.receive_ctp(server_packet(0xc0, 0x4321, 32, 16, invite()),
                                ("192.0.2.20", 52051))
        self.assertEqual(self.commands(), [])
        self.assertEqual(self.client.door_state, "idle")
        self.client.resolve_entrance.assert_not_awaited()


class EntranceResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_strict_resolution_and_registration_can_overlap(self):
        client = NativeClient(Settings(), lambda _: None, lambda: None)
        client.crp, client.ctp = Transport(), Transport()
        client.local_port = 43017
        master_task = asyncio.create_task(client.resolve_master())
        entrance_task = asyncio.create_task(client.resolve_entrance())
        await asyncio.sleep(0)
        self.assertEqual({p[20:30] for p, _ in client.crp.sent}, {MASTER, ENTRANCE})
        reply = b"\0\0\0\x02" + ENTRANCE + bytes.fromhex("d431c0000215") + bytes(16)
        client.receive_crp(reply, ("192.0.2.21", 12))
        client.receive_crp(reply[:4] + MASTER + reply[14:], ("192.0.2.21", 24100))
        client.receive_crp(reply[:16] + bytes.fromhex("c000026f") + reply[20:], ("192.0.2.21", 24100))
        self.assertFalse(client.entrance_crp_pending.done())
        self.assertFalse(client.crp_pending.done())
        client.receive_crp(reply, ("192.0.2.21", 24100))
        await entrance_task
        self.assertEqual(client.entrance_port, 54321)
        self.assertFalse(client.crp_pending.done())
        master_reply = b"\0\0\0\x02" + MASTER + bytes.fromhex("d298c0000214") + bytes(16)
        client.receive_crp(master_reply, ("192.0.2.20", 24100))
        await master_task
        self.assertEqual(client.master_port, 53912)
        self.assertEqual(client.crp_state, "resolved")
        self.assertEqual(client.ctp.sent, [])

    async def test_no_resolution_does_not_fall_back_to_old_port(self):
        client = NativeClient(Settings(), lambda _: None, lambda: None)
        client.crp, client.ctp = Transport(), Transport()
        client.running = client.registered = True
        client.local_port = 43017
        with self.assertRaisesRegex(DoorCommandError, "kein Türöffnerbefehl gesendet"):
            await client.open_door()
        self.assertEqual(client.ctp.sent, [])
        self.assertEqual(len(client.crp.sent), 3)
        self.assertEqual(client.door_command_count, 0)
        self.assertTrue(client.registered)

    async def test_stop_while_resolving_sends_no_unlock(self):
        client = NativeClient(Settings(), lambda _: None, lambda: None)
        client.crp, client.ctp = Transport(), Transport()
        client.running = client.registered = True
        transport = client.ctp
        task = asyncio.create_task(client.open_door())
        await asyncio.sleep(0)
        await client.stop()
        with self.assertRaises(DoorCommandError):
            await task
        self.assertEqual(transport.sent, [])
        self.assertIsNone(client.entrance_crp_pending)
