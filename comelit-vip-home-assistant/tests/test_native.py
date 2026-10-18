"""Offline tests: fixed wire fixtures and a loopback-only fake master.

These do not demonstrate that a TP6842 accepts registration or delivers calls.
"""
import asyncio
import importlib
from pathlib import Path
import struct
import socket
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components" / "comelit_native"))
from protocol.client import NativeClient, Settings
from protocol.ctp import parse_ctp_packet, build_ctp_packet
from protocol.messages import register_request, register_result, crp_reply, crp_request, crp_result

OWN = b"000000011\x00"
MASTER = b"00000001\x00\x00"
ENTRANCE = b"00000002\x00\x00"


def server_packet(flags, cid, seq, ack, body, source=MASTER, dest=OWN):
    # Independent fixture construction, rather than round-trip through encoder.
    return struct.pack(">BBHBBH", flags, 24, cid, seq, ack, len(body)) + body + b"\0" * (-len(body) % 4) + b"\xff" * 4 + source + dest


def invite(tag=b"PP", call_id=b"\x11\x22\x33\x44"):
    return b"\x00\x01" + ENTRANCE + MASTER + b"\x01\x20" + call_id + ENTRANCE + tag


class Sink:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr=None):
        self.sent.append((data, addr))


class WireTests(unittest.TestCase):
    def test_native_registration_request_byte_order(self):
        expected = bytes.fromhex("0040341230303030303030313100100e00")
        self.assertEqual(register_request(0x1234, "000000011"), expected)
        self.assertEqual(len(expected), 17)
        wire = build_ctp_packet(flags=0xc0, connection=b"\x25\x61", sequence=7,
            acknowledgement=9, body=expected, source="000000011", destination="00000001")
        expected_wire = bytes.fromhex("c018256107090011") + expected + b"\0" * 3 + b"\xff" * 4 + OWN + MASTER
        self.assertEqual(wire, expected_wire)

    def test_result_must_match_request_address_and_success_code(self):
        body = b"\x00\x41\x34\x12" + OWN + b"\x00\x00"
        self.assertEqual(register_result(body, 0x1234, "000000011"), 0)
        self.assertIsNone(register_result(body, 0x1235, "000000011"))
        self.assertIsNone(register_result(body, 0x1234, "000000012"))
        self.assertEqual(register_result(body[:-2] + b"\x08\0", 0x1234, "000000011"), 8)
        self.assertIsNone(register_result(body[:-1], 0x1234, "000000011"))

    def test_crp_answers_own_slave_only(self):
        query = bytes.fromhex("00000001") + MASTER + bytes.fromhex("cb53c0000214") + OWN + b"\0" * 6
        expected = bytes.fromhex("00000002") + OWN + bytes.fromhex("a028c000020a") + query[4:20]
        self.assertEqual(crp_reply(query, "000000011", "192.0.2.10", 41000), expected)
        self.assertIsNone(crp_reply(query[:20] + MASTER + query[30:], "000000011", "192.0.2.10", 41000))
        self.assertIsNone(crp_reply(b"bad", "000000011", "192.0.2.10", 41000))

    def test_crp_request_advertises_ha_ctp_return_path(self):
        query = crp_request("000000011", "192.0.2.10", 49293, "00000001")
        expected = (bytes.fromhex("00000001") + OWN
                    + bytes.fromhex("c08dc000020a") + MASTER + bytes(6))
        self.assertEqual(query, expected)
        reply = bytes.fromhex("00000002") + MASTER + bytes.fromhex("c350c0000214") + query[4:20]
        self.assertEqual(crp_result(reply, query, "192.0.2.20"), 50000)

    def test_crp_reply_cannot_redirect_to_another_ip_or_identity(self):
        query = crp_request("000000011", "192.0.2.10", 49293, "00000001")
        reply = bytes.fromhex("00000002") + MASTER + bytes.fromhex("c350c0000214") + query[4:20]
        variants = [reply[:-1], reply + b"\0", reply[:14] + b"\0\0" + reply[16:],
                    reply[:4] + ENTRANCE + reply[14:],
                    reply[:16] + bytes.fromhex("c0000232") + reply[20:],
                    b"\0\0\0\x03" + reply[4:]]
        for variant in variants:
            with self.subTest(variant=variant.hex()), self.assertRaises(ValueError):
                crp_result(variant, query, "192.0.2.20")

    def test_crp_resolution_does_not_require_requester_echo(self):
        query = crp_request("000000011", "192.0.2.10", 49293, "00000001")
        reply = bytes.fromhex("00000002") + MASTER + bytes.fromhex("c350c0000214") + query[4:20]
        # Synthetic variations, not an assertion about the unseen TP6842 bytes.
        # Native CRP correlates the pending target, not this reply suffix.
        variants = [reply[:20] + bytes(16),
                    reply[:20] + MASTER + reply[30:],
                    reply[:30] + bytes.fromhex("c08e") + reply[32:],
                    reply[:32] + bytes.fromhex("c0000232")]
        for variant in variants:
            with self.subTest(variant=variant.hex()):
                self.assertEqual(crp_result(variant, query, "192.0.2.20"), 50000)

    def test_truncated_and_bad_marker_packets_rejected(self):
        packet = server_packet(0xc0, 10, 1, 2, invite())
        for length in range(len(packet)):
            with self.assertRaises(ValueError):
                parse_ctp_packet(packet[:length])
        bad = bytearray(packet)
        bad[-24] = 0
        with self.assertRaises(ValueError):
            parse_ctp_packet(bytes(bad))

    def test_config_does_not_allow_master_identity(self):
        with self.assertRaises(ValueError):
            Settings(source_address="00000001").validate()
        with self.assertRaises(ValueError):
            Settings(source_address="000000021").validate()

    def test_native_trailerless_packet_has_no_padding_or_addresses(self):
        wire = bytes.fromhex("4000923407020003004100")
        packet = parse_ctp_packet(wire)
        self.assertEqual(packet.trailer_length, 0)
        self.assertEqual(packet.body, b"\0\x41\0")
        self.assertIsNone(packet.source)
        self.assertIsNone(packet.destination)
        for bad in (wire[:-1], wire + b"\0", bytes.fromhex("4001923407020000")):
            with self.assertRaises(ValueError):
                parse_ctp_packet(bad)


class IncomingTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.client = NativeClient(Settings(loxone_enabled=True), self.events.append, lambda: None)
        self.client.running = self.client.registered = True
        self.client.ctp = Sink()
        self.client.loxone = Sink()
        self.peer = ("192.0.2.20", 52051)

    def test_ring_once_and_repeat_ack(self):
        packet = server_packet(0xc0, 0x1234, 255, 13, invite())
        self.client.receive_ctp(packet, self.peer)
        self.client.receive_ctp(packet, self.peer)
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.client.loxone.sent, [(b"COMELIT_RING=1", None)])
        self.assertEqual(len(self.client.ctp.sent), 2)
        ack = parse_ctp_packet(self.client.ctp.sent[-1][0])
        self.assertEqual(ack.connection, b"\x92\x34")
        self.assertEqual((ack.sequence, ack.acknowledgement, ack.flags), (13, 0, 0))

    def test_no_false_ring_from_ack_floor_unknown_peer_wrong_destination(self):
        packet = server_packet(0xc0, 0x1234, 7, 2, invite())
        self.client.receive_ctp(packet, ("192.0.2.51", 52051))
        self.client.receive_ctp(server_packet(0xc0, 0x1234, 7, 2, invite(), dest=MASTER), self.peer)
        self.client.receive_ctp(server_packet(0xc0, 0x1234, 7, 2, invite(b"FF")), self.peer)
        self.client.receive_ctp(server_packet(0, 0x1234, 8, 2, b""), self.peer)
        self.assertEqual(self.events, [])

    def test_no_ring_without_successful_registration(self):
        self.client.registered = False
        self.client.receive_ctp(server_packet(0xc0, 0x1234, 7, 2, invite()), self.peer)
        self.assertEqual(self.events, [])
        self.assertEqual(self.client.loxone.sent, [])

    def test_empty_keepalive_does_not_advance_sequence(self):
        self.client.receive_ctp(server_packet(0xc0, 0x1234, 7, 2, invite()), self.peer)
        self.client.receive_ctp(server_packet(0x40, 0x1234, 8, 2, b""), self.peer)
        ack = parse_ctp_packet(self.client.ctp.sent[-1][0])
        self.assertEqual(ack.acknowledgement, 8)

    def test_deduplicate_same_call_over_second_connection(self):
        for cid in (0x1234, 0x1235):
            self.client.receive_ctp(server_packet(0xc0, cid, 7, 2, invite()), self.peer)
        self.assertEqual(len(self.events), 1)

    def test_older_28_byte_start_and_41_byte_start(self):
        self.client.receive_ctp(server_packet(0xc0, 0x1234, 7, 2, invite()[:28]), self.peer)
        self.client.receive_ctp(server_packet(0xc0, 0x1235, 7, 2, invite(call_id=b"\x55" * 4) + b"\0"), self.peer)
        self.assertEqual(len(self.events), 2)

    def test_new_call_reusing_connection_id_is_not_lost(self):
        self.client.receive_ctp(server_packet(0xc0, 0x1234, 7, 2, invite()), self.peer)
        self.client.receive_ctp(server_packet(0xc0, 0x1234, 90, 52, invite(call_id=b"\x66" * 4)), self.peer)
        self.assertEqual(len(self.events), 2)

    def test_unknown_trailerless_connection_cannot_ring(self):
        body = invite()
        wire = struct.pack(">BBHBBH", 0xc0, 0, 0x1234, 7, 2, len(body)) + body
        self.client.receive_ctp(wire, self.peer)
        self.assertEqual(self.events, [])
        self.assertEqual(self.client.rejected_packets, 1)
        self.assertIn("ohne bekannte Verbindung", self.client.last_rejection)

    def test_changed_port_is_reported_but_not_used(self):
        self.client.receive_ctp(server_packet(0xc0, 0x1234, 7, 2, invite()),
                                (self.peer[0], 52052))
        self.assertEqual(self.client.master_udp_received, 1)
        self.assertEqual(self.client.master_ctp_accepted, 0)
        self.assertIn("52052", self.client.last_rejection)
        self.assertEqual(self.events, [])
        self.assertEqual(self.client.ctp.sent, [])

    def test_invalid_packet_from_master_is_distinct_from_no_reply(self):
        self.client.receive_ctp(b"invalid", self.peer)
        self.assertIn("UDP von TP6842 empfangen", self.client.registration_timeout())
        self.assertIn("too short", self.client.last_rejection)


class MockMaster(asyncio.DatagramProtocol):
    def __init__(self, broadcast_replies=False):
        self.broadcast_replies = broadcast_replies

    def connection_made(self, transport):
        self.transport = transport
        self.requests = []
        self.request_peers = []

    def datagram_received(self, data, addr):
        if data[8:10] != b"\x00\x40":
            return
        self.requests.append(data)
        self.request_peers.append(addr)
        if self.broadcast_replies:
            addr = ("127.255.255.255", addr[1])
        # Echo registration's independent request ID and slave; status=OK.
        response_body = b"\x00\x41" + data[10:22] + b"\0\0"
        cid = int.from_bytes(data[2:4], "big") ^ 0x8000
        response = server_packet(0x60, cid, data[5], (data[4] + 1) & 255, response_body)
        self.transport.sendto(response, addr)
        loop = asyncio.get_running_loop()
        ring = server_packet(0xc0, 0x1122, 15, 22, invite())
        loop.call_later(0.02, self.transport.sendto, ring, addr)
        loop.call_later(0.04, self.transport.sendto, ring, addr)


class AckThenDelayedMaster(MockMaster):
    """Native ACK followed by trailerless REGISTER_RES on the same connection."""

    def datagram_received(self, data, addr):
        if data[8:10] != b"\0\x40":
            return
        self.requests.append(data)
        cid = int.from_bytes(data[2:4], "big") ^ 0x8000
        ack = server_packet(0, cid, data[5], (data[4] + 1) & 255, b"")
        self.transport.sendto(ack, addr)
        body = b"\0\x41" + data[10:22] + b"\0\0"
        response = struct.pack(">BBHBBH", 0x60, 0, cid, data[5],
                               (data[4] + 1) & 255, len(body)) + body
        # Past the old retry interval: an ACK must suppress duplicate requests.
        asyncio.get_running_loop().call_later(0.65, self.transport.sendto, response, addr)


class MockCrp(asyncio.DatagramProtocol):
    """Independent CRP responder: advertise the actual loopback CTP port."""

    def __init__(self, ctp_port, echo_return_path=True):
        self.ctp_port = ctp_port
        self.echo_return_path = echo_return_path
        self.queries = []

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if len(data) != 36 or data[:4] != b"\0\0\0\x01" or data[20:30] != MASTER:
            return
        self.queries.append((data, addr))
        suffix = data[4:20] if self.echo_return_path else bytes(16)
        response = (b"\0\0\0\x02" + MASTER + struct.pack(">H", self.ctp_port)
                    + socket.inet_aton(self.transport.get_extra_info("sockname")[0]) + suffix)
        self.transport.sendto(response, (addr[0], 24100))


class LoopbackTests(unittest.IsolatedAsyncioTestCase):
    async def start_with_crp(self, client, ctp_port, echo_return_path=True):
        loop = asyncio.get_running_loop()
        real_create = loop.create_datagram_endpoint
        crp_transport, resolver = await real_create(
            lambda: MockCrp(ctp_port, echo_return_path), local_addr=(client.settings.master_ip, 24100))

        async def create_endpoint(factory, **kwargs):
            # Two simulated hosts share this OS: isolate their CRP binds by IP.
            # The exchange uses unicast only inside this test fixture. A
            # separate real-socket test verifies loopback broadcast delivery.
            if kwargs.get("local_addr") == ("0.0.0.0", 24100):
                kwargs["local_addr"] = (client.settings.local_ip, 24100)
            return await real_create(factory, **kwargs)

        try:
            with patch.object(loop, "create_datagram_endpoint", side_effect=create_endpoint):
                await client.start()
        except BaseException:
            crp_transport.close()
            raise
        return crp_transport, resolver

    async def test_udp_register_ring_shutdown(self):
        await self.check_udp_register_ring_shutdown(echo_return_path=True)

    async def test_udp_register_and_ring_without_crp_requester_echo(self):
        await self.check_udp_register_ring_shutdown(echo_return_path=False)

    async def test_udp_register_and_ring_with_ctp_broadcast_replies(self):
        await self.check_udp_register_ring_shutdown(echo_return_path=False, broadcast_replies=True)

    async def check_udp_register_ring_shutdown(self, echo_return_path, broadcast_replies=False):
        loop = asyncio.get_running_loop()
        transport, master = await loop.create_datagram_endpoint(
            lambda: MockMaster(broadcast_replies), local_addr=("127.0.0.2", 0), allow_broadcast=True)
        actual_port = transport.get_extra_info("sockname")[1]
        settings = Settings(local_ip="127.0.0.1", master_ip="127.0.0.2",
            crp_broadcast_ip="127.0.0.2",
            master_port=1, entrance_ip="127.0.0.4",
            loxone_enabled=False)
        events = []
        client = NativeClient(settings, events.append, lambda: None)
        crp_transport = None
        try:
            crp_transport, resolver = await self.start_with_crp(client, actual_port, echo_return_path)
            await asyncio.sleep(0.1)
            self.assertTrue(client.registered)
            self.assertEqual(len(events), 1)
            self.assertEqual(len(master.requests), 1)
            self.assertEqual(master.requests[0][0], 0xc0)
            self.assertEqual(master.requests[0][6:8], b"\0\x11")
            self.assertEqual(client.master_port, actual_port)
            self.assertEqual(client.settings.master_port, 1)
            self.assertEqual(client.crp_state, "resolved")
            self.assertEqual(client.crp_return_path_matches, echo_return_path)
            self.assertEqual(client.ctp.get_extra_info("sockname"), ("0.0.0.0", client.local_port))
            self.assertEqual(client.ctp_route_source, settings.local_ip)
            self.assertEqual(master.request_peers, [(settings.local_ip, client.local_port)])
            self.assertEqual(len(resolver.queries), 1)
            self.assertEqual(resolver.queries[0][0][16:20], b"\x7f\0\0\x01")
            self.assertEqual(int.from_bytes(resolver.queries[0][0][14:16], "big"), client.local_port)
        finally:
            await client.stop()
            if crp_transport:
                crp_transport.close()
            transport.close()
        self.assertFalse(client.registered)
        self.assertIsNone(client.task)
        self.assertIsNone(client.ctp)

    async def test_unanswered_request_stays_unregistered(self):
        client = NativeClient(Settings(loxone_enabled=False), lambda event: None, lambda: None)
        client.ctp = Sink()
        # This regression covers the CTP stage only; resolution has its own tests.
        client.resolve_master = AsyncMock()
        with self.assertRaises(TimeoutError):
            await client.register()
        self.assertFalse(client.registered)
        self.assertEqual(len(client.ctp.sent), 6)
        self.assertEqual(len(set(packet for packet, addr in client.ctp.sent)), 1)
        self.assertIn("Keine UDP-Antwort", client.registration_timeout())

    async def test_acked_request_not_repeated_and_native_reply_is_accepted(self):
        loop = asyncio.get_running_loop()
        transport, master = await loop.create_datagram_endpoint(
            AckThenDelayedMaster, local_addr=("127.0.0.2", 0))
        settings = Settings(local_ip="127.0.0.1", master_ip="127.0.0.2",
            crp_broadcast_ip="127.0.0.2",
            master_port=transport.get_extra_info("sockname")[1], entrance_ip="127.0.0.4",
            loxone_enabled=False)
        client = NativeClient(settings, lambda event: None, lambda: None)
        crp_transport = None
        try:
            crp_transport, resolver = await self.start_with_crp(
                client, transport.get_extra_info("sockname")[1])
            await asyncio.wait_for(self.wait_registered(client), 2)
            self.assertEqual(len(master.requests), 1)
            self.assertEqual(client.registration_tx, 1)
            self.assertTrue(client.registration_acknowledged)
            self.assertEqual(client.master_udp_received, 2)
            self.assertEqual(client.registration_ctp_received, 2)
            self.assertEqual(client.last_ctp["trailer_length"], 0)
            self.assertEqual(client.rejected_packets, 0)
        finally:
            await client.stop()
            if crp_transport:
                crp_transport.close()
            transport.close()

    async def test_unresolved_master_gets_no_ctp_registration(self):
        client = NativeClient(Settings(loxone_enabled=False), lambda event: None, lambda: None)
        client.ctp, client.crp = Sink(), Sink()
        client.local_port = 49293
        with self.assertRaisesRegex(TimeoutError, "Keine passende CRP-Antwort"):
            await client.register()
        self.assertFalse(client.registered)
        self.assertEqual(client.ctp.sent, [])
        self.assertEqual(client.registration_tx, 0)
        self.assertEqual(len(client.crp.sent), 3)
        self.assertEqual({peer for _, peer in client.crp.sent}, {("192.0.2.255", 24100)})
        self.assertEqual({data[20:30] for data, _ in client.crp.sent}, {MASTER})
        self.assertEqual(client.crp_state, "failed")
        self.assertIsNone(client.crp_pending)
        self.assertIsNone(client.crp_query)

    async def test_crp_requires_expected_sender_identity_and_ip_while_pending(self):
        client = NativeClient(Settings(loxone_enabled=False), lambda event: None, lambda: None)
        client.ctp, client.crp = Sink(), Sink()
        client.local_port = 49293
        task = asyncio.create_task(client.resolve_master())
        await asyncio.sleep(0)
        query = client.crp.sent[0][0]
        reply = b"\0\0\0\x02" + MASTER + bytes.fromhex("c350c0000214") + query[4:20]
        client.receive_crp(reply, ("192.0.2.50", 24100))
        self.assertFalse(client.crp_pending.done())
        client.receive_crp(reply, ("192.0.2.20", 12345))
        self.assertFalse(client.crp_pending.done())
        client.receive_crp(reply[:4] + ENTRANCE + reply[14:], ("192.0.2.20", 24100))
        self.assertFalse(client.crp_pending.done())
        client.receive_crp(reply[:16] + bytes.fromhex("c0000232") + reply[20:], ("192.0.2.20", 24100))
        self.assertFalse(client.crp_pending.done())
        client.receive_crp(reply[:20] + bytes(16), ("192.0.2.20", 24100))
        await task
        self.assertEqual(client.master_port, 50000)
        self.assertEqual(client.settings.master_port, 52051)
        self.assertFalse(client.crp_return_path_matches)
        self.assertFalse(client.registered)
        self.assertEqual(client.ctp.sent, [])
        # A later unsolicited CRP answer must not change the selected port.
        client.receive_crp(reply[:14] + bytes.fromhex("c351") + reply[16:], ("192.0.2.20", 24100))
        self.assertEqual(client.master_port, 50000)

    async def test_stop_during_resolution_closes_sockets(self):
        client = NativeClient(Settings(local_ip="127.0.0.1", master_ip="127.0.0.2",
                                      crp_broadcast_ip="127.255.255.255",
                                      loxone_enabled=False), lambda event: None, lambda: None)
        await client.start()
        await asyncio.sleep(0.02)
        self.assertEqual(client.crp_state, "resolving")
        await client.stop()
        self.assertIsNone(client.crp_pending)
        self.assertIsNone(client.crp_query)
        self.assertIsNone(client.task)
        self.assertIsNone(client.crp)
        self.assertIsNone(client.ctp)

    async def test_different_route_source_does_not_silently_change_ha_ip(self):
        client = NativeClient(Settings(loxone_enabled=False), lambda event: None, lambda: None)
        with patch("protocol.client.route_source_ip", return_value="192.0.2.31"):
            with self.assertRaisesRegex(OSError, "CTP-Route verwendet 192.0.2.31"):
                await client.start()
        self.assertFalse(client.running)
        self.assertIsNone(client.ctp)
        self.assertIsNone(client.crp)
        self.assertIsNone(client.task)
        self.assertEqual(client.settings.local_ip, "192.0.2.10")

    async def test_native_socket_sends_and_receives_loopback_broadcast(self):
        client = NativeClient(Settings(local_ip="127.0.0.1", master_ip="127.0.0.2",
                                      entrance_ip="127.0.0.4",
                                      crp_broadcast_ip="127.255.255.255",
                                      loxone_enabled=False), lambda event: None, lambda: None)
        received = asyncio.get_running_loop().create_future()
        original_receive = client.receive_crp

        def observe(data, addr):
            if not received.done():
                received.set_result((data, addr))
            original_receive(data, addr)

        client.receive_crp = observe
        try:
            # No endpoint patch: the production wildcard bind receives the
            # real loopback broadcast. No packets leave the loopback network.
            await client.start()
            data, addr = await asyncio.wait_for(received, 1)
            sock = client.crp.get_extra_info("socket")
            self.assertEqual(sock.getsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST), 1)
            self.assertEqual(sock.getsockname(), ("0.0.0.0", 24100))
            self.assertEqual(addr[1], 24100)
            self.assertEqual(data, crp_request("000000011", "127.0.0.1",
                                               client.local_port, "00000001"))
            # Receiving our query is not a response or a registration.
            self.assertFalse(client.registered)
            self.assertEqual(client.crp_responses, 0)
            self.assertEqual(client.registration_tx, 0)
        finally:
            await client.stop()

    async def wait_registered(self, client):
        while not client.registered:
            await asyncio.sleep(0.01)


if __name__ == "__main__":
    unittest.main()
