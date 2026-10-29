"""Validate browser-visible loopback authorities, independently of bind ports."""
from ipaddress import ip_address
from copy import deepcopy
import re
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


def history_image_request(headers, peer, path):
    """Opaque snapshot documents send cross-site image loads without a referrer.

    Permit only no-CORS raster subresources under an unguessable snapshot ID.
    The handler must still check the snapshot's exact resource allowlist. This
    grants no access to HTML, APIs, credentials, or cross-origin pixel reads.
    Loopback peer and Host validation remain identical to the control API.
    """
    if (headers.get_all('Sec-Fetch-Dest', []) != ['image']
            or headers.get_all('Sec-Fetch-Mode', []) != ['no-cors']
            or headers.get_all('Sec-Fetch-Site', []) != ['cross-site']
            or headers.get_all('Origin', []) not in ([], ['null'])
            or not re.fullmatch(r'/history/[0-9a-f]{32}/(?:assets|previews|pages)/[^?#]+\.(?:png|jpe?g|gif|webp)', path, re.I)):
        return False
    copy = deepcopy(headers)
    del copy['Sec-Fetch-Site']
    del copy['Origin']
    return local_request(copy, peer)
