from email.message import Message

import pytest

from latex_review.local_security import local_request


@pytest.mark.parametrize('host', ['localhost:49123', '127.0.0.1:49123', '[::1]:49123'])
def test_loopback_authority_can_use_forwarded_port(host):
    headers = Message()
    headers['Host'] = host
    headers['Origin'] = 'http://' + host
    assert local_request(headers, '127.0.0.1')
    assert not local_request(headers, '192.0.2.1')


@pytest.mark.parametrize('host', ['evil.example', 'localhost.evil.example:80',
    'localhost:99999', 'user@localhost:80', 'localhost:80/path', 'localhost:80#x'])
def test_invalid_authorities_are_rejected(host):
    headers = Message()
    headers['Host'] = host
    assert not local_request(headers, '127.0.0.1')


def test_duplicate_headers_and_cross_site_are_rejected():
    headers = Message()
    headers['Host'] = 'localhost:80'
    headers['Host'] = 'localhost:80'
    assert not local_request(headers, '127.0.0.1')
    del headers['Host']
    headers['Host'] = 'localhost:80'
    headers['Origin'] = 'http://localhost:80'
    headers['Origin'] = 'http://localhost:80'
    assert not local_request(headers, '127.0.0.1')
