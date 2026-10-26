"""Bounded public HTTP downloads with DNS pinning, TLS verification and per-hop checks."""
from dataclasses import dataclass
import ipaddress
import socket
import ssl
import time
from urllib.parse import urlsplit, urljoin
import urllib3
from .util import normalize_url

class FetchError(Exception):
    def __init__(self, kind, status=0, retry_after=None):
        super().__init__(kind)
        self.kind, self.status, self.retry_after = kind, status, retry_after

@dataclass
class Response:
    url: str
    status: int
    headers: dict
    body: bytes

def public_addresses(host, port):
    addresses = list(dict.fromkeys(x[4][0] for x in socket.getaddrinfo(
        host, port, type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(x).is_global for x in addresses):
        raise FetchError("blocked_address")
    return addresses

class PublicHTTP:
    def __init__(self, max_bytes=8_000_000, timeout=25, gap=1.0):
        self.max_bytes, self.timeout, self.gap = max_bytes, timeout, gap
        self.last = {}

    def get(self, url, headers=None):
        url = normalize_url(url)
        started = time.monotonic()
        original = urlsplit(url).netloc
        for _ in range(6):
            parts = urlsplit(url)
            port = parts.port or (443 if parts.scheme == "https" else 80)
            if port not in (80, 443):
                raise FetchError("blocked_port")
            ips = public_addresses(parts.hostname, port)
            if time.monotonic() - started > 90:
                raise FetchError("request_deadline")
            wait = self.gap - (time.monotonic() - self.last.get(parts.hostname, 0))
            if wait > 0:
                time.sleep(wait)
            request_headers = {"Host": parts.netloc, "User-Agent": "NyaaNews/0.1 RSS archive",
                               "Accept-Encoding": "identity"}
            if parts.netloc == original:
                request_headers.update(headers or {})
            request_path = parts.path or "/"
            if parts.query:
                request_path += "?" + parts.query
            options = dict(port=port, timeout=urllib3.Timeout(connect=10, read=self.timeout),
                           retries=False)
            if parts.scheme == "https":
                pool = urllib3.HTTPSConnectionPool(ips[0], server_hostname=parts.hostname,
                    assert_hostname=parts.hostname, cert_reqs="CERT_REQUIRED",
                    ssl_context=ssl.create_default_context(), **options)
            else:
                pool = urllib3.HTTPConnectionPool(ips[0], **options)
            response = None
            try:
                response = pool.urlopen("GET", request_path, headers=request_headers,
                    redirect=False, assert_same_host=False, preload_content=False)
                rh = {k.lower(): v for k, v in response.headers.items()}
                if response.status in (301, 302, 303, 307, 308):
                    location = rh.get("location")
                    if not location:
                        raise FetchError("redirect_without_location")
                    target = normalize_url(urljoin(url, location))
                    if parts.scheme == "https" and urlsplit(target).scheme != "https":
                        raise FetchError("tls_downgrade")
                    url = target
                    continue
                if response.status == 304:
                    return Response(url, 304, rh, b"")
                if response.status != 200:
                    raise FetchError("http_error", response.status, rh.get("retry-after"))
                total = 0
                blocks = []
                for block in response.stream(65536, decode_content=True):
                    total += len(block)
                    if total > self.max_bytes:
                        raise FetchError("response_too_large")
                    if time.monotonic() - started > 90:
                        raise FetchError("request_deadline")
                    blocks.append(block)
                return Response(url, response.status, rh, b"".join(blocks))
            except FetchError:
                raise
            except Exception as exc:
                # Do not serialize URLs, query strings or tokens from HTTP errors.
                raise FetchError("network_error:" + type(exc).__name__) from None
            finally:
                if response:
                    response.close()
                pool.close()
                self.last[parts.hostname] = time.monotonic()
        raise FetchError("too_many_redirects")
