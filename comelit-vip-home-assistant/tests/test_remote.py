"""External calls must be explicitly authorized; secrets never become attributes."""
import asyncio
import importlib.util
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

from test_sip import request
import test_sip_outgoing as sip_outgoing
from protocol.remote_gate import RemoteGate, valid_gateway_key
from protocol.sip_wire import parse_sip

KEY = "test_key_" + "a" * 40


class GateTests(unittest.TestCase):
    def test_one_use_and_expiration(self):
        now = [100.0]
        gate = RemoteGate(lambda: now[0])
        self.assertFalse(gate.consume())
        gate.grant()
        now[0] = 129.99
        self.assertTrue(gate.consume())
        self.assertFalse(gate.consume())
        gate.grant()
        now[0] += 30
        self.assertFalse(gate.consume())

    def test_exact_single_secret_and_configuration_validation(self):
        self.assertTrue(valid_gateway_key(KEY))
        self.assertTrue(valid_gateway_key(""))
        for key in ("short", "x"*129, "\n"+KEY, KEY+";", None):
            self.assertFalse(valid_gateway_key(key))
        msg = parse_sip(request(extra="X-Comelit-Gateway: "+KEY))
        self.assertTrue(RemoteGate.authenticate(msg, KEY))
        self.assertFalse(RemoteGate.authenticate(msg, KEY+"b"))
        self.assertFalse(RemoteGate.authenticate(msg, ""))
        msg.headers['x-comelit-gateway'].append(KEY)
        self.assertFalse(RemoteGate.authenticate(msg, KEY))


class RemoteSipTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = sip_outgoing.OutgoingSipTests.asyncSetUp
    asyncTearDown = sip_outgoing.OutgoingSipTests.asyncTearDown
    endpoint = sip_outgoing.OutgoingSipTests.endpoint
    recv_sip = sip_outgoing.OutgoingSipTests.recv_sip
    send = sip_outgoing.OutgoingSipTests.send
    connect = sip_outgoing.OutgoingSipTests.connect

    async def test_automatic_mode_starts_only_with_valid_gateway_key_without_grant(self):
        self.client.settings.remote_gateway_key = KEY
        self.client.settings.remote_auto_enabled = True
        self.send(extra="X-Comelit-Gateway: wrong")
        await self.recv_sip(403)
        self.assertEqual(self.native_peer.commands(), [])
        self.send(call_id="auto-call", branch="z9hG4bK-auto", extra="X-Comelit-Gateway: "+KEY)
        await self.recv_sip(200)
        self.assertFalse(self.bridge.remote_gate.available)
        self.assertTrue(self.bridge.session.external)
        self.assertEqual(self.bridge.diagnostics()['remote_audio_mode'], 'automatic')

    async def test_automatic_mode_still_rejects_invalid_media_before_native_activity(self):
        self.client.settings.remote_gateway_key = KEY
        self.client.settings.remote_auto_enabled = True
        self.send(body=b"invalid", extra="X-Comelit-Gateway: "+KEY)
        await self.recv_sip(488)
        self.assertEqual(self.native_peer.commands(), [])

    async def test_gateway_cannot_start_before_authenticated_ha_permission(self):
        self.client.settings.remote_gateway_key = KEY
        self.send(extra="X-Comelit-Gateway: "+KEY)
        await self.recv_sip(403)
        self.assertEqual(self.native_peer.commands(), [])
        self.assertFalse(self.client.call.owned)

    async def test_permission_needs_key_and_is_not_consumed_by_invalid_sdp(self):
        self.client.settings.remote_gateway_key = KEY
        self.bridge.grant_remote_audio()
        self.send()
        await self.recv_sip(403)
        self.assertTrue(self.bridge.remote_gate.available)
        self.send(call_id="bad-offer",branch="z9hG4bK-bad", body=b"invalid",
                  extra="X-Comelit-Gateway: "+KEY)
        await self.recv_sip(488)
        self.assertTrue(self.bridge.remote_gate.available)
        self.assertEqual(self.native_peer.commands(), [])

    async def test_valid_gateway_uses_once_and_duplicate_invite_is_cached(self):
        self.client.settings.remote_gateway_key = KEY
        self.bridge.grant_remote_audio()
        data = self.send(extra="X-Comelit-Gateway: "+KEY)
        answer = await self.recv_sip(200)
        self.assertFalse(self.bridge.remote_gate.available)
        self.assertTrue(self.bridge.session.external)
        self.phone.transport.sendto(data, ("127.0.0.1", self.bridge.local_port))
        repeated = await self.recv_sip(200)
        self.assertEqual(answer.body, repeated.body)
        self.assertEqual(sum(p.opcode == 1 for p in self.native_peer.commands()), 1)
        self.assertNotIn(KEY, str(self.bridge.diagnostics()))
        await self.bridge.stop()
        self.assertFalse(self.bridge.remote_gate.available)

    async def test_local_allowlist_is_not_gateway_bypass(self):
        self.client.settings.remote_gateway_key = KEY
        self.bridge.grant_remote_audio()
        self.assertIn('127.0.0.1', self.bridge.allowed_ips)
        self.send(extra="X-Comelit-Gateway: wrong")
        await self.recv_sip(403)
        self.assertEqual(self.native_peer.commands(), [])


spec=importlib.util.spec_from_file_location('audio_gateway',Path(__file__).parents[1]/'addons/comelit_external_audio/gateway.py')
gateway=importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)


def gateway_options():
    return dict(sip_username='test_only_123',sip_password='TestOnlyPassword123',bridge_key=KEY,
                ha_ip='192.0.2.10',ha_sip_port=5062,local_network='192.0.2.0/24',
                public_media_ip='',turn_server='',turn_username='',turn_password='')


class GatewayConfigTests(unittest.TestCase):
    def test_legacy_stun_requires_matching_transaction_and_mapped_address(self):
        tx=bytes(range(16))
        value=struct.pack('!BBHI',0,1,40000,0x08080808)
        packet=struct.pack('!HH',0x101,12)+tx+struct.pack('!HH',1,8)+value
        self.assertEqual(gateway.legacy_stun_address(packet,tx),'8.8.8.8')
        for data,transaction in [(packet,b'bad!'*4),(packet[:-1],tx),
                                 (packet[:20],tx),(packet[:20]+struct.pack('!HH',0x20,8)+value,tx),
                                 (packet[:-4]+bytes([192,168,1,30]),tx)]:
            with self.assertRaises(ValueError):gateway.legacy_stun_address(data,transaction)

    def test_stun_falls_back_after_incompatible_server_and_pins_selected_address(self):
        answers=[[(2,2,17,'',('1.1.1.1',3478))],[(2,2,17,'',('8.8.4.4',19302))]]
        with patch.object(gateway.socket,'getaddrinfo',side_effect=answers), \
             patch.object(gateway,'probe_asterisk_stun',side_effect=[TimeoutError(),'8.8.8.8']) as probe:
            public,peer,host=gateway.select_asterisk_stun('192.0.2.10')
        self.assertEqual((public,peer,host),('8.8.8.8',('8.8.4.4',19302),'stun.l.google.com'))
        self.assertEqual(probe.call_count,2)
        files=gateway.render(gateway_options(),public,['1.1.1.1'],stun_endpoint=peer)
        self.assertIn('stunaddr=8.8.4.4:19302',files['rtp.conf'])

    def test_config_has_only_native_dial_target_and_tls_verified_provider(self):
        files=gateway.render(gateway_options(),'8.8.8.8',['1.1.1.1'])
        self.assertIn('verify_server=yes',files['pjsip.conf'])
        self.assertIn('transport=tls',files['pjsip.conf'])
        self.assertIn('allow_transfer=no',files['pjsip.conf'])
        self.assertIn('PJSIP/native/sip:comelit@192.0.2.10:5062',files['extensions.conf'])
        self.assertEqual(files['extensions.conf'].count('Dial('),1)
        self.assertNotIn('System(',''.join(files.values()))
        self.assertIn('L(120000)',files['extensions.conf'])
        self.assertNotIn('00000002',''.join(files.values()))
        self.assertNotIn('button.press',''.join(files.values()))

    def test_reject_config_injection_and_wrong_address(self):
        for key,value in [('sip_username','x\n[attack]'),('sip_password','p\n[attack]'),
                          ('bridge_key',KEY+'\n'),('ha_ip','8.8.8.8'),('ha_sip_port',5064),
                          ('public_media_ip','192.0.2.1'),('turn_server','test;bad')]:
            options=gateway_options();options[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):
                gateway.validate(options)
        options=gateway_options();options['sip_password']='a;b\\c123'
        self.assertIn('password=a\\;b\\c123',gateway.render(options,'8.8.8.8',['1.1.1.1'])['pjsip.conf'])

    def test_stun_transaction_and_address_validation(self):
        tx=bytes(range(12))
        value=struct.pack('!BBHI',0,1,40000^(gateway.COOKIE>>16),0x08080808^gateway.COOKIE)
        packet=struct.pack('!HHI',0x101,12,gateway.COOKIE)+tx+struct.pack('!HH',0x20,8)+value
        self.assertEqual(gateway.stun_address(packet,tx),'8.8.8.8')
        for data,transaction in [(packet,b'bad'*4),(packet[:-1],tx),(packet[:20],tx)]:
            with self.assertRaises(ValueError):gateway.stun_address(data,transaction)


class StunProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_udp_preflight_uses_asterisk_legacy_format(self):
        packets=[]
        class Peer(asyncio.DatagramProtocol):
            def connection_made(self,transport):self.transport=transport
            def datagram_received(self,data,peer):
                packets.append(data)
                value=struct.pack('!BBHI',0,1,peer[1],0x08080808)
                self.transport.sendto(struct.pack('!HH',0x101,12)+data[4:20]
                                      +struct.pack('!HH',1,8)+value,peer)
        transport,_=await asyncio.get_running_loop().create_datagram_endpoint(
            Peer,local_addr=('127.0.0.1',0))
        try:
            mapped=await asyncio.to_thread(gateway.probe_asterisk_stun,'127.0.0.1',
                                          transport.get_extra_info('sockname'),timeout=0.5)
            self.assertEqual(mapped,'8.8.8.8')
            self.assertEqual(len(packets),1)
            self.assertEqual(packets[0][:4],b'\x00\x01\x00\x00')
            self.assertEqual(len(packets[0]),20)
        finally:transport.close()
