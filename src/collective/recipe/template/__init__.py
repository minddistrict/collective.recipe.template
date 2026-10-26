import logging
import os
import re
import stat
import sys
import time
import urllib.request
import urllib.error
import zc.buildout


TRUE_VALUES = ('y', 'yes', '1', 'true')

# How often a template download is tried before the part gives up, and the
# first wait in between. The wait doubles after every failed attempt.
DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_BACKOFF = 1.0
# Seconds to wait for a download when the part sets no "timeout" option.
# Generous on purpose: on a busy link a connection can take more than ten
# seconds and still succeed, and aborting the buildout costs far more than
# waiting does.
DOWNLOAD_TIMEOUT = 30.0
# HTTP statuses that deserve another attempt. Every other 4xx says the
# request itself is wrong, so repeating it cannot help.
RETRY_STATUSES = frozenset((408, 429, 500, 502, 503, 504))


class Recipe:
    def __init__(self, buildout, name, options):
        self.buildout = buildout
        self.name = name
        self.options = options
        self.logger = logging.getLogger(self.name)
        self.msg = None

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
        self.timeout = float(options.get("timeout", DOWNLOAD_TIMEOUT))
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
            source = self._download()
            if source is None:
                self.logger.error(self.msg)
                raise zc.buildout.UserError(self.msg)
            self.source = source
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

    def _download(self):
        """Return the template text at ``self.url``.

        Returns ``None`` and sets ``self.msg`` when every attempt failed.

        Both the request and the reading of the body sit inside the same
        ``try``: a slow server can time out at either point, and until now a
        timeout during the read escaped the recipe as a traceback.
        """
        last_error = None
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(
                        self.url, timeout=self.timeout) as response:
                    return response.read().decode(self.input_encoding)
            except urllib.error.HTTPError as error:
                last_error = error
                if error.code not in RETRY_STATUSES:
                    # The server understood us and said no. Asking again
                    # gives the same answer.
                    break
            except OSError as error:
                # URLError covers DNS, connect and proxy failures. A timeout
                # while waiting for the response, or while reading it,
                # arrives as a plain TimeoutError instead. That is an
                # OSError but not a URLError, so catching URLError alone
                # would let it through.
                last_error = error
            except ValueError as error:
                # A malformed URL. Repeating it cannot help.
                last_error = error
                break
            if attempt < DOWNLOAD_ATTEMPTS:
                time.sleep(DOWNLOAD_BACKOFF * 2 ** (attempt - 1))
        self.msg = (
            "Could not download the template for part '%s' from %s after "
            "%s: %s" % (
                self.name,
                self.url,
                '1 attempt' if attempt == 1 else '%s attempts' % attempt,
                last_error,
            ))
        return None

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
