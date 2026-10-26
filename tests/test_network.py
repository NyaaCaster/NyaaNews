import socket
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from curl_cffi import CurlOpt
from nyaanews.network import PublicHTTP, FetchError

class BrowserHTTPTests(unittest.TestCase):
    def mock_session(self, status=200, headers=None, body=b"original article"):
        session=patch('curl_cffi.requests.Session')
        constructor=session.start()
        self.addCleanup(session.stop)
        client=constructor.return_value.__enter__.return_value
        def get(*args,**kwargs):
            kwargs['content_callback'](body)
            return SimpleNamespace(status_code=status,headers=headers or {})
        client.get.side_effect=get
        return constructor,client

    def test_pins_public_ip_and_preserves_certificate_validation(self):
        constructor,client=self.mock_session()
        with patch('nyaanews.network.public_addresses',return_value=['93.184.215.14']):
            result=PublicHTTP().get('https://example.invalid/article',profile='chrome')
        self.assertEqual(result.body,b'original article')
        options=constructor.call_args.kwargs
        self.assertEqual(options['curl_options'][CurlOpt.RESOLVE],['example.invalid:443:93.184.215.14'])
        self.assertEqual(options['curl_options'][CurlOpt.PROXY],'')
        self.assertFalse(options['trust_env'])
        request=client.get.call_args.kwargs
        self.assertIs(request['verify'],True)
        self.assertIs(request['allow_redirects'],False)

    def test_redirect_to_private_target_is_rejected(self):
        _,client=self.mock_session(302,{'location':'https://127.0.0.1/private'})
        def resolve(host,port,**kwargs):
            ip='127.0.0.1' if host=='127.0.0.1' else '93.184.215.14'
            return [(socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,port))]
        with patch('socket.getaddrinfo',side_effect=resolve):
            with self.assertRaisesRegex(FetchError,'blocked_address'):
                PublicHTTP(gap=0).get('https://example.invalid/a',profile='chrome')
        self.assertEqual(client.get.call_count,1)

    def test_oversized_body_is_aborted_during_callback(self):
        self.mock_session(body=b'123456')
        with patch('nyaanews.network.public_addresses',return_value=['93.184.215.14']):
            with self.assertRaisesRegex(FetchError,'response_too_large'):
                PublicHTTP(max_bytes=5).get('https://example.invalid/a',profile='chrome')

    def test_browser_challenge_has_specific_reason_and_status(self):
        self.mock_session(403,{'cf-mitigated':'challenge'})
        with patch('nyaanews.network.public_addresses',return_value=['93.184.215.14']):
            with self.assertRaises(FetchError) as raised:
                PublicHTTP().get('https://example.invalid/a',profile='chrome')
        self.assertEqual(raised.exception.kind,'browser_challenge')
        self.assertEqual(raised.exception.status,403)

    def test_https_downgrade_is_rejected(self):
        self.mock_session(302,{'location':'http://example.invalid/a'})
        with patch('nyaanews.network.public_addresses',return_value=['93.184.215.14']):
            with self.assertRaisesRegex(FetchError,'tls_downgrade'):
                PublicHTTP().get('https://example.invalid/a',profile='chrome')
