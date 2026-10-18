"""Offline codec, hostile packet and bounded receive-buffer checks."""
import asyncio
import struct
import unittest
from unittest.mock import patch

import test_native  # Adds the standalone protocol package to sys.path.
from protocol.audio import AudioReceiver, decode_g711, parse_audio_packet
from protocol.messages import audio_media_request


def rtp(seq=1, timestamp=0, pt=8, payload=b"\xd5" * 160, ssrc=123):
    return struct.pack(">BBHII", 0x80, pt, seq, timestamp, ssrc) + payload


class AudioWireTests(unittest.TestCase):
    def test_native_media_request_fixed_vector_and_stop(self):
        expected = bytes.fromhex("00111800c000020a2c5e7805ee020000")
        self.assertEqual(audio_media_request("192.0.2.10", 24108), expected)
        self.assertEqual(audio_media_request("192.0.2.10", 24108, stop=True),
                         expected[:2] + b"\x98" + expected[3:])
        for ip, port in (("0.0.0.0", 1), ("::1", 24108), ("192.0.2.10", 0)):
            with self.assertRaises(ValueError):
                audio_media_request(ip, port)

    def test_all_g711_codes_against_independent_stdlib_decoder(self):
        # audioop is only a test oracle under Python 3.12, never a dependency.
        try:
            import audioop
        except ImportError:
            self.skipTest("audioop oracle requires Python <=3.12")
        codes = bytes(range(256))
        self.assertEqual(decode_g711(codes, 8), audioop.alaw2lin(codes, 2))
        self.assertEqual(decode_g711(codes, 0), audioop.ulaw2lin(codes, 2))
        self.assertEqual(decode_g711(b"\xff\x7f", 0), b"\0" * 4)
        self.assertEqual(decode_g711(b"\xd5\x55", 8), struct.pack("<hh", 8, -8))

    def test_rtp_extension_csrc_padding_and_marker(self):
        wire = struct.pack(">BBHII", 0xb1, 0x88, 65535, 0xffffffff, 42)
        wire += b"CSRC" + bytes.fromhex("bede0001") + b"EXT!" + b"\xd5\x55" + b"\0\2"
        packet = parse_audio_packet(wire)
        self.assertEqual((packet.sequence, packet.timestamp, packet.ssrc, packet.payload_type),
                         (65535, 0xffffffff, 42, 8))
        self.assertEqual(packet.payload, b"\xd5\x55")

    def test_bad_rtp_never_becomes_pcm(self):
        bad = [b"", rtp()[:11], b"\x40" + rtp()[1:], rtp(pt=200), rtp(pt=96),
               b"\x8f" + rtp()[1:14], b"\x90" + rtp()[1:12],
               b"\x90" + rtp()[1:12] + b"\0\0\xff\xff", rtp(payload=b""),
               b"\xa0" + rtp()[1:-1] + b"\0", rtp(payload=b"x" * 2037)]
        for data in bad:
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                parse_audio_packet(data)


class ReceiverTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.chunks, self.first, self.errors = [], [], []
        self.receiver = AudioReceiver(["192.0.2.21"], self.chunks.append,
                                      lambda: self.first.append(True), self.errors.append)
        self.addCleanup(self.receiver.close)
        self.peer = ("192.0.2.21", 24108)

    def feed(self, seq, timestamp, **kw):
        self.receiver.datagram_received(rtp(seq, timestamp, **kw), self.peer)

    async def test_reorder_duplicates_sequence_wrap_and_pcm_batch(self):
        for seq, timestamp in ((65534, 0), (0, 320), (65535, 160), (1, 480), (2, 640)):
            self.feed(seq, timestamp)
        self.feed(2, 640)
        self.assertEqual(self.receiver.packets, 5)
        self.assertEqual(self.receiver.duplicates, 1)
        self.assertEqual(self.first, [True])
        self.assertEqual(self.chunks, [b"\x08\0" * 800])

    async def test_missing_packet_expires_and_does_not_block_audio(self):
        self.feed(1, 0)
        for seq in (3, 4, 5):
            self.feed(seq, (seq - 1) * 160)
        await asyncio.sleep(0.12)
        self.assertEqual(self.receiver.lost, 1)
        self.assertEqual(self.chunks[0], b"\x08\0" * 160 + b"\0" * 320 + b"\x08\0" * 480)
        self.assertIsNone(self.receiver.timer)

    async def test_source_codec_and_ssrc_pinned_and_buffer_bounded(self):
        self.receiver.datagram_received(rtp(), ("192.0.2.51", 24108))
        self.assertIsNone(self.receiver.peer)
        self.feed(1, 0)
        self.receiver.datagram_received(rtp(2), (self.peer[0], 7777))
        self.feed(2, 160, ssrc=777)
        self.feed(2, 160, pt=0)
        for seq in range(100, 500):
            self.feed(seq, seq * 160)
        self.assertEqual(self.receiver.packets, 1)
        self.assertEqual(len(self.receiver.pending), 0)
        self.assertEqual(self.receiver.rejected, 403)

    async def test_same_stream_can_resume_after_network_gap(self):
        self.feed(1, 0)
        with patch("protocol.audio.time.monotonic", return_value=self.receiver.last_packet + 2):
            self.feed(102, 16160)
        self.assertEqual(self.receiver.packets, 2)
        self.assertEqual(self.receiver.lost, 100)
        self.receiver.close()
        self.feed(103, 16320)
        self.assertEqual(self.receiver.packets, 2)
        self.assertEqual(len(self.receiver.pcm), 0)
