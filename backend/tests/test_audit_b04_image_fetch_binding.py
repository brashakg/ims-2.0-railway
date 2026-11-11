"""
Audit L1-F32: the external-image fetch must connect ONLY to the addresses its
SSRF check validated.

Reproduction (audit lane1/image-rehost-dns-probe.py): the check resolved a
hostname to a public address, then httpx resolved the SAME hostname again for
the connection and got 127.0.0.1, so the fetch read a loopback server.

These tests drive the real api.services.image_rehost.fetch_external_image with
the real httpx against a loopback-only HTTP(S) server and controlled DNS
answers. Every non-loopback connect is refused by the harness and recorded, so
no test can reach a real network.

Run: JWT_SECRET_KEY=test python -m pytest \
       backend/tests/test_audit_b04_image_fetch_binding.py -q
"""

import base64
import datetime
import ipaddress
import os
import socket
import ssl
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import httpx  # noqa: E402
import pytest  # noqa: E402

from api.services import image_rehost  # noqa: E402

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jF90A"
    "AAAASUVORK5CYII="
)
_PNG_ONLY = frozenset({"image/png"})
_PUBLIC = "8.8.8.8"  # never contacted: the harness refuses every non-loopback connect


class _Server:
    """A loopback HTTP server that records what reached it."""

    def __init__(self, redirect_to=None, tls_context=None):
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server API
                outer.requests.append(
                    {"path": self.path, "host": self.headers.get("Host")}
                )
                if self.path == "/redirect" and redirect_to:
                    self.send_response(302)
                    self.send_header("Location", redirect_to())
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(_PNG)))
                self.end_headers()
                self.wfile.write(_PNG)

            def log_message(self, *args):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        if tls_context is not None:
            self.httpd.socket = tls_context.wrap_socket(
                self.httpd.socket, server_side=True
            )
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    srv = _Server()
    yield srv
    srv.close()


@pytest.fixture
def net(monkeypatch):
    """Controlled network: refuse (and record) every non-loopback connect, and
    answer DNS for test hostnames from a table. `net.dns[host]` is the answer
    the HTTP CLIENT would get if it resolved the hostname itself (the rebind);
    `net.client_lookups` records every such lookup."""
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv(key.lower(), raising=False)

    class Net:
        dns = {}
        client_lookups = []
        connects = []

    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect

    def fake_getaddrinfo(host, port, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else host
        if name in Net.dns:
            Net.client_lookups.append(name)
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
                 (Net.dns[name], port or 0))
            ]
        try:
            ipaddress.ip_address(name)
        except ValueError:
            raise AssertionError(f"unexpected DNS lookup for {name!r}") from None
        return real_getaddrinfo(host, port, *args, **kwargs)

    def guarded_connect(self, address):
        Net.connects.append(address[0])
        if address[0] not in ("127.0.0.1", "::1"):
            raise ConnectionRefusedError("test harness: no external network")
        return real_connect(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    return Net


def _validated(monkeypatch, table, allow_loopback=False):
    """What the SSRF CHECK sees: `table[host]` is the validation DNS answer."""
    monkeypatch.setattr(image_rehost, "_resolve_host", lambda host: list(table[host]))
    if allow_loopback:
        # Stand-in for "this address passed the check": lets the loopback test
        # server play a public host. The real predicate is tested on its own.
        monkeypatch.setattr(image_rehost, "_is_blocked_ip", lambda ip: False)


def _fetch(url):
    return image_rehost.fetch_external_image(
        url, allowed_mimes=_PNG_ONLY, max_bytes=1024 * 1024
    )


# ---------------------------------------------------------------------------
# The audit reproduction
# ---------------------------------------------------------------------------


def test_dns_rebinding_cannot_steer_the_fetch_to_loopback(server, net, monkeypatch):
    """Check sees a public address; a second lookup would answer 127.0.0.1.
    The fetch must go to the CHECKED address and never read the loopback
    server."""
    _validated(monkeypatch, {"audit-rebind.invalid": [_PUBLIC]})
    net.dns["audit-rebind.invalid"] = "127.0.0.1"  # the rebind answer

    with pytest.raises(image_rehost.ImageFetchError) as ei:
        _fetch(f"http://audit-rebind.invalid:{server.port}/synthetic-only.png")

    assert ei.value.status == 502
    assert server.requests == [], "the loopback server must never be reached"
    assert net.client_lookups == [], "the hostname must not be resolved again"
    assert net.connects == [_PUBLIC], "only the validated address is dialled"


def test_redirect_hop_is_bound_to_its_own_validated_address(net, monkeypatch):
    """Hop 1 is a genuine host that redirects to a second hostname. The second
    hop is checked and bound the same way: its rebind answer is never used."""
    srv = _Server(
        redirect_to=lambda: f"http://second.invalid:{srv.port}/secret.png"
    )
    try:
        _validated(
            monkeypatch,
            {"first.invalid": ["127.0.0.1"], "second.invalid": [_PUBLIC]},
            allow_loopback=True,
        )
        net.dns["second.invalid"] = "127.0.0.1"

        with pytest.raises(image_rehost.ImageFetchError):
            _fetch(f"http://first.invalid:{srv.port}/redirect")

        assert [r["path"] for r in srv.requests] == ["/redirect"]
        assert net.client_lookups == []
        assert net.connects == ["127.0.0.1", _PUBLIC]
    finally:
        srv.close()


# ---------------------------------------------------------------------------
# A genuine image still fetches, on the bound connection
# ---------------------------------------------------------------------------


def test_genuine_image_fetches_and_keeps_the_hostname_as_host_header(
    server, net, monkeypatch
):
    _validated(monkeypatch, {"img.pinned.invalid": ["127.0.0.1"]}, allow_loopback=True)

    content, mime, final_url = _fetch(
        f"http://img.pinned.invalid:{server.port}/a/photo.png?v=2"
    )

    assert content == _PNG and mime == "image/png"
    assert final_url == f"http://img.pinned.invalid:{server.port}/a/photo.png?v=2"
    assert server.requests == [
        {"path": "/a/photo.png?v=2", "host": f"img.pinned.invalid:{server.port}"}
    ]
    assert net.client_lookups == []


def test_falls_back_to_the_next_validated_address(server, net, monkeypatch):
    """A multi-address answer keeps working when the first address is down:
    only VALIDATED addresses are tried, in order."""
    _validated(
        monkeypatch, {"multi.invalid": [_PUBLIC, "127.0.0.1"]}, allow_loopback=True
    )

    content, _mime, _url = _fetch(f"http://multi.invalid:{server.port}/p.png")

    assert content == _PNG
    assert net.connects == [_PUBLIC, "127.0.0.1"]
    assert net.client_lookups == []


def test_environment_proxy_is_not_used(server, net, monkeypatch):
    """A proxy would resolve the hostname itself, outside the check. With
    HTTP_PROXY pointing at the test server, a proxied request would arrive
    with an absolute-URI path; the bound fetch arrives with the plain path."""
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{server.port}")
    monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{server.port}")
    _validated(monkeypatch, {"img.pinned.invalid": ["127.0.0.1"]}, allow_loopback=True)

    _fetch(f"http://img.pinned.invalid:{server.port}/direct.png")

    assert [r["path"] for r in server.requests] == ["/direct.png"]


# ---------------------------------------------------------------------------
# HTTPS: the certificate is still checked against the HOSTNAME
# ---------------------------------------------------------------------------


def _self_signed(tmp_path, dns_name):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, dns_name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        # The certificate names the HOSTNAME only - never the IP we dial.
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(dns_name)]), critical=False
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return str(cert_path), str(key_path)


@pytest.fixture
def tls_server(tmp_path, monkeypatch):
    """A loopback HTTPS server whose certificate is valid for
    `secure.pinned.invalid` ONLY, trusted by the fetcher's client."""
    cert_path, key_path = _self_signed(tmp_path, "secure.pinned.invalid")
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(cert_path, key_path)
    srv = _Server(tls_context=server_ctx)

    client_ctx = ssl.create_default_context(cafile=cert_path)
    real_client = httpx.Client

    class TrustingClient(real_client):
        def __init__(self, *args, **kwargs):
            kwargs["verify"] = client_ctx
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", TrustingClient)
    yield srv
    srv.close()


def test_https_fetch_verifies_the_certificate_against_the_hostname(
    tls_server, net, monkeypatch
):
    """Dialling the IP must not weaken TLS: the certificate (valid for the
    hostname, not for 127.0.0.1) verifies, so SNI + the name check use the
    hostname."""
    _validated(
        monkeypatch, {"secure.pinned.invalid": ["127.0.0.1"]}, allow_loopback=True
    )

    content, _mime, _url = _fetch(
        f"https://secure.pinned.invalid:{tls_server.port}/s.png"
    )

    assert content == _PNG
    assert tls_server.requests == [
        {"path": "/s.png", "host": f"secure.pinned.invalid:{tls_server.port}"}
    ]
    assert net.client_lookups == []


def test_https_fetch_refuses_a_certificate_for_another_name(
    tls_server, net, monkeypatch
):
    _validated(
        monkeypatch, {"other.pinned.invalid": ["127.0.0.1"]}, allow_loopback=True
    )

    with pytest.raises(image_rehost.ImageFetchError) as ei:
        _fetch(f"https://other.pinned.invalid:{tls_server.port}/s.png")

    assert ei.value.status == 502
    assert tls_server.requests == []


# ---------------------------------------------------------------------------
# The address predicate: global UNICAST only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ip",
    [
        "224.0.0.1",          # IPv4 multicast (is_global is True for it)
        "239.255.255.250",
        "ff02::1",            # IPv6 multicast
        "ff0e::1",
        "::ffff:127.0.0.1",   # IPv4-mapped loopback
        "::ffff:10.0.0.5",    # IPv4-mapped private
        "64:ff9b::7f00:1",    # NAT64 wrapping 127.0.0.1
        "64:ff9b::a00:5",     # NAT64 wrapping 10.0.0.5
        "2002:7f00:1::",      # 6to4 wrapping 127.0.0.1
    ],
)
def test_multicast_and_wrapped_private_addresses_are_blocked(ip):
    assert image_rehost._is_blocked_ip(ip) is True, ip


@pytest.mark.parametrize(
    "ip", ["8.8.8.8", "93.184.216.34", "2606:4700::1111", "64:ff9b::808:808"]
)
def test_public_unicast_addresses_stay_allowed(ip):
    assert image_rehost._is_blocked_ip(ip) is False, ip


def test_multicast_literal_url_is_refused_without_a_fetch(net):
    for url in ("http://224.0.0.1/a.png", "http://[ff02::1]/a.png"):
        with pytest.raises(image_rehost.ImageFetchError) as ei:
            _fetch(url)
        assert ei.value.status == 400, url
    assert net.connects == []


def test_check_returns_the_addresses_it_validated(monkeypatch):
    _validated(monkeypatch, {"cdn.invalid": ["93.184.216.34", "2606:4700::1111"]})
    assert image_rehost.assert_url_allowed("https://cdn.invalid/a.png") == [
        "93.184.216.34",
        "2606:4700::1111",
    ]
    assert image_rehost.assert_url_allowed("https://93.184.216.34/a.png") == [
        "93.184.216.34"
    ]
