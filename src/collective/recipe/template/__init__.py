import http.client
import logging
import os
import re
import stat
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
import zc.buildout


TRUE_VALUES = ('y', 'yes', '1', 'true')

# How often a template download is tried before the part gives up, and the
# first wait in between. The wait doubles after every failed attempt. A part
# can set both itself, with the "attempts" and "retry-wait" options.
DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_BACKOFF = 1.0
# Seconds to wait for a download when the part sets no "timeout" option.
# Generous on purpose: on a busy link a connection can take more than ten
# seconds and still succeed, and aborting the buildout costs far more than
# waiting does. The timeout counts per attempt, not for all attempts
# together.
DOWNLOAD_TIMEOUT = 30.0
# HTTP statuses that deserve another attempt. Every other 4xx says the
# request itself is wrong, so repeating it cannot help.
RETRY_STATUSES = frozenset((408, 429, 500, 502, 503, 504))
# Schemes whose download can fail by accident. A file that is not there, or
# a scheme urllib does not know, gives the same answer every time, so trying
# again would only make the buildout slower to report it.
RETRY_SCHEMES = frozenset(('http', 'https', 'ftp'))


class Recipe:
    def __init__(self, buildout, name, options):
        self.buildout = buildout
        self.name = name
        self.options = options
        self.logger = logging.getLogger(self.name)

        if ("input" not in options
                and "inline" not in options
                and "url" not in options):
            self.logger.error(
                "No input file, inline template, or URL specified.")
            raise zc.buildout.UserError(
                "No input file, inline template, or URL specified.")

        if "output" not in options:
            self.logger.error("No output file specified.")
            raise zc.buildout.UserError("No output file specified.")

        if "input" in options:
            if options.get('inline') or options.get('url'):
                self.logger.error("Too many input sources.")
                raise zc.buildout.UserError("Too many input sources.")

        self.output = options["output"]
        self.input = options.get("input")
        self.input_encoding = options.get("input-encoding", "utf-8")
        self.output_encoding = options.get("output-encoding", "utf-8")
        self.inline = options.get("inline")
        self.url = options.get("url")
        self.timeout = self._number_option(
            "timeout", DOWNLOAD_TIMEOUT, minimum=0)
        self.attempts = int(self._number_option(
            "attempts", DOWNLOAD_ATTEMPTS, minimum=1))
        self.retry_wait = self._number_option(
            "retry-wait", DOWNLOAD_BACKOFF, minimum=0)
        self.overwrite = options.get(
            "overwrite", 'true').lower() in TRUE_VALUES
        if "inline" in options:
            self.source = self.inline.lstrip()
            self.mode = None
        elif "input" in options and os.path.exists(self.input):
            with open(self.input, 'rb') as f:
                self.source = f.read().decode(self.input_encoding)
            self.mode = stat.S_IMODE(os.stat(self.input).st_mode)
        elif "input" in options and self.input.startswith('inline:'):
            self.source = self.input[len('inline:'):].lstrip()
            self.mode = None
        elif "url" in options:
            self.source = self._download()
            self.mode = None
        else:
            msg = "Input file '%s' does not exist." % self.input
            self.logger.error(msg)
            raise zc.buildout.UserError(msg)

        self._execute()

        if "mode" in options:
            self.mode = int(options["mode"], 8)

    def _execute(self):
        template = self.source
        if sys.version_info < (3,):
            template = template.encode('utf-8')
        template = re.sub(r"\$\{([^:]+?)\}", r"${%s:\1}" % self.name, template)
        self.result = self.options._sub(template, [])
        if sys.version_info < (3,):
            self.result = self.result.decode('utf-8')

    def _number_option(self, option, default, minimum):
        """Return a numeric option, or ``default`` when the part sets none.

        An empty value counts as "sets none": writing ``timeout =`` is how a
        part drops an option that it gets from a macro.
        """
        value = self.options.get(option, "")
        if isinstance(value, str):
            value = value.strip()
        if value == "" or value is None:
            return default
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = None
        if number is None or number < minimum:
            msg = ("Part '%s' has an invalid '%s' option: %r. Use a number "
                   "of %s or more." % (self.name, option, value, minimum))
            self.logger.error(msg)
            raise zc.buildout.UserError(msg)
        return number

    def _may_retry(self):
        """Can the download of this URL fail by accident?"""
        try:
            scheme = urllib.parse.urlsplit(self.url).scheme
        except (AttributeError, ValueError):
            return False
        return scheme.lower() in RETRY_SCHEMES

    def _download(self):
        """Return the template text at ``self.url``.

        Raises ``zc.buildout.UserError`` when the download failed. The
        message names the part, the URL and the number of attempts.

        The request and the reading of the body sit in the same ``try``: a
        slow or broken connection can fail at either point, and a failure
        while reading used to escape the recipe as a traceback.

        This does not use ``zc.buildout.download.Download``, which would
        bring buildout's download cache. That helper has no timeout and no
        retry of its own, so the loop below would stay. Moving to it is a
        change of its own.
        """
        attempts = self.attempts if self._may_retry() else 1
        attempt = 0
        body = None
        last_error = None
        while attempt < attempts:
            attempt += 1
            try:
                with urllib.request.urlopen(
                        self.url, timeout=self.timeout) as response:
                    body = response.read()
                break
            except urllib.error.HTTPError as error:
                # urlopen raises this with the response still open, and the
                # "with" above never ran, so close it here.
                error.close()
                last_error = error
                if error.code not in RETRY_STATUSES:
                    # The server understood us and said no. Asking again
                    # gives the same answer.
                    break
            except (OSError, http.client.HTTPException) as error:
                # URLError covers DNS, connect and proxy failures, and is an
                # OSError. A timeout while waiting for the response arrives
                # as a plain TimeoutError, and a connection that breaks
                # while the body is read as an IncompleteRead, which is an
                # HTTPException. Neither is a URLError, so catching URLError
                # alone would let them through as a traceback.
                last_error = error
            except ValueError as error:
                # A malformed URL. Repeating it cannot help.
                last_error = error
                break
            if attempt < attempts:
                wait = self.retry_wait * 2 ** (attempt - 1)
                self.logger.warning(
                    "Could not download %s (attempt %s of %s): %s. Trying "
                    "again in %s seconds.",
                    self.url, attempt, attempts, last_error, wait)
                time.sleep(wait)

        if body is None:
            msg = (
                "Could not download the template for part '%s' from %s "
                "after %s: %s" % (
                    self.name,
                    self.url,
                    '1 attempt' if attempt == 1 else '%s attempts' % attempt,
                    last_error,
                ))
            self.logger.error(msg)
            raise zc.buildout.UserError(msg)

        try:
            return body.decode(self.input_encoding)
        except UnicodeDecodeError as error:
            # The download itself worked, so do not report a network
            # problem: the part needs another "input-encoding".
            msg = ("The template for part '%s' at %s is not %s: %s" % (
                self.name, self.url, self.input_encoding, error))
            self.logger.error(msg)
            raise zc.buildout.UserError(msg)

    def install(self):
        if not self.overwrite and os.path.isfile(self.output):
            return self.options.created()

        self.createIntermediatePaths(os.path.dirname(self.output))
        try:
            os.remove(self.output)
        except OSError:
            pass
        with open(self.output, "wb") as output:
            output.write(self.result.encode(self.output_encoding))

        if self.mode is not None:
            os.chmod(self.output, self.mode)

        if self.overwrite:
            # prevents deleting after execute buildout after modifying
            # buildout.cfg
            self.options.created(self.output)

        return self.options.created()

    def update(self):
        # Variables in other parts might have changed so we may need to do a
        # full reinstall.
        try:
            with open(self.output, "rb") as f:
                output = f.read()
        except IOError:
            result_changed = True
        else:
            result_changed = output != self.result.encode(self.output_encoding)

        if result_changed:
            # Output has changed, re-write output file
            return self.install()

    def createIntermediatePaths(self, path):
        parent = os.path.dirname(path)
        if os.path.exists(path) or parent == path:
            return
        self.createIntermediatePaths(parent)
        os.mkdir(path)
        self.options.created(path)
