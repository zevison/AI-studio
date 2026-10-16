import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from core.hypit_provider_bridge import SafeMp4Collector, MediaFailure, _PinnedHTTPSConnection
from tests.test_hypit_provider_bridge import Opener, mp4_bytes


class DownloadSecurityTests(unittest.TestCase):
    def test_connection_pins_verified_ip_and_preserves_certificate_hostname(self):
        sock = Mock()
        context = Mock()
        with patch('socket.socket', return_value=sock) as factory, patch('socket.getaddrinfo') as resolve:
            connection = _PinnedHTTPSConnection('cdn.example.com', pinned_host='cdn.example.com',
                addresses=('8.8.8.8',), timeout=3, context=context)
            connection.connect()
            factory.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect.assert_called_once_with(('8.8.8.8', 443))
            context.wrap_socket.assert_called_once_with(sock, server_hostname='cdn.example.com')
            resolve.assert_not_called()

    def test_destination_change_rejected_before_connect(self):
        with self.assertRaises(MediaFailure):
            _PinnedHTTPSConnection('other.example.com', pinned_host='cdn.example.com', addresses=('8.8.8.8',))

    def test_false_type_or_truncated_length_never_published(self):
        for headers in ({'Content-Type':'text/html'}, {},
                        {'Content-Type':'video/mp4','Content-Length':str(len(mp4_bytes())+1)}):
            with self.subTest(headers=headers), tempfile.TemporaryDirectory() as tmp:
                collector = SafeMp4Collector({'cdn.example.com'}, opener=Opener(mp4_bytes(),headers=headers),
                    resolver=lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))],
                    video_probe=lambda p: True)
                target = Path(tmp)/'video.mp4'
                with self.assertRaises(MediaFailure):
                    collector.collect('https://cdn.example.com/video.mp4',target)
                self.assertFalse(target.exists())
                self.assertEqual(list(Path(tmp).iterdir()),[])

    def test_mixed_public_private_dns_rejected(self):
        collector = SafeMp4Collector({'cdn.example.com'}, resolver=lambda *a,**k:[
            (socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443)),
            (socket.AF_INET,socket.SOCK_STREAM,6,'',('169.254.169.254',443))])
        with self.assertRaises(MediaFailure): collector._verify_host('https://cdn.example.com/video.mp4')
