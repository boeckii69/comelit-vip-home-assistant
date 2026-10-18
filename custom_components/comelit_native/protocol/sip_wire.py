"""Bounded direct-UDP SIP/SDP subset (RFC 3261/3264); no proxy or registrar."""
from dataclasses import dataclass
import ipaddress
import re
import secrets

ALIASES = {"v": "via", "f": "from", "t": "to", "i": "call-id",
           "l": "content-length", "c": "content-type", "m": "contact"}
ALLOW = "INVITE, ACK, CANCEL, BYE, OPTIONS"
REASONS = {100: "Trying", 180: "Ringing", 200: "OK", 400: "Bad Request",
           403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
           415: "Unsupported Media Type", 420: "Bad Extension",
           480: "Temporarily Unavailable", 481: "Call/Transaction Does Not Exist",
           482: "Loop Detected", 486: "Busy Here", 487: "Request Terminated",
           488: "Not Acceptable Here", 500: "Server Internal Error", 503: "Service Unavailable"}


class SipError(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def parameter(value, name):
    match = re.search(r"(?:^|;)\s*" + re.escape(name) + r"=([^;\s,]+)", value, re.I)
    return match.group(1) if match else ""


def uri(value):
    match = re.search(r"(?:<|^)(sip:[^<>\s]+)", value.strip(), re.I)
    if not match:
        raise SipError("SIP-URI fehlt")
    return match.group(1)


@dataclass
class SipMessage:
    method: str
    target: str
    status: int
    headers: dict
    body: bytes
    cseq: int
    cseq_method: str

    def get(self, key):
        return self.headers.get(key, [""])[0]

    @property
    def branch(self):
        return parameter(self.get("via"), "branch")

    @property
    def key(self):
        return self.get("call-id"), self.cseq, self.cseq_method, self.branch


def parse_sip(data):
    if len(data) > 8192 or b"\r\n\r\n" not in data or b"\0" in data:
        raise SipError("Ungültige SIP-Nachrichtenlänge oder Trennung")
    head, body = data.split(b"\r\n\r\n", 1)
    lines = head.decode("utf-8", errors="strict").split("\r\n")
    if len(lines) > 80 or any(len(line) > 2048 for line in lines):
        raise SipError("SIP-Headerlimit überschritten")
    start = lines.pop(0).split(" ", 2)
    if len(start) != 3:
        raise SipError("Ungültige SIP-Startzeile")
    if start[0] == "SIP/2.0":
        method, target, status = "", "", int(start[1])
        if not 100 <= status <= 699:
            raise SipError("Ungültiger SIP-Status")
    else:
        method, target, version = start
        status = 0
        if version != "SIP/2.0" or not re.fullmatch("[A-Z]+", method):
            raise SipError("SIP-Version oder Methode unbekannt")
    unfolded = []
    for line in lines:
        if any(ord(c) < 32 and c != "\t" for c in line):
            raise SipError("Steuerzeichen im SIP-Header")
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += " " + line.strip()
        else:
            unfolded.append(line)
    headers = {}
    for line in unfolded:
        if ":" not in line:
            raise SipError("SIP-Header ohne Doppelpunkt")
        key, value = line.split(":", 1)
        key = ALIASES.get(key.strip().lower(), key.strip().lower())
        headers.setdefault(key, []).append(value.strip())
    for name in ("from", "to", "call-id", "cseq", "content-length", "content-type", "contact"):
        if len(headers.get(name, [])) > 1:
            raise SipError("Doppelter SIP-Einzelheader")
    for name in ("via", "from", "to", "call-id", "cseq"):
        if not headers.get(name) or not headers[name][0]:
            raise SipError("Pflichtheader fehlt")
    if len(headers["call-id"][0]) > 256:
        raise SipError("Call-ID zu lang")
    content_length = int(headers.get("content-length", [str(len(body))])[0])
    if content_length != len(body):
        raise SipError("Content-Length passt nicht zum UDP-Datagramm")
    seq = headers["cseq"][0].split()
    if len(seq) != 2 or not seq[0].isdigit() or not 0 <= int(seq[0]) < 2**31:
        raise SipError("Ungültige CSeq")
    if method and seq[1] != method:
        raise SipError("CSeq-Methode passt nicht")
    message = SipMessage(method, target, status, headers, body, int(seq[0]), seq[1])
    if not message.get("via").upper().startswith("SIP/2.0/UDP ") or not message.branch.startswith("z9hG4bK"):
        raise SipError("Nur SIP/2.0 über UDP mit Transaktions-ID unterstützt")
    return message


def response(message, code, peer, tag="", body=b"", contact="", extra=()):
    via = message.get("via")
    via = re.sub(r";(?:received|rport)(?:=[^;,\s]*)?", "", via, flags=re.I)
    via += f";received={peer[0]};rport={peer[1]}"
    to = message.get("to")
    if code > 100 and tag and not parameter(to, "tag"):
        to += ";tag=" + tag
    lines = [f"SIP/2.0 {code} {REASONS[code]}", "Via: " + via]
    lines += ["Via: " + value for value in message.headers.get("via", [])[1:]]
    lines += ["From: " + message.get("from"), "To: " + to,
              "Call-ID: " + message.get("call-id"), "CSeq: " + message.get("cseq"),
              "User-Agent: Comelit-Native/0.1.12", "Allow: " + ALLOW]
    if contact:
        lines.append("Contact: <" + contact + ">")
    lines.extend(extra)
    if body:
        lines.append("Content-Type: application/sdp")
    lines.append(f"Content-Length: {len(body)}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


@dataclass
class AudioOffer:
    ip: str
    port: int
    rtcp_port: int
    pt: int
    direction: str
    media: list
    audio_index: int


def parse_sdp(body, peer_ip):
    if len(body) > 4096:
        raise SipError("SDP zu lang", 488)
    text = body.decode("ascii")
    lines = text.replace("\r\n", "\n").splitlines()
    if not lines or lines[0] != "v=0" or len(lines) > 80:
        raise SipError("Ungültiges SDP", 488)
    media, current = [], None
    session_ip, session_direction = "", "sendrecv"
    for line in lines:
        if line.startswith("m="):
            parts = line[2:].split()
            if len(parts) < 4 or not parts[1].isdigit():
                raise SipError("Ungültige SDP-Medienzeile", 488)
            current = {"kind": parts[0], "port": int(parts[1]), "proto": parts[2],
                       "formats": parts[3:], "ip": None, "direction": None, "rtpmaps": {}}
            media.append(current)
        elif line.startswith("c="):
            parts = line[2:].split()
            if len(parts) != 3 or parts[:2] != ["IN", "IP4"]:
                raise SipError("Nur IPv4-Medien unterstützt", 488)
            if current is None: session_ip = parts[2]
            else: current["ip"] = parts[2]
        elif line in ("a=sendrecv", "a=sendonly", "a=recvonly", "a=inactive"):
            if current is None: session_direction = line[2:]
            else: current["direction"] = line[2:]
        elif current is not None and line.startswith("a=rtpmap:"):
            fields = line[9:].split()
            if len(fields) == 2:
                current["rtpmaps"][fields[0]] = fields[1].upper()
        elif current is not None and line.startswith("a=rtcp:"):
            fields = line[7:].split()
            if not fields or not fields[0].isdigit():
                raise SipError("Ungültiges RTCP-Ziel", 488)
            if len(fields) > 1 and fields[1:] != ["IN", "IP4", peer_ip]:
                raise SipError("Abweichendes RTCP-Ziel", 488)
            current["rtcp"] = int(fields[0])
        elif current is not None and line.startswith("a=maxptime:"):
            if float(line[11:]) < 20:
                raise SipError("Paketzeit unter 20 ms nicht unterstützt", 488)
    candidates = [(i, item) for i, item in enumerate(media) if item["kind"] == "audio" and item["port"]]
    if len(candidates) != 1 or len(media) > 4:
        raise SipError("Genau ein aktiver Audiostream erforderlich", 488)
    index, item = candidates[0]
    ip = item["ip"] or session_ip
    if str(ipaddress.IPv4Address(ip)) != peer_ip or item["proto"] != "RTP/AVP":
        raise SipError("Nur direktes RTP/AVP zur anrufenden LAN-IP unterstützt", 488)
    port, rtcp = item["port"], item.get("rtcp", item["port"] + 1)
    if not 1 <= port <= 65535 or not 1 <= rtcp <= 65535:
        raise SipError("Ungültiger RTP/RTCP-Port", 488)
    for pt, codec in ((8, "PCMA"), (0, "PCMU")):
        if str(pt) not in item["formats"]:
            continue
        mapping = item["rtpmaps"].get(str(pt), codec + "/8000")
        if mapping in (codec + "/8000", codec + "/8000/1"):
            direction = item["direction"] or session_direction
            if direction == "inactive":
                raise SipError("Inaktives Audioangebot", 488)
            return AudioOffer(ip, port, rtcp, pt, direction, media, index)
    raise SipError("Loxone bietet kein G.711 PCMA/PT8 oder PCMU/PT0 an", 488)


def sdp_answer(offer, local_ip, port, rtcp_port):
    session_id = secrets.randbits(48)
    lines = ["v=0", f"o=comelit {session_id} 1 IN IP4 {local_ip}", "s=Comelit Native",
             f"c=IN IP4 {local_ip}", "t=0 0"]
    for index, item in enumerate(offer.media):
        if index != offer.audio_index:
            lines.append(f"m={item['kind']} 0 {item['proto']} {item['formats'][0]}")
            continue
        direction = {"sendrecv": "sendrecv", "sendonly": "recvonly", "recvonly": "sendonly"}[offer.direction]
        lines += [f"m=audio {port} RTP/AVP {offer.pt}",
                  f"a=rtpmap:{offer.pt} {'PCMA' if offer.pt == 8 else 'PCMU'}/8000",
                  f"a=rtcp:{rtcp_port} IN IP4 {local_ip}", "a=ptime:20", "a=" + direction]
    return ("\r\n".join(lines) + "\r\n").encode()
