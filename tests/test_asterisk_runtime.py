"""Optional real Asterisk/TLS/Digest/RTP test, entirely on 127.0.0.0/8.

Set COMELIT_ASTERISK_RUNTIME to an extracted Alpine runtime. No user credentials,
public SIP registration, microphone, HA instance or real door is accessed.
"""
import asyncio
import hashlib
import os
from pathlib import Path
import re
import ssl
import struct
import subprocess
import tempfile
import unittest

import test_sip_outgoing as outgoing_fixture
from test_audio import rtp
from test_remote import gateway, gateway_options, KEY


RUNTIME = os.environ.get('COMELIT_ASTERISK_RUNTIME')


class StunPeer(asyncio.DatagramProtocol):
    def __init__(self):
        self.requests=0

    def connection_made(self,transport):
        self.transport=transport

    def datagram_received(self,data,peer):
        if len(data)<20 or data[:2] != b'\x00\x01':return
        self.requests+=1
        address=bytes(map(int,peer[0].split('.')))
        attr=struct.pack('!HHBBH',1,8,0,1,peer[1])+address
        self.transport.sendto(struct.pack('!HH',0x101,len(attr))+data[4:20]+attr,peer)


def message(data):
    head, body=data.split(b'\r\n\r\n',1)
    lines=head.decode().split('\r\n')
    headers={}
    for line in lines[1:]:
        key,value=line.split(':',1)
        headers.setdefault(key.lower(),[]).append(value.strip())
    return lines[0],headers,body


def reply(msg, code, reason, extras=()):
    _, h, _=msg
    lines=[f'SIP/2.0 {code} {reason}']
    for v in h['via']: lines.append('Via: '+v)
    lines += ['From: '+h['from'][0], 'To: '+h['to'][0]+';tag=mock-provider',
              'Call-ID: '+h['call-id'][0], 'CSeq: '+h['cseq'][0], *extras, 'Content-Length: 0']
    return ('\r\n'.join(lines)+'\r\n\r\n').encode()


@unittest.skipUnless(RUNTIME, 'Set COMELIT_ASTERISK_RUNTIME for the actual Asterisk engine test')
class AsteriskRuntimeTests(unittest.IsolatedAsyncioTestCase):
    endpoint = outgoing_fixture.OutgoingSipTests.endpoint

    async def asyncSetUp(self):
        await outgoing_fixture.OutgoingSipTests.asyncSetUp(self)
        self.process=self.server=self.writer=self.log=self.temp=None
        self.addAsyncCleanup(self.cleanup_runtime)
        self.client.settings.remote_gateway_key=KEY
        self.remote_audio=await self.endpoint('127.0.0.2')
        self.stun=StunPeer()
        await asyncio.get_running_loop().create_datagram_endpoint(lambda:self.stun,local_addr=('127.0.0.4',0))
        self.sockets.append(self.stun.transport)
        self.temp=tempfile.TemporaryDirectory(prefix='ast-',dir=Path(__file__).parents[2])
        self.root=Path(self.temp.name)
        self.runtime=Path(RUNTIME).absolute()
        self.cmd=[str(self.runtime/'lib/ld-musl-x86_64.so.1'),'--library-path',
                  str(self.runtime/'lib')+':'+str(self.runtime/'usr/lib'),
                  str(self.runtime/'usr/sbin/asterisk')]
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                        '-keyout',str(self.root/'key.pem'),'-out',str(self.root/'cert.pem'),
                        '-subj','/CN=127.0.0.2','-addext','subjectAltName=IP:127.0.0.2'],
                       check=True,capture_output=True)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.root/'cert.pem'),str(self.root/'key.pem'))
        self.messages=asyncio.Queue()
        self.registered=asyncio.Event()
        self.writer=None
        self.provider_error=None
        self.password='Test;Only\\Password123'
        self.server=await asyncio.start_server(self.provider,'127.0.0.2',9091,ssl=context)
        options=gateway_options();options['ha_sip_port']=self.bridge.local_port
        options['sip_password']=self.password
        self.files=gateway.render(options,'8.8.8.8',['127.0.0.2'],str(self.root),
                                  str(self.runtime/'usr/lib/asterisk/modules'),test_provider='127.0.0.2',
                                  stun_endpoint=self.stun.transport.get_extra_info('sockname'))
        self.files={n:v.replace('192.0.2.10','127.0.0.1').replace('8.8.8.8','127.0.0.1')
                       .replace('/etc/ssl/cert.pem',str(self.root/'cert.pem'))
                    for n,v in self.files.items()}
        gateway.write_config(self.files,self.root)
        self.log=(self.root/'console.log').open('w+')
        self.process=await asyncio.create_subprocess_exec(*self.cmd,'-f','-q','-C',str(self.root/'asterisk.conf'),
                                                          stdout=self.log,stderr=self.log)
        try:
            await asyncio.wait_for(self.registered.wait(),10)
        except TimeoutError:
            self.log.flush()
            raise AssertionError((self.root/'console.log').read_text()[-20000:])
        self.assertIsNone(self.provider_error)

    async def asyncTearDown(self):
        pass

    async def cleanup_runtime(self):
        if getattr(self,'process',None) and self.process.returncode is None:
            self.process.terminate()
            try: await asyncio.wait_for(self.process.wait(),4)
            except TimeoutError:
                self.process.kill();await self.process.wait()
        if self.writer:
            self.writer.close()
        if self.server:
            self.server.close();await self.server.wait_closed()
        if self.log:self.log.close()
        await outgoing_fixture.OutgoingSipTests.asyncTearDown(self)
        if self.temp:self.temp.cleanup()

    async def provider(self, reader, writer):
        self.writer=writer
        try:
            while True:
                header=await reader.readuntil(b'\r\n\r\n')
                if header == b'\r\n\r\n':
                    writer.write(b'\r\n');await writer.drain();continue
                length=int(re.search(rb'Content-Length:\s*(\d+)',header,re.I)[1])
                msg=message(header+await reader.readexactly(length))
                start,h,body=msg
                if start.startswith('REGISTER '):
                    auth=h.get('authorization')
                    if not auth:
                        writer.write(reply(msg,401,'Unauthorized',[
                            'WWW-Authenticate: Digest realm="sip.antisip.com", nonce="fixture_nonce", algorithm=MD5, qop="auth"']))
                    else:
                        fields={
                            name:a or b for name,a,b in re.findall(r'(\w+)=(?:"([^"]*)"|([^, ]+))',auth[0])}
                        md5=lambda text:hashlib.md5(text.encode()).hexdigest()
                        ha1=md5('test_only_123:sip.antisip.com:'+self.password)
                        ha2=md5('REGISTER:'+fields['uri'])
                        expected=md5(':'.join((ha1,'fixture_nonce',fields['nc'],fields['cnonce'],'auth',ha2)))
                        if fields['response'] != expected:
                            self.provider_error='Invalid real Asterisk Digest response'
                        self.contact=h['contact'][0]
                        writer.write(reply(msg,200,'OK',['Contact: '+self.contact+';expires=300','Expires: 300']))
                        self.registered.set()
                    await writer.drain()
                else:
                    await self.messages.put(msg)
        except (asyncio.IncompleteReadError,ConnectionError):
            return

    async def recv(self, final=False):
        async with asyncio.timeout(8):
            while True:
                msg=await self.messages.get()
                if msg[0].startswith('SIP/2.0 '):
                    if not final or int(msg[0].split()[1]) >= 200:return msg

    def send(self, method='INVITE', answer=None):
        contact=re.search(r'<([^>]+)>',self.contact)[1]
        body=(f'v=0\r\no=phone 1 1 IN IP4 127.0.0.2\r\ns=Phone\r\nc=IN IP4 127.0.0.2\r\n'
              f't=0 0\r\nm=audio {self.remote_audio.port} RTP/AVP 8\r\na=rtpmap:8 PCMA/8000\r\na=sendrecv\r\n').encode() if method=='INVITE' else b''
        to=answer[1]['to'][0] if answer else '<sip:test_only_123@sip.antisip.com>'
        if method in ('ACK','BYE') and answer:
            contact=re.search(r'<([^>]+)>',answer[1]['contact'][0])[1]
        lines=[f'{method} {contact} SIP/2.0',
               'Via: SIP/2.0/TLS 127.0.0.2:9091;branch=z9hG4bK-fixture-'+method,
               'From: <sip:phone@127.0.0.2>;tag=phonefixture','To: '+to,
               'Call-ID: external-fixture',f'CSeq: {2 if method=="BYE" else 1} {method}',
               'Max-Forwards: 70','Contact: <sip:phone@127.0.0.2:9091;transport=tls>']
        if body:lines.append('Content-Type: application/sdp')
        lines.append(f'Content-Length: {len(body)}')
        self.writer.write(('\r\n'.join(lines)+'\r\n\r\n').encode()+body)

    async def test_tls_digest_gateway_audio_both_directions_door_and_bye(self):
        self.bridge.grant_remote_audio()
        await self.full_audio_call()

    async def test_automatic_gateway_audio_without_extra_grant(self):
        self.client.settings.remote_auto_enabled=True
        self.assertFalse(self.bridge.remote_gate.available)
        await self.full_audio_call()

    async def full_audio_call(self):
        self.send()
        answer=await self.recv(final=True)
        self.assertIn(' 200 ',answer[0],(answer[0],self.bridge.diagnostics(),(self.root/'console.log').read_text()[-8000:]))
        self.assertTrue(self.bridge.session.external)
        self.assertGreater(self.stun.requests,0)
        self.send('ACK',answer)
        port=int(re.search(rb'm=audio (\d+)',answer[2])[1])
        heard=False;spoken=False
        for index in range(50):
            self.native_rx.transport.sendto(rtp(index,index*160,payload=b'\xa5'*160),
                                           ('127.0.0.1',self.client.call.audio.port))
            self.remote_audio.transport.sendto(rtp(index,index*160,payload=b'\x55'*160),('127.0.0.1',port))
            await asyncio.sleep(0.02)
            while not self.remote_audio.queue.empty():
                data,_=self.remote_audio.queue.get_nowait();heard |= data[12:]==b'\xa5'*160
            while not self.native_tx.queue.empty():
                data,_=self.native_tx.queue.get_nowait();spoken |= data[12:]==b'\x55'*160
        self.assertTrue(heard,'No native -> Asterisk -> phone audio')
        self.assertTrue(spoken,'No phone -> Asterisk -> native audio')
        await self.client.open_door()
        self.assertEqual(self.client.door_state,'transport_acknowledged')
        self.send('BYE',answer)
        bye=await self.recv(final=True)
        self.assertIn(' 200 ',bye[0])
        await asyncio.sleep(0.1)
        self.assertFalse(self.client.call.owned)

    async def test_anonymous_external_invite_without_grant_never_reaches_native(self):
        self.send()
        answer=await self.recv(final=True)
        self.assertGreaterEqual(int(answer[0].split()[1]),400)
        self.assertEqual(self.native_peer.commands(),[])
        self.assertFalse(self.client.call.owned)
