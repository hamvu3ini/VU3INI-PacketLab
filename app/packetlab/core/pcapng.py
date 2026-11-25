"""Minimal pcapng writer (IETF draft-ietf-opsawg-pcapng) for Wireshark-compatible captures.

Frames are rebuilt from the engine's PDU dictionaries: Ethernet II, IEEE 802.1Q,
MPLS, ARP, IPv4 with a correct header checksum, ICMP echo, TCP SYN and UDP.
"""
from __future__ import annotations

import ipaddress
import struct
import time
from typing import Iterable, List


def _mac(s: str) -> bytes:
    return bytes(int(x, 16) for x in str(s).split(":"))


def _csum(b: bytes) -> int:
    if len(b) % 2:
        b += b"\0"
    s = sum(struct.unpack(f"!{len(b) // 2}H", b))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF


def frame_from_pdu(pdu: dict, seq: int = 1) -> bytes:
    eth = pdu.get("Ethernet II") or {"Source MAC": "02:00:00:00:00:01", "Destination MAC": "02:00:00:00:00:02"}
    out = _mac(eth["Destination MAC"]) + _mac(eth["Source MAC"])
    if "ARP" in pdu:
        a = pdu["ARP"]
        op = 2 if str(a["Opcode"]).startswith("2") else 1
        out += struct.pack("!H", 0x0806)
        out += struct.pack("!HHBBH", 1, 0x0800, 6, 4, op)
        out += _mac(a["Sender MAC"]) + ipaddress.ip_address(a["Sender IP"]).packed
        out += _mac(a["Target MAC"]) + ipaddress.ip_address(a["Target IP"]).packed
        return out + b"\0" * 18
    if "IEEE 802.1Q" in pdu:
        out += struct.pack("!HH", 0x8100, int(pdu["IEEE 802.1Q"]["VLAN ID"]) & 0x0FFF)
    if "MPLS" in pdu:
        m = pdu["MPLS"]
        out += struct.pack("!H", 0x8847)
        out += struct.pack("!I", (int(m["Label"]) << 12) | (1 << 8) | (int(m.get("TTL", 64)) & 0xFF))
    else:
        out += struct.pack("!H", 0x0800)
    ip = pdu.get("IPv4", {})
    proto = int(str(ip.get("Protocol", "1")).split()[0])
    if "ICMP" in pdu:
        typ = int(pdu["ICMP"]["Type"])
        payload = b"PacketLab VU3INI" * 2
        hdr = struct.pack("!BBHHH", typ, 0, 0, 0x5650, seq & 0xFFFF) + payload
        l4 = hdr[:2] + struct.pack("!H", _csum(hdr)) + hdr[4:]
    elif "TCP" in pdu:
        dport = int(pdu["TCP"].get("Destination port") or 80)
        flags = 0x02 if pdu["TCP"].get("Flags") == "SYN" else 0x12
        l4 = struct.pack("!HHIIBBHHH", 49152 + (seq % 1000), dport, seq, 0, 5 << 4, flags, 64240, 0, 0)
    elif "UDP" in pdu:
        dport = int(pdu["UDP"].get("Destination port") or 53)
        data = b"PacketLab"
        l4 = struct.pack("!HHHH", 49152 + (seq % 1000), dport, 8 + len(data), 0) + data
    else:
        l4 = b""
    src = ipaddress.ip_address(ip.get("Source", "0.0.0.0")).packed
    dst = ipaddress.ip_address(ip.get("Destination", "0.0.0.0")).packed
    total = 20 + len(l4)
    hdr = struct.pack("!BBHHHBBH4s4s", 0x45, 0, total, seq & 0xFFFF, 0, max(0, int(ip.get("TTL", 64))) & 0xFF,
                      proto, 0, src, dst)
    hdr = hdr[:10] + struct.pack("!H", _csum(hdr)) + hdr[12:]
    return out + hdr + l4


def _block(btype: int, body: bytes) -> bytes:
    pad = (4 - len(body) % 4) % 4
    body += b"\0" * pad
    ln = 12 + len(body)
    return struct.pack("<II", btype, ln) + body + struct.pack("<I", ln)


def _opt(code: int, val: bytes) -> bytes:
    pad = (4 - len(val) % 4) % 4
    return struct.pack("<HH", code, len(val)) + val + b"\0" * pad


def write(path: str, events: Iterable, comment: str = "VU3INI PacketLab capture") -> int:
    """Write events (engine Event objects) that carry PDUs to a pcapng file. Returns frame count."""
    shb_opts = _opt(1, comment.encode()) + _opt(4, b"VU3INI PacketLab") + struct.pack("<HH", 0, 0)
    shb = struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1) + shb_opts
    data = _block(0x0A0D0D0A, shb)
    idb = struct.pack("<HHI", 1, 0, 65535) + _opt(2, b"packetlab0") + _opt(9, b"\x06") + struct.pack("<HH", 0, 0)
    data += _block(1, idb)
    base_us = int(time.time() * 1e6)
    n = 0
    frames: List[bytes] = []
    for k, ev in enumerate(events):
        pdu = getattr(ev, "pdu", None) or (ev.get("pdu") if isinstance(ev, dict) else None)
        if not pdu or ("Ethernet II" not in pdu and "ARP" not in pdu):
            continue
        fr = frame_from_pdu(pdu, k + 1)
        t_ms = getattr(ev, "t_ms", 0.0)
        ts = base_us + int(t_ms * 1000)
        note = f"{getattr(ev, 'node', '')}: {getattr(ev, 'summary', '')}".encode()[:200]
        epb = struct.pack("<IIIII", 0, ts >> 32, ts & 0xFFFFFFFF, len(fr), len(fr)) + fr
        epb += b"\0" * ((4 - len(fr) % 4) % 4)
        epb += _opt(1, note) + struct.pack("<HH", 0, 0)
        frames.append(_block(6, epb))
        n += 1
    with open(path, "wb") as f:
        f.write(data + b"".join(frames))
    return n
