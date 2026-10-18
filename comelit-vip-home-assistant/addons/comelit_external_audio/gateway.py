"""Bounded Antisip/Asterisk sidecar. Never contains a door-release command.

Secrets are entered locally in HA add-on options, not shipped in this package.
The native integration separately authorizes one external call for 30 seconds.
No HTTP, AMI, ARI, phone extensions, recording, PSTN or provider outbound dialplan.
"""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import signal
import socket
import struct
import subprocess
import sys
import time

PROVIDER = "sip.antisip.com"
STUN = "stun.antisip.com"
STUN_SERVERS = ((STUN, 3478), ("stun.l.google.com", 19302))
COOKIE = 0x2112A442


def option_text(value, name, *, empty=False):
    if (not isinstance(value, str) or (not empty and not value)
            or len(value) > 256 or value != value.strip()
            or any(ord(c) < 32 or ord(c) > 126 for c in value)):
        raise ValueError(f"Ungültiger Wert für {name}")
    # Asterisk removes the escape before a semicolon; other backslashes are
    # literal. Doubling them would silently change the SIP password.
    return value.replace(";", "\\;")


def validate(options):
    if not re.fullmatch(r"(?=.*[a-z])[a-z0-9._-]{5,128}", options.get("sip_username", "")):
        raise ValueError("Antisip-Benutzername muss mindestens 5 Zeichen lang und kleingeschrieben sein")
    option_text(options.get("sip_password"), "sip_password")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", options.get("bridge_key", "")):
        raise ValueError("Gateway-Schlüssel: 32–128 Buchstaben, Ziffern, _ oder -")
    ip = ipaddress.IPv4Address(options.get("ha_ip", ""))
    network = ipaddress.IPv4Network(options.get("local_network", ""))
    if not ip.is_private or ip.is_loopback or ip.is_unspecified or ip not in network:
        raise ValueError("HA-IP muss eine private LAN-Adresse im angegebenen lokalen Netz sein")
    port = options.get("ha_sip_port")
    if type(port) is not int or not 1024 <= port <= 65535 or port in (5064, 5065):
        raise ValueError("HA-SIP-Port muss frei und verschieden von 5064/5065 sein")
    if options.get("public_media_ip"):
        if not ipaddress.IPv4Address(options["public_media_ip"]).is_global:
            raise ValueError("Öffentliche Medien-IP muss eine globale IPv4-Adresse sein")
    turn = options.get("turn_server", "")
    if turn:
        if not re.fullmatch(r"[a-zA-Z0-9.-]+(?::[0-9]{1,5})?", turn):
            raise ValueError("TURN-Server als Hostname oder Hostname:Port angeben")
        if ":" in turn and not 1 <= int(turn.rsplit(":", 1)[1]) <= 65535:
            raise ValueError("Ungültiger TURN-Port")
        option_text(options.get("turn_username"), "turn_username")
        option_text(options.get("turn_password"), "turn_password")


def stun_address(data, transaction):
    if len(data) < 20:
        raise ValueError("STUN-Antwort zu kurz")
    kind, size, cookie = struct.unpack("!HHI", data[:8])
    if kind != 0x0101 or cookie != COOKIE or data[8:20] != transaction or len(data) != 20 + size:
        raise ValueError("STUN-Antwort passt nicht zur Anfrage")
    offset, mapped = 20, None
    while offset < len(data):
        if offset + 4 > len(data):
            raise ValueError("STUN-Attribut abgeschnitten")
        typ, length = struct.unpack("!HH", data[offset:offset + 4])
        offset += 4
        value = data[offset:offset + length]
        if len(value) != length:
            raise ValueError("STUN-Attribut abgeschnitten")
        if typ in (0x20, 1) and length == 8 and value[1] == 1:
            raw = int.from_bytes(value[4:8], "big") ^ (COOKIE if typ == 0x20 else 0)
            address = ipaddress.IPv4Address(raw)
            if not address.is_global:
                raise ValueError("STUN liefert keine öffentliche IPv4-Adresse")
            if typ == 0x20:
                return str(address)
            mapped = str(address)
        offset += (length + 3) & ~3
    if mapped:
        return mapped
    raise ValueError("STUN enthält keine IPv4-Abbildung")


def discover_public_ip(local_ip):
    peer = socket.getaddrinfo(STUN, 3478, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
    transaction = secrets.token_bytes(12)
    request = struct.pack("!HHI", 1, 0, COOKIE) + transaction
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((local_ip, 0))
        sock.connect(peer)
        sock.settimeout(2)
        for _ in range(3):
            sock.send(request)
            try:
                return stun_address(sock.recv(2048), transaction)
            except TimeoutError:
                pass
    raise OSError("Keine STUN-Antwort; öffentliche Medien-IP konnte nicht ermittelt werden")


def legacy_stun_address(data, transaction):
    """Validate the RFC3489 MAPPED-ADDRESS used by Asterisk 20's RTP engine.

    A successful RFC5389/XOR-only probe does not establish compatibility with
    that engine. Never substitute a guessed RTP port or a different socket's map.
    """
    if len(transaction) != 16 or len(data) < 20:
        raise ValueError("STUN-Antwort zu kurz")
    kind, size = struct.unpack("!HH", data[:4])
    if kind != 0x0101 or size % 4 or len(data) != 20 + size or data[4:20] != transaction:
        raise ValueError("STUN-Antwort passt nicht zur Asterisk-Anfrage")
    offset, mapped = 20, None
    while offset < len(data):
        if offset + 4 > len(data):
            raise ValueError("STUN-Attribut abgeschnitten")
        typ, length = struct.unpack("!HH", data[offset:offset+4])
        offset += 4
        padded = (length + 3) & ~3
        if offset + padded > len(data):
            raise ValueError("STUN-Attribut abgeschnitten")
        value = data[offset:offset+length]
        if typ == 1 and length == 8 and value[1] == 1:
            address = ipaddress.IPv4Address(value[4:8])
            if not address.is_global or not int.from_bytes(value[2:4], "big"):
                raise ValueError("STUN liefert keine öffentliche IPv4-Abbildung")
            mapped = str(address)
        offset += padded
    if mapped is None:
        raise ValueError("STUN liefert nicht das von Asterisk benötigte MAPPED-ADDRESS")
    return mapped


def probe_asterisk_stun(local_ip, peer, *, timeout=2):
    transaction = secrets.token_bytes(16)
    request = struct.pack("!HH", 1, 0) + transaction
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((local_ip, 0))
        sock.connect(peer)
        sock.settimeout(timeout)
        for _ in range(2):
            sock.send(request)
            try:
                return legacy_stun_address(sock.recv(2048), transaction)
            except (TimeoutError, ValueError):
                pass
    raise OSError("Keine für Asterisk verwendbare STUN-Antwort")


def select_asterisk_stun(local_ip, public_override="", *, servers=STUN_SERVERS):
    """Check actual legacy format before committing the RTP engine to a server."""
    failures = []
    for host, port in servers:
        try:
            peers = list(dict.fromkeys(item[4] for item in
                socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)))[:2]
            for peer in peers:
                try:
                    mapped = probe_asterisk_stun(local_ip, peer)
                    return public_override or mapped, peer, host
                except (OSError, ValueError):
                    pass
        except OSError:
            pass
        failures.append(host)
    raise OSError("STUN für Asterisk nicht erreichbar/kompatibel: " + ", ".join(failures)
                  + ". Ausgehende UDP-Verbindung prüfen; kein SIP-Portwechsel erforderlich.")


def render(options, public_ip, provider_ips, root="/run/comelit-audio", modules="/usr/lib/asterisk/modules",
           *, test_provider=None, stun_endpoint=None):
    """Render fixed-purpose configs. test_provider is only used in loopback tests."""
    validate(options)
    ipaddress.IPv4Address(public_ip)
    ips = [str(ipaddress.IPv4Address(ip)) for ip in provider_ips]
    if not ips:
        raise ValueError("Keine IPv4-Adresse für Antisip gefunden")
    local = options["ha_ip"]
    user = options["sip_username"]
    password = option_text(options["sip_password"], "sip_password")
    key = options["bridge_key"]
    provider = test_provider or PROVIDER
    root = str(Path(root).absolute())
    tls_port = 9091
    stun_server = f"{STUN}:3478"
    if stun_endpoint is not None:
        stun_host = str(ipaddress.IPv4Address(stun_endpoint[0]))
        if type(stun_endpoint[1]) is not int or not 1 <= stun_endpoint[1] <= 65535:
            raise ValueError("Ungültiger STUN-Port")
        stun_server = f"{stun_host}:{stun_endpoint[1]}"
    files = {}
    files["asterisk.conf"] = f"""[directories]
astetcdir => {root}
astmoddir => {modules}
astvarlibdir => {root}/var
astdbdir => {root}/var
astkeydir => {root}/keys
astdatadir => {root}/var
astagidir => {root}/agi
astspooldir => {root}/spool
astrundir => {root}
astlogdir => {root}/log
[options]
verbose = 0
debug = 0
maxcalls = 2
hideconnect = yes
"""
    files["pjsip.conf"] = fr"""[global]
type=global
user_agent=Comelit-External-Audio/0.1.1
endpoint_identifier_order=ip
keep_alive_interval=25

[local-udp]
type=transport
protocol=udp
bind={local}:5064
local_net=0.0.0.0/0

[provider-tls]
type=transport
protocol=tls
bind={local}:5065
method=tlsv1_2
ca_list_file=/etc/ssl/cert.pem
verify_server=yes
verify_client=no
require_client_cert=no
local_net={options['local_network']}
local_net=127.0.0.0/8
external_media_address={public_ip}
external_signaling_address={public_ip}
symmetric_transport=yes

[antisip-auth]
type=auth
auth_type=userpass
username={user}
password={password}

[antisip-registration]
type=registration
transport=provider-tls
outbound_auth=antisip-auth
server_uri=sip:{provider}\;transport=tls
client_uri=sip:{user}@{PROVIDER}\;transport=tls
outbound_proxy=sip:{provider}:{tls_port}\;transport=tls\;lr
contact_user=comelit-inbound
expiration=300
retry_interval=60
max_retries=10000
auth_rejection_permanent=yes
line=yes
endpoint=antisip-in
support_path=yes
support_outbound=yes

[antisip-in]
type=endpoint
transport=provider-tls
context=from-antisip
disallow=all
allow=alaw,ulaw
direct_media=no
media_address={local}
bind_rtp_to_media_address=yes
force_rport=yes
rewrite_contact=yes
rtp_symmetric=yes
ice_support=yes
rtp_keepalive=5
rtp_timeout=25
rtp_timeout_hold=25
allow_transfer=no
allow_subscribe=no
timers=no
trust_id_inbound=no

[antisip-identify]
type=identify
endpoint=antisip-in
match={','.join(ips)}

[native]
type=endpoint
transport=local-udp
context=deny-all
disallow=all
allow=alaw,ulaw
direct_media=no
media_address={local}
bind_rtp_to_media_address=yes
ice_support=no
rtp_symmetric=no
force_rport=yes
from_user=comelit-gateway
contact_user=comelit-gateway
allow_transfer=no
allow_subscribe=no
timers=no
"""
    files["extensions.conf"] = f"""[general]
static=yes
writeprotect=yes
autofallthrough=yes

[from-antisip]
exten => comelit-inbound,1,Set(GROUP()=comelit-remote)
 same => n,GotoIf($[${{GROUP_COUNT(comelit-remote)}} > 1]?busy)
 same => n,Set(TIMEOUT(absolute)=150)
 same => n,Dial(PJSIP/native/sip:comelit@{local}:{options['ha_sip_port']},20,b(native-auth^s^1)L(120000))
 same => n,Hangup()
 same => n(busy),Hangup(17)

[native-auth]
exten => s,1,Set(PJSIP_HEADER(add,X-Comelit-Gateway)={key})
 same => n,Return()

[deny-all]
; No matching extension: reject all inbound requests in this context.
"""
    files["rtp.conf"] = f"""[general]
rtpstart=20000
rtpend=20039
strictrtp=yes
probation=4
icesupport=yes
stunaddr={stun_server}
stun_deny=0.0.0.0/0
stun_permit={local}/32
ice_deny=0.0.0.0/0
ice_permit={local}/32
"""
    if options.get("turn_server"):
        files["rtp.conf"] += (f"turnaddr={options['turn_server']}\n"
            f"turnusername={option_text(options['turn_username'], 'turn_username')}\n"
            f"turnpassword={option_text(options['turn_password'], 'turn_password')}\n")
    files["modules.conf"] = """[modules]
autoload=no
load=res_pjproject.so
load=res_sorcery_config.so
load=res_sorcery_memory.so
load=res_sorcery_astdb.so
load=res_resolver_unbound.so
load=res_pjsip.so
load=res_pjsip_pubsub.so
load=res_pjsip_authenticator_digest.so
load=res_pjsip_outbound_authenticator_digest.so
load=res_pjsip_endpoint_identifier_ip.so
load=res_pjsip_outbound_registration.so
load=res_pjsip_session.so
load=res_pjsip_sdp_rtp.so
load=res_pjsip_nat.so
load=res_pjsip_header_funcs.so
load=res_pjsip_dlg_options.so
load=res_pjsip_rfc3326.so
load=res_pjsip_sips_contact.so
load=res_rtp_asterisk.so
load=chan_pjsip.so
load=codec_alaw.so
load=codec_ulaw.so
load=format_pcm.so
load=bridge_simple.so
load=bridge_builtin_features.so
load=bridge_builtin_interval_features.so
load=res_timing_timerfd.so
load=pbx_config.so
load=app_dial.so
load=app_stack.so
load=func_channel.so
load=func_timeout.so
load=func_groupcount.so
load=func_logic.so
"""
    files["http.conf"] = "[general]\nenabled=no\n"
    files["manager.conf"] = "[general]\nenabled=no\nwebenabled=no\n"
    files["ari.conf"] = "[general]\nenabled=no\n"
    files["cdr.conf"] = "[general]\nenable=no\n"
    files["cel.conf"] = "[general]\nenable=no\n"
    files["logger.conf"] = "[general]\n[logfiles]\nconsole => warning,error\n"
    for name in ("stasis.conf", "ccss.conf", "features.conf", "indications.conf",
                 "acl.conf", "udptl.conf", "pjproject.conf"):
        files[name] = "; Intentionally use Asterisk defaults; no additional services.\n"
    return files


def write_config(files, root, uid=None, gid=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("var", "keys", "agi", "spool", "log"):
        (root/name).mkdir(exist_ok=True, mode=0o700)
    for name, value in files.items():
        p = root/name
        p.write_text(value)
        p.chmod(0o600)
    if uid is not None:
        for p in [root, *root.rglob('*')]:
            os.chown(p, uid, gid)


def main():
    options = json.loads(Path('/data/options.json').read_text())
    validate(options)
    root = '/run/comelit-audio'
    account = pwd.getpwnam('asterisk')
    stopping = False
    process = None
    def stop(_signal, _frame):
        nonlocal stopping
        stopping = True
        if process is not None and process.poll() is None:
            process.terminate()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        public, stun_peer, stun_host = select_asterisk_stun(options['ha_ip'], options.get('public_media_ip', ''))
        ips = sorted({item[4][0] for item in socket.getaddrinfo(PROVIDER, 9091, socket.AF_INET, socket.SOCK_STREAM)})
        write_config(render(options, public, ips, root, stun_endpoint=stun_peer), root, account.pw_uid, account.pw_gid)
        print('Comelit externes Audio startet. SIP: TLS; Codecs: PCMA/PCMU; STUN/ICE aktiv.', flush=True)
        print(f'STUN-Vorabprüfung für Asterisk erfolgreich: {stun_host}:{stun_peer[1]}; Medienbindung {options["ha_ip"]}.', flush=True)
        print('Direkter Loxone-Rufstart: Automatikmodus in den Comelit-Integrationsoptionen aktivieren.', flush=True)
        process = subprocess.Popen(['/usr/sbin/asterisk', '-f', '-q', '-C', root+'/asterisk.conf',
                                    '-U', 'asterisk', '-G', 'asterisk'])
        last = None
        next_check = time.monotonic() + 120
        refresh = False
        def cli(command):
            return subprocess.run(['/usr/sbin/asterisk', '-C', root+'/asterisk.conf', '-rx', command],
                                  capture_output=True, text=True, timeout=3).stdout
        while process.poll() is None:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    match = re.search(r'\b(Registered|Rejected|Unregistered|Stopped)\b',cli('pjsip show registrations'))
                    state = match.group(1) if match else 'Nicht bereit'
                    if state != last:
                        print('Antisip-Registrierung: '+state, flush=True)
                        last = state
                    if time.monotonic() >= next_check:
                        next_check = time.monotonic() + 120
                        try:
                            new_public, new_stun_peer, _ = select_asterisk_stun(options['ha_ip'], options.get('public_media_ip', ''))
                            new_ips = sorted({item[4][0] for item in socket.getaddrinfo(PROVIDER,9091,socket.AF_INET,socket.SOCK_STREAM)})
                            refresh = refresh or new_public != public or new_ips != ips or new_stun_peer != stun_peer
                        except (OSError,ValueError):
                            print('Adressprüfung vorübergehend fehlgeschlagen; bestehende Verbindung bleibt bestehen.',flush=True)
                    if refresh and re.search(r'^0 active channels$',cli('core show channels count'),re.M):
                        print('Netzadresse geändert; Audio-Gateway wird im Leerlauf neu angemeldet.',flush=True)
                        process.terminate()
                        try: process.wait(timeout=8)
                        except subprocess.TimeoutExpired:
                            process.kill();process.wait()
                        break
                except subprocess.TimeoutExpired:
                    print('Asterisk-Statusabfrage verzögert.',flush=True)
        if stopping:
            return 0
        if not refresh:
            return process.returncode or 1
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, subprocess.TimeoutExpired) as exc:
        # Never dump options or generated config: they contain credentials.
        print('Audio-Gateway konnte nicht starten: '+str(exc), file=sys.stderr)
        sys.exit(1)
