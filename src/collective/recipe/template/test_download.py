"""Tests for downloading a template over HTTP.

These drive the recipe against small servers that misbehave on purpose, so
each test reproduces one real failure instead of a nearby one. The shapes
come from a buildout that aborted on a saturated network: a proxy that
answers CONNECT late, connections that are dropped, and responses that
arrive slowly or never finish.
"""

import email.message
import io
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

import zc.buildout

from collective.recipe.template import Recipe
from collective.recipe.template.genshitemplate import Recipe as GenshiRecipe


BODY = b'the template body'
# Long enough that the client always gives up first, short enough that a
# wedged test does not hang the suite.
STALL = 10


class Options(dict):
    """The part of the buildout options API that the recipe uses."""

    def _sub(self, template, seen):
        return template


class Closeable(io.BytesIO):
    """A response body that counts how often it was closed."""

    def __init__(self, *arguments):
        super(Closeable, self).__init__(*arguments)
        self.close_count = 0

    def close(self):
        self.close_count += 1
        super(Closeable, self).close()


class FakeServer:
    """A tiny HTTP server that answers however its handler tells it to.

    The handler gets ``(server, connection, request_number)``. ``requests``
    counts accepted connections, which is how the tests tell a retry from a
    single try. Read it through ``wait_for_requests``: the counting happens
    on another thread, and the client can be done before that thread gets
    around to it.
    """

    def __init__(self, handler):
        self.handler = handler
        self.requests = 0
        self._lock = threading.Lock()
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

    def wait_for_requests(self, expected, timeout=STALL):
        """Return the number of accepted connections.

        Waits until there are ``expected`` of them, so a test does not read
        the counter while the accepting thread is still on its way to it.
        Returns the count it ends with, also when that is fewer or more
        than expected, so the test can report the real number.
        """
        deadline = time.time() + timeout
        while True:
            with self._lock:
                count = self.requests
            if count >= expected or time.time() >= deadline:
                return count
            time.sleep(0.01)

    def _serve(self):
        while not self._done.is_set():
            try:
                connection, _ = self._socket.accept()
            except socket.timeout:
                # Not TimeoutError: only Python 3.10 and later make the two
                # the same, and before that this is a plain OSError.
                continue
            except OSError:
                return
            connection.settimeout(None)
            with self._lock:
                self.requests += 1
                request_number = self.requests
            # A handler that stalls must not block the next accept(), or the
            # retries would queue in the listen backlog and go uncounted.
            thread = threading.Thread(
                target=self._handle,
                args=(connection, request_number),
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


def answer_with_an_empty_body(server, connection, request_number):
    _respond(connection, body=b'')


def answer_in_latin1(server, connection, request_number):
    _respond(connection, body='caf\xe9'.encode('latin-1'))


def drop_the_first(count):
    """Close the connection without answering, for the first ``count``."""
    def handler(server, connection, request_number):
        if request_number <= count:
            connection.close()
            return
        _respond(connection)
    return handler


def truncate_the_first(count):
    """Promise a body, send four bytes of it, then close.

    This is a connection that breaks while the body travels. The client
    sees it as an IncompleteRead, which is not an OSError.
    """
    def handler(server, connection, request_number):
        if request_number <= count:
            connection.sendall(
                b'HTTP/1.1 200 OK\r\nContent-Length: '
                + str(len(BODY)).encode() + b'\r\n\r\n' + BODY[:4])
            connection.close()
            return
        _respond(connection)
    return handler


def answer_with_garbage(server, connection, request_number):
    """Answer with something that is not a status line.

    A proxy that lost the server, or a captive portal, can do this. The
    client sees it as a BadStatusLine, which is not an OSError either.
    """
    connection.sendall(b'not a http answer at all\r\n\r\n')


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


class DownloadTestCase(unittest.TestCase):
    """Makes recipes that download from a FakeServer."""

    recipe_class = Recipe

    def setUp(self):
        import collective.recipe.template as module
        self.module = module
        # Ignore any proxy in the environment, so the tests behave the same
        # everywhere. One test installs a proxy of its own.
        urllib.request.install_opener(
            urllib.request.build_opener(urllib.request.ProxyHandler({})))
        handle, self.output = tempfile.mkstemp()
        os.close(handle)

    def tearDown(self):
        urllib.request.install_opener(None)
        if os.path.exists(self.output):
            os.remove(self.output)

    def makeRecipe(self, url, **extra):
        """Build the recipe for ``url``.

        Underscores in the extra options become dashes, so a test can write
        ``retry_wait='1'`` for the ``retry-wait`` option.
        """
        options = Options(url=url, output=self.output)
        # Keep the tests quick; one test checks the waits on their own.
        options['retry-wait'] = '0'
        options.update(
            (name.replace('_', '-'), value)
            for name, value in extra.items())
        return self.recipe_class({}, 'templatepart', options)

    def assertFails(self, url, **extra):
        """Run the recipe and return the UserError message it raised."""
        with self.assertRaises(zc.buildout.UserError) as caught:
            self.makeRecipe(url, **extra)
        return str(caught.exception)


class OptionTests(DownloadTestCase):

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
            self.assertEqual(
                7.0, self.makeRecipe(server.url, timeout='7').timeout)

    def test_download__Recipe____init____3(self):
        """It falls back to the default when an option is left empty.

        Writing ``timeout =`` is how a part drops an option that it gets
        from a macro, so that must not be an error.
        """
        with FakeServer(answer_normally) as server:
            recipe = self.makeRecipe(server.url, timeout='', attempts='')
            self.assertEqual(30.0, recipe.timeout)
            self.assertEqual(3, recipe.attempts)

    def test_download__Recipe____init____4(self):
        """It names the part and the value when an option is not a number.

        Buildout prints a UserError as a message, where a ValueError would
        reach the user as a traceback without any part name in it.
        """
        message = self.assertFails(
            'http://download.example/x.in', timeout='soon')
        self.assertIn("'templatepart'", message)
        self.assertIn("'timeout'", message)
        self.assertIn("'soon'", message)

    def test_download__Recipe____init____5(self):
        """It refuses a number of attempts below one."""
        message = self.assertFails(
            'http://download.example/x.in', attempts='0')
        self.assertIn("'attempts'", message)
        self.assertIn('1 or more', message)


class DownloadTests(DownloadTestCase):

    # -- the happy path ---------------------------------------------------

    def test_download__Recipe___download__1(self):
        """It asks a working server once and returns the template."""
        with FakeServer(answer_normally) as server:
            recipe = self.makeRecipe(server.url)
            self.assertEqual(BODY.decode(), recipe.source)
            self.assertEqual(1, server.wait_for_requests(1))

    def test_download__Recipe___download__2(self):
        """It accepts an empty template.

        An empty answer is a template, not a failure, so the recipe must
        not report it as one.
        """
        with FakeServer(answer_with_an_empty_body) as server:
            self.assertEqual('', self.makeRecipe(server.url).source)

    def test_download__Recipe___download__3(self):
        """It reads the template in the encoding the part asks for."""
        with FakeServer(answer_in_latin1) as server:
            recipe = self.makeRecipe(server.url, input_encoding='latin-1')
            self.assertEqual('caf\xe9', recipe.source)

    # -- failures that are worth another try ------------------------------

    def test_download__Recipe___download__4(self):
        """It tries again when the connection is dropped, and succeeds."""
        with FakeServer(drop_the_first(2)) as server:
            recipe = self.makeRecipe(server.url)
            self.assertEqual(BODY.decode(), recipe.source)
            self.assertEqual(3, server.wait_for_requests(3))

    def test_download__Recipe___download__5(self):
        """It tries again when the body stops halfway, and succeeds.

        The client reports this as an IncompleteRead, which is an
        HTTPException and not an OSError. Catching OSError alone let it
        through as a traceback.
        """
        with FakeServer(truncate_the_first(2)) as server:
            recipe = self.makeRecipe(server.url)
            self.assertEqual(BODY.decode(), recipe.source)
            self.assertEqual(3, server.wait_for_requests(3))

    def test_download__Recipe___download__6(self):
        """It reports a body that never arrives in full."""
        with FakeServer(truncate_the_first(99)) as server:
            message = self.assertFails(server.url)
            self.assertEqual(3, server.wait_for_requests(3))
            self.assertIn(server.url, message)
            self.assertIn('3 attempts', message)
            self.assertIn('IncompleteRead', message)

    def test_download__Recipe___download__7(self):
        """It reports an answer that is not HTTP at all.

        A BadStatusLine is an HTTPException as well, so this used to be a
        traceback too.
        """
        with FakeServer(answer_with_garbage) as server:
            message = self.assertFails(server.url)
            self.assertEqual(3, server.wait_for_requests(3))
            self.assertIn(server.url, message)

    def test_download__Recipe___download__8(self):
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
            self.assertEqual(3, proxy.wait_for_requests(3))
            self.assertIn(url, message)
            self.assertIn('3 attempts', message)

    def test_download__Recipe___download__9(self):
        """It reports a timeout that happens before the response headers.

        This one used to escape the recipe as a traceback: it arrives as a
        plain TimeoutError, which is not a URLError.
        """
        with FakeServer(stall_before_the_headers) as server:
            message = self.assertFails(server.url, timeout='0.3')
            self.assertEqual(3, server.wait_for_requests(3))
            self.assertIn(server.url, message)
            self.assertIn('timed out', message)

    def test_download__Recipe___download__10(self):
        """It reports a timeout that happens while reading the body.

        The read used to sit outside the try, so this escaped as well.
        """
        with FakeServer(stall_during_the_body) as server:
            message = self.assertFails(server.url, timeout='0.3')
            self.assertEqual(3, server.wait_for_requests(3))
            self.assertIn(server.url, message)

    def test_download__Recipe___download__11(self):
        """It tries again after an HTTP 503."""
        with FakeServer(answer_with(b'503 Service Unavailable')) as server:
            message = self.assertFails(server.url)
            self.assertEqual(3, server.wait_for_requests(3))
            self.assertIn('503', message)

    def test_download__Recipe___download__12(self):
        """It tries as often as the "attempts" option says."""
        with FakeServer(answer_with(b'503 Service Unavailable')) as server:
            message = self.assertFails(server.url, attempts='2')
            self.assertEqual(2, server.wait_for_requests(2))
            self.assertIn('2 attempts', message)

    def test_download__Recipe___download__13(self):
        """It waits longer before every next attempt."""
        waits = []
        with mock.patch.object(self.module.time, 'sleep', waits.append):
            with FakeServer(answer_with(b'503 Service Unavailable')) as serv:
                self.assertFails(serv.url, retry_wait='1')
        self.assertEqual([1.0, 2.0], waits)

    def test_download__Recipe___download__14(self):
        """It says in the log that it will try again.

        Without this the buildout is silent for as long as the attempts
        take, and that silence is what makes people stop the run.
        """
        with mock.patch.object(self.module.time, 'sleep'):
            with FakeServer(drop_the_first(1)) as server:
                with self.assertLogs('templatepart', 'WARNING') as logged:
                    self.makeRecipe(server.url, retry_wait='1')
        self.assertIn('attempt 1 of 3', logged.output[0])
        self.assertIn(server.url, logged.output[0])
        self.assertIn('1.0 seconds', logged.output[0])

    def test_download__Recipe___download__15(self):
        """It closes the response that comes with an HTTP error.

        urlopen raises HTTPError with the response still open, and the
        "with" never runs, so nothing else closes it. Three retried 503s
        would leak three sockets per part.
        """
        bodies = [Closeable(b''), Closeable(b'')]
        answers = iter(bodies)

        def fail(url, timeout=None):
            raise urllib.error.HTTPError(
                url, 503, 'Service Unavailable', email.message.Message(),
                next(answers))

        with mock.patch('urllib.request.urlopen', fail):
            self.assertFails('http://download.example/x.in', attempts='2')
        self.assertEqual([1, 1], [body.close_count for body in bodies])

    # -- failures that must not be retried --------------------------------

    def test_download__Recipe___download__16(self):
        """It gives up at once on an HTTP 404 and names the URL."""
        with FakeServer(answer_with(b'404 Not Found')) as server:
            message = self.assertFails(server.url)
            self.assertEqual(1, server.wait_for_requests(1))
            self.assertIn(server.url, message)
            self.assertIn('404', message)
            self.assertIn('1 attempt', message)

    def test_download__Recipe___download__17(self):
        """It gives up at once on an HTTP 403.

        An egress proxy answers 403 for a host it may not reach. Trying
        again only makes the buildout slower to tell us.
        """
        with FakeServer(answer_with(b'403 Forbidden')) as server:
            message = self.assertFails(server.url)
            self.assertEqual(1, server.wait_for_requests(1))
            self.assertIn('403', message)

    def test_download__Recipe___download__18(self):
        """It names the URL when the scheme cannot be used.

        Only a download over the network can fail by accident, so this one
        is not tried again.
        """
        message = self.assertFails('nonsense://download.example/x.in')
        self.assertIn('nonsense://download.example/x.in', message)
        self.assertIn('unknown url type', message)
        self.assertIn('1 attempt', message)

    def test_download__Recipe___download__19(self):
        """It gives up at once on a file that is not there.

        A local file answers the same way every time, and the doctests use
        a file URL, so retrying would only slow them down.
        """
        missing = os.path.join(tempfile.gettempdir(), 'no-such-template.in')
        message = self.assertFails('file://%s' % missing)
        self.assertIn('1 attempt', message)

    def test_download__Recipe___download__20(self):
        """It gives up at once on an empty URL."""
        self.assertIn('1 attempt', self.assertFails(''))

    # -- the message itself -----------------------------------------------

    def test_download__Recipe___download__21(self):
        """It names the buildout part that wanted the template."""
        with FakeServer(answer_with(b'404 Not Found')) as server:
            self.assertIn("part 'templatepart'", self.assertFails(server.url))

    def test_download__Recipe___download__22(self):
        """It reports a template it cannot decode as an encoding problem.

        The download worked, so the part needs another "input-encoding",
        not another attempt.
        """
        with FakeServer(answer_in_latin1) as server:
            message = self.assertFails(server.url)
            self.assertEqual(1, server.wait_for_requests(1))
            self.assertIn("part 'templatepart'", message)
            self.assertIn('utf-8', message)


class GenshiTests(DownloadTestCase):
    """The genshi recipe downloads through the same code."""

    recipe_class = GenshiRecipe

    def test_download__genshitemplate_Recipe___execute__1(self):
        """It names the URL when a template from a URL has an error.

        A template that came from a URL has no "input", so the message used
        to say "Error in template None".
        """
        def answer_with_an_undefined_name(server, connection, number):
            _respond(connection, body=b'${options.nothere}')

        with FakeServer(answer_with_an_undefined_name) as server:
            message = self.assertFails(server.url)
            self.assertIn(server.url, message)
            self.assertNotIn('None', message)


def test_suite():
    loader = unittest.defaultTestLoader
    return unittest.TestSuite([
        loader.loadTestsFromTestCase(OptionTests),
        loader.loadTestsFromTestCase(DownloadTests),
        loader.loadTestsFromTestCase(GenshiTests),
    ])
