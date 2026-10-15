"""Validate browser-visible loopback authorities, independently of bind ports."""
from ipaddress import ip_address
from urllib.parse import urlsplit


def local_request(headers, peer):
    try:
        if not ip_address(peer).is_loopback:
            return False
        hosts = headers.get_all('Host', [])
        if len(hosts) != 1:
            return False
        host = hosts[0]
        authority = urlsplit('http://' + host)
        if (authority.hostname not in ('localhost', '127.0.0.1', '::1')
                or authority.username is not None or authority.password is not None
                or authority.path or authority.query or authority.fragment
                or authority.port is not None and not 1 <= authority.port <= 65535):
            return False
        origins = headers.get_all('Origin', [])
        if len(origins) > 1 or origins and origins[0] != 'http://' + host:
            return False
        return headers.get('Sec-Fetch-Site') not in ('cross-site', 'same-site')
    except ValueError:
        return False
