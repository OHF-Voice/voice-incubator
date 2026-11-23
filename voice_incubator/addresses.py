"""Satellites by address: connected to at a host the user names, not only
found by mDNS (where mDNS does not cross VLANs, or is kept out of them).

Each entry is a host -- a DNS name, an IPv4 address or a bracketed IPv6 one --
with an optional port (6055 by default). The server keeps a session to each,
reconnecting with its usual backoff for as long as the entry stands; once a
satellite says who it is (HELLO's MAC), mDNS finding the same one starts no
second session.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

DEFAULT_PORT = 6055
MAX_ADDRESSES = 64

# A DNS name (RFC 1123 section 2.1): labels of letters, digits and hyphens,
# not starting or ending with one, at most 63 octets each, 253 in all.
_LABEL = r"(?!-)[A-Za-z0-9-]{1,63}(?<!-)"
_HOSTNAME = re.compile(rf"^{_LABEL}(\.{_LABEL})*$")
# What an entry is made of: a host name's characters, an IP address's, a
# port's colon and an IPv6 address's brackets; nothing that would make
# urlsplit read a scheme, a user or a path.
_CHARACTERS = re.compile(r"^[A-Za-z0-9.:\[\]-]+$")


@dataclass(frozen=True, order=True)
class Address:
    host: str
    port: int = DEFAULT_PORT

    @property
    def key(self) -> str:
        """The session's key until the satellite says its MAC."""
        return f"address:{self}"

    def __str__(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return host if self.port == DEFAULT_PORT else f"{host}:{self.port}"


def parse(entry: str) -> Address:
    """One entry; ValueError says what is wrong with it. urlsplit splits the
    host from the port and an IPv6 address from its brackets; what it lets
    through (a space, a leading hyphen, 1.2.3.999, port 0) is checked here."""
    text = str(entry).strip()
    if not text:
        raise ValueError("empty")
    if not _CHARACTERS.match(text):
        raise ValueError(f"{text}: not a host name or an address")
    if text.count(":") > 1 and not text.startswith("["):
        # A bare IPv6 address: urlsplit would take its last group for a port.
        try:
            ip = ipaddress.IPv6Address(text)
        except ValueError:
            raise ValueError(f"{text}: not an IPv6 address") from None
        return Address(_host_ip(ip, text))
    try:
        split = urlsplit(f"//{text}")
        port = split.port
    except ValueError as err:
        raise ValueError(f"{text}: {err}") from None
    host = split.hostname or ""
    if port is None:
        if text.endswith(":"):
            raise ValueError(f"{text}: the port is 1 to 65535")
        port = DEFAULT_PORT
    elif not 1 <= port <= 65535:
        raise ValueError(f"{text}: the port is 1 to 65535")
    if text.startswith("["):
        try:
            ip6 = ipaddress.IPv6Address(host)
        except ValueError:
            raise ValueError(f"{text}: [ ] holds an IPv6 address") from None
        return Address(_host_ip(ip6, text), port)
    try:
        ip4 = ipaddress.IPv4Address(host)
    except ValueError:
        pass
    else:
        return Address(_host_ip(ip4, text), port)
    host = host.rstrip(".")
    if host.replace(".", "").isdigit():
        raise ValueError(f"{text}: not an IPv4 address")
    if len(host) > 253 or not _HOSTNAME.match(host):
        raise ValueError(f"{text}: not a host name")
    if host.rsplit(".", 1)[-1].isdigit():
        # A top-level domain is never all digits (RFC 3696 section 2): it
        # would read as an IPv4 address.
        raise ValueError(f"{text}: not a host name (its last part is a number)")
    return Address(host, port)


def _host_ip(ip: "ipaddress.IPv4Address | ipaddress.IPv6Address", text: str) -> str:
    """An address a TCP connection can reach a host at: not a multicast
    group, the unspecified address or a reserved one (240.0.0.0/4, the
    broadcast address with it). Loopback and link-local stay: both are
    hosts."""
    if ip.is_multicast:
        raise ValueError(f"{text}: a multicast address, not a host's")
    if ip.is_unspecified:
        raise ValueError(f"{text}: the unspecified address, not a host's")
    if isinstance(ip, ipaddress.IPv4Address) and ip.is_reserved:
        raise ValueError(f"{text}: a reserved address, not a host's")
    return str(ip)


_MAC = re.compile(r"^[0-9a-f]{12}$")


def text_of(entry) -> str:
    """An entry of the setting as listed: {"address": ..., "mac": ...}, or a
    string (as stored before the MAC was kept)."""
    if isinstance(entry, dict):
        return str(entry.get("address") or "")
    return str(entry)


def mac_of(entry) -> str | None:
    """The MAC of the satellite that answered at an entry, once one has."""
    mac = entry.get("mac") if isinstance(entry, dict) else None
    return mac if isinstance(mac, str) and _MAC.match(mac) else None


def with_macs(wanted: list[Address], old) -> list[dict]:
    """``wanted`` as the setting holds it: each {"address": ...}, with the MAC
    an entry of ``old`` had for the same address (a new one has none yet)."""
    known = {text_of(entry): mac_of(entry) for entry in old or ()}
    return [{"address": str(a), **({"mac": known[str(a)]} if known.get(str(a)) else {})} for a in wanted]


def parse_all(entries) -> list[Address]:
    """The list as the settings hold it, or as text: each parsed, repeats
    once, in order. ValueError names the first entry that is wrong."""
    if isinstance(entries, str):
        entries = entries.splitlines()
    out: list[Address] = []
    for entry in entries or ():
        if not text_of(entry).strip():
            continue
        address = parse(text_of(entry))
        if address not in out:
            out.append(address)
    if len(out) > MAX_ADDRESSES:
        raise ValueError(f"at most {MAX_ADDRESSES} addresses")
    return out
