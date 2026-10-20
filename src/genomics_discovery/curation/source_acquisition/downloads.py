"""Anonymous public HTTP retrieval; redirects receive the same URL checks."""

from dataclasses import dataclass
import ipaddress
import socket
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from ..document_manifest import MAX_BYTES


def public_url(url: str | None, *, resolve=False) -> str:
    if not isinstance(url, str) or not url or any(c.isspace() for c in url):
        raise ValueError("Missing or invalid public document URL")
    parts = urlsplit(url)
    if (parts.scheme not in {"https", "http"} or not parts.hostname
            or parts.username is not None or parts.password is not None
            or parts.port not in {None, 80, 443}):
        raise ValueError("Document URL must be anonymous public HTTP(S) on a standard port")
    host = parts.hostname
    if host.lower() == "localhost" or "." not in host and ":" not in host:
        raise ValueError("Document URL must name a public host")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("Private/local document addresses are not public sources")
    if resolve:
        addresses = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80),
                                       type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
            raise ValueError("Document host resolves to a nonpublic address")
    return url


@dataclass(frozen=True)
class Download:
    content: bytes
    final_url: str
    content_type: str


class PublicRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl, resolve=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str) -> Download:
    """Fetch bytes without environment proxies, cookies or account credentials.

    The socket timeout bounds a stalled request, not the native research session.
    Authentication, rate limits and access challenges remain unavailable sources.
    """
    public_url(url, resolve=True)
    opener = build_opener(ProxyHandler({}), PublicRedirect())
    request = Request(url, headers={"User-Agent": "sequencing-technology-atlas/source-acquisition",
                                    "Accept-Encoding": "identity"})
    with opener.open(request, timeout=60) as response:
        final_url = public_url(response.geturl(), resolve=True)
        content = response.read(MAX_BYTES + 1)
        if not content or len(content) > MAX_BYTES:
            raise ValueError("Empty document or document exceeds existing source size limit")
        return Download(content, final_url, response.headers.get_content_type())
