Introduction
************

This recipe can be used to generate textfiles from a (text)
template.

.. contents::

A short example::

  [buildout]
  parts = message

  [message]
  recipe = collective.recipe.template
  input = templates/message.in
  output = ${buildout:parts-directory}/etc/message

  mymessage = Hello, World!


In the template you can use the exact same variables as you can use
in the buildout configuration. For example an input file can look like this::

  My top level directory is ${buildout:directory}
  Executables are stored in ${buildout:bin-directory}


As an extension to the buildout syntax you can reference variables from
the current buildout part directly. For example::

  My message is: ${mymessage}


Features
========

* Starting with version 1.3, you can also specify a path to the output
  file and the path will be created if it does not exist.
* Starting with version 1.5, you can use inline templates.
* Starting with version 1.7, you can use `genshi text templates`_.
* Starting with version 1.9, you can use a URL to specify template input.
* Starting with version 1.12, you can specify ``timeout`` as an option to set
  how long the recipe waits for a URL download. The default is 30 seconds.
  It counts per attempt, not for all attempts together.
* Starting with version 2.1, you can set ``input-encoding`` and
  ``output-encoding`` to specify the character encoding.
* Starting with version 2.2+md.6, a download that failed by accident is tried
  again. The recipe makes three attempts, and waits one second before the
  second and two seconds before the third. The ``attempts`` and ``retry-wait``
  options change these numbers. Tried again are a network failure and the HTTP
  statuses 408, 429, 500, 502, 503 and 504. Any other answer, a 404 for
  example, is not tried again, because it would be the same answer. A URL that
  is not ``http``, ``https`` or ``ftp`` is not tried again either: a local file
  gives the same result every time.

  With the default options, a host that never answers takes about 93 seconds
  (three attempts of 30 seconds, plus 3 seconds of waiting) before the part
  reports the failure.

Genshi text templates
---------------------

A short example::

  [buildout]
  parts = message

  [message]
  recipe = collective.recipe.template[genshi]:genshi
  input = templates/message.in
  output = ${buildout:parts-directory}/etc/message
  some-option = value

  mymessage = Hello, World!

In the template you can use the exact same variables as you can use
in the buildout configuration, but instead of colons as the separator you
either have to use attribute access, or for options with a dash dictionary
syntax. The global buildout config is accessible through ``parts``, the
current part through ``options``.

For example an input file can look like this::

  My top level directory is ${parts.buildout.directory}
  Executables are stored in ${parts.buildout['bin-directory']}
  Accessing the current part: ${options['some-option']}


Why another template recipe?
============================

Both `iw.recipe.template`_ and `inquant.recipe.textfile`_ claim to do the
same thing. I have found them to be undocumented and too buggy for real
world use, and neither are in a public repository where I could fix them. In
addition this implementation leverages the buildout variable substitution
code, making it a lot simpler.


.. _genshi text templates: http://genshi.edgewall.org/wiki/Documentation/text-templates.html
.. _iw.recipe.template: http://pypi.python.org/pypi/iw.recipe.template
.. _inquant.recipe.textfile: http://pypi.python.org/pypi/inquant.recipe.textfile
