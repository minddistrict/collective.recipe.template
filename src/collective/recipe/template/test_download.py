"""Tests for downloading a template over HTTP.

These drive the recipe against small servers that misbehave on purpose, so
each test reproduces one real failure instead of a nearby one. The shapes
come from a buildout that aborted on a saturated network: a proxy that
answers CONNECT late, connections that are dropped, and responses that
arrive slowly.
"""

import os
import socket
import tempfile
import threading
import unittest
import urllib.request
from unittest import mock

import zc.buildout

from collective.recipe.template import Recipe


BODY = b'the template body'
# Long enough that the client always gives up first, short enough that a
# wedged test does not hang the suite.
STALL = 10


class Options(dict):
    """The part of the buildout options API that the recipe uses."""

    def _sub(self, template, seen):
        return template


class FakeServer:
    """A tiny HTTP server that answers however its handler tells it to.

    The handler gets ``(server, connection, request_number)``. ``requests``
    counts accepted connections, which is how the tests tell a retry from a
    single try.
    """

    def __init__(self, handler):
        self.handler = handler
        self.requests = 0
        self._done = threading.Event()
        self._handlers = []
        self._socket = socket.socket()
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # 127.0.0.1, never "localhost": the name can resolve to ::1 first.
        self._socket.bind(('127.0.0.1', 0))
        self._socket.listen(8)
        # Closing a socket does not reliably wake a blocked accept(), so
        # poll instead and check whether we are shutting down.
        self._socket.settimeout(0.1)
        self.port = self._socket.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exception):
        # Wake the stalling handlers before joining, so leaving the block
        # does not wait out their full delay.
        self._done.set()
        try:
            self._socket.close()
        except OSError:
            pass
        self._thread.join(STALL)
        for thread in self._handlers:
            thread.join(STALL)

    @property
    def url(self):
        return 'http://127.0.0.1:%d/template.in' % self.port

    def stall(self, seconds=STALL):
        """Wait, but give up as soon as the server is shutting down."""
        self._done.wait(seconds)

    def _serve(self):
        while not self._done.is_set():
            try:
                connection, _ = self._socket.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            connection.settimeout(None)
            self.requests += 1
            # A handler that stalls must not block the next accept(), or the
            # retries would queue in the listen backlog and go uncounted.
            thread = threading.Thread(
                target=self._handle,
                args=(connection, self.requests),
                daemon=True,
            )
            self._handlers.append(thread)
            thread.start()

    def _handle(self, connection, request_number):
        try:
            connection.settimeout(STALL)
            try:
                connection.recv(4096)
            except OSError:
                pass
            self.handler(self, connection, request_number)
        except OSError:
            pass
        finally:
            try:
                connection.close()
            except OSError:
                pass


def _respond(connection, body=BODY, status=b'200 OK'):
    connection.sendall(
        b'HTTP/1.1 ' + status
        + b'\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n'
        + body)


def answer_normally(server, connection, request_number):
    _respond(connection)


def drop_the_first(count):
    """Close the connection without answering, for the first ``count``."""
    def handler(server, connection, request_number):
        if request_number <= count:
            connection.close()
            return
        _respond(connection)
    return handler


def stall_before_the_headers(server, connection, request_number):
    server.stall()
    _respond(connection)


def stall_during_the_body(server, connection, request_number):
    connection.sendall(
        b'HTTP/1.1 200 OK\r\nContent-Length: '
        + str(len(BODY)).encode() + b'\r\n\r\n' + BODY[:4])
    server.stall()
    connection.sendall(BODY[4:])


def answer_with(status):
    def handler(server, connection, request_number):
        connection.sendall(
            b'HTTP/1.1 ' + status + b'\r\nContent-Length: 0\r\n\r\n')
    return handler


def stall_before_answering_connect(server, connection, request_number):
    """A proxy that takes its time to say "Connection established".

    This is the shape that aborted the real buildout: squid had accepted the
    client but had not reached the server yet.
    """
    server.stall()
    try:
        connection.sendall(b'HTTP/1.1 200 Connection established\r\n\r\n')
    except OSError:
        pass  # the client gave up first, which is the point


class DownloadTests(unittest.TestCase):

    def setUp(self):
        import collective.recipe.template as module
        self.module = module
        self._attempts = module.DOWNLOAD_ATTEMPTS
        self._backoff = module.DOWNLOAD_BACKOFF
        # Keep the tests quick; one test checks the waits on their own.
        module.DOWNLOAD_BACKOFF = 0.0
        # Ignore any proxy in the environment, so the tests behave the same
        # everywhere. One test installs a proxy of its own.
        urllib.request.install_opener(
            urllib.request.build_opener(urllib.request.ProxyHandler({})))
        handle, self.output = tempfile.mkstemp()
        os.close(handle)

    def tearDown(self):
        self.module.DOWNLOAD_ATTEMPTS = self._attempts
        self.module.DOWNLOAD_BACKOFF = self._backoff
        urllib.request.install_opener(None)
        if os.path.exists(self.output):
            os.remove(self.output)

    def makeRecipe(self, url, **extra):
        options = Options(url=url, output=self.output, **extra)
        return Recipe({}, 'templatepart', options)

    def assertFails(self, url, **extra):
        """Run the recipe and return the UserError message it raised."""
        with self.assertRaises(zc.buildout.UserError) as caught:
            self.makeRecipe(url, **extra)
        return str(caught.exception)

    # -- the timeout option -----------------------------------------------

    def test_download__Recipe____init____1(self):
        """It waits 30 seconds for a download by default.

        The old default of 1 second was the real trouble: on a busy link a
        connection often takes more than a second and still succeeds.
        """
        with FakeServer(answer_normally) as server:
            self.assertEqual(30.0, self.makeRecipe(server.url).timeout)

    def test_download__Recipe____init____2(self):
        """It takes the number of seconds from the "timeout" option."""
        with FakeServer(answer_normally) as server:
            self.assertEqual(7.0, self.makeRecipe(server.url, timeout='7')
                             .timeout)

    # -- the happy path ---------------------------------------------------

    def test_download__Recipe___download__1(self):
        """It asks a working server once and returns the template."""
        with FakeServer(answer_normally) as server:
            recipe = self.makeRecipe(server.url)
            self.assertEqual(BODY.decode(), recipe.source)
            self.assertEqual(1, server.requests)

    # -- failures that are worth another try ------------------------------

    def test_download__Recipe___download__2(self):
        """It tries again when the connection is dropped, and succeeds."""
        with FakeServer(drop_the_first(2)) as server:
            recipe = self.makeRecipe(server.url)
            self.assertEqual(BODY.decode(), recipe.source)
            self.assertEqual(3, server.requests)

    def test_download__Recipe___download__3(self):
        """It retries a proxy that answers CONNECT too late.

        This is the shape that aborted the real buildout. The error names
        the URL and how often it tried.
        """
        with FakeServer(stall_before_answering_connect) as proxy:
            urllib.request.install_opener(urllib.request.build_opener(
                urllib.request.ProxyHandler(
                    {'https': 'http://127.0.0.1:%d' % proxy.port})))
            url = 'https://download.example/kgs/1.0/templates/pytest.ini.in'
            message = self.assertFails(url, timeout='0.3')
            self.assertEqual(3, proxy.requests)
            self.assertIn(url, message)
            self.assertIn('3 attempts', message)

    def test_download__Recipe___download__4(self):
        """It reports a timeout that happens before the response headers.

        This one used to escape the recipe as a traceback: it arrives as a
        plain TimeoutError, which is not a URLError.
        """
        with FakeServer(stall_before_the_headers) as server:
            message = self.assertFails(server.url, timeout='0.3')
            self.assertEqual(3, server.requests)
            self.assertIn(server.url, message)
            self.assertIn('timed out', message)

    def test_download__Recipe___download__5(self):
        """It reports a timeout that happens while reading the body.

        The read used to sit outside the try, so this escaped as well.
        """
        with FakeServer(stall_during_the_body) as server:
            message = self.assertFails(server.url, timeout='0.3')
            self.assertEqual(3, server.requests)
            self.assertIn(server.url, message)

    def test_download__Recipe___download__6(self):
        """It tries again after an HTTP 503."""
        with FakeServer(answer_with(b'503 Service Unavailable')) as server:
            message = self.assertFails(server.url)
            self.assertEqual(3, server.requests)
            self.assertIn('503', message)

    def test_download__Recipe___download__7(self):
        """It waits longer before every next attempt."""
        self.module.DOWNLOAD_BACKOFF = 1.0
        waits = []
        with mock.patch.object(self.module.time, 'sleep', waits.append):
            with FakeServer(answer_with(b'503 Service Unavailable')) as server:
                self.assertFails(server.url)
        self.assertEqual([1.0, 2.0], waits)

    # -- failures that must not be retried --------------------------------

    def test_download__Recipe___download__8(self):
        """It gives up at once on an HTTP 404 and names the URL."""
        with FakeServer(answer_with(b'404 Not Found')) as server:
            message = self.assertFails(server.url)
            self.assertEqual(1, server.requests)
            self.assertIn(server.url, message)
            self.assertIn('404', message)
            self.assertIn('1 attempt', message)

    def test_download__Recipe___download__9(self):
        """It gives up at once on an HTTP 403.

        An egress proxy answers 403 for a host it may not reach. Trying
        again only makes the buildout slower to tell us.
        """
        with FakeServer(answer_with(b'403 Forbidden')) as server:
            message = self.assertFails(server.url)
            self.assertEqual(1, server.requests)
            self.assertIn('403', message)

    def test_download__Recipe___download__10(self):
        """It names the URL when the scheme cannot be used."""
        message = self.assertFails('nonsense://download.example/x.in')
        self.assertIn('nonsense://download.example/x.in', message)
        self.assertIn('unknown url type', message)

    def test_download__Recipe___download__11(self):
        """It gives up at once on an empty URL."""
        self.assertIn('1 attempt', self.assertFails(''))

    # -- the message itself -----------------------------------------------

    def test_download__Recipe___download__12(self):
        """It names the buildout part that wanted the template."""
        with FakeServer(answer_with(b'404 Not Found')) as server:
            self.assertIn("part 'templatepart'", self.assertFails(server.url))


def test_suite():
    return unittest.defaultTestLoader.loadTestsFromTestCase(DownloadTests)
