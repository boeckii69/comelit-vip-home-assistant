"""Independent encoder oracle and the user's confirmed native endpoint vector."""
import struct
import unittest

import test_native
from protocol.talk import RtpWriter, encode_g711, media_destination
from protocol.audio import parse_audio_packet


class TalkTests(unittest.TestCase):
    def test_confirmed_live_request_is_tx_port_30008_not_rx_port_50076(self):
        body = bytes.fromhex("00111800c00002143875")
        self.assertEqual(media_destination(body, ("192.0.2.20", "192.0.2.21")),
                         ("192.0.2.20", 30008))
        self.assertIsNone(media_destination(body[:2] + b"\x98" + body[3:], ("192.0.2.20",)))
        variants = [body[:-1], body[:3] + b"\2" + body[4:], body[:2] + b"\x14" + body[3:],
                    body[:4] + bytes((192, 168, 1, 62)) + body[8:], body[:8] + b"\0\0"]
        for data in variants:
            with self.assertRaises(ValueError):
                media_destination(data, ("192.0.2.20",))

    def test_all_signed_pcm_values_match_independent_g711_oracle(self):
        try:
            import audioop
        except ImportError:
            self.skipTest("Independent audioop oracle requires Python <=3.12")
        for pt, oracle in ((8, audioop.lin2alaw), (0, audioop.lin2ulaw)):
            for start in range(-32768, 32768, 1000):
                pcm = b"".join(struct.pack("<h", i) for i in range(start, min(start + 1000, 32768)))
                self.assertEqual(encode_g711(pcm, pt), oracle(pcm, 2))

    def test_rtp_sequence_timestamp_wrap_marker_and_payload(self):
        writer = RtpWriter()
        writer.sequence, writer.timestamp, writer.ssrc = 65535, 0xfffffff0, 7
        first = writer.packet(b"\xd5" * 160, 8)
        self.assertEqual(first[:12], bytes.fromhex("8088fffffffffff000000007"))
        next_packet = writer.packet(b"\x55" * 160, 8)
        packet = parse_audio_packet(next_packet)
        self.assertEqual((packet.sequence, packet.timestamp, packet.ssrc), (0, 144, 7))
        self.assertEqual(next_packet[1], 8)
        self.assertEqual(writer.octets, 320)
