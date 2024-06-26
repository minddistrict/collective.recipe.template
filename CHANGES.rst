Changelog
*********

2.2+md.5 (unreleased)
=====================

- Nothing changed yet.


2.2+md.4 (2024-05-14)
=====================

- Add "eggs" working set to template context when it is mentioned in the part.
  Shamelessly copied from `gobre.recipe.template`.

2.2+md.3 (2023-09-18)
=====================

- Remove six.

2.2+md.2 (2023-09-13)
=====================

- Allow empty string for input/url/inline combo.

2.2+md.1 (2023-09-13)
=====================

- Sync with upstream, get rid of 2to3 warnings.

2.2 (2021-12-01)
================

* Port code and tests to Python 3 instead of using no longer supported 2to3.

* Add support for Python 3.7, 3.8, 3.9, and 3.10.

* Drop support for Python 3.4.

2.1+md.6 (2021-03-17)
=====================

* Allow Genshi template to include other files.

* Fix ordering issue when rendering the template. Only do it when the
  recipe is installed.

* Claim support for Python 3.6.

* Allow to override url/input in a buildout part that inherits from another
  one.

2.1 (2018-07-14)
================

* Support new ``input-encoding`` and ``output-encoding`` options.
  [fschulze]

* On update, do not rewrite the output file (thus preserving its
  modification timestamp) unless its content has changed.
  [dairiki]

2.0 (2017-01-17)
================

* Claim support for Python 3.5 and drop support for Python 2.6.
  [sallner]

1.13 (2015-10-20)
=================

* Back compatibility with zc.buildout 1.7.1 [#11]
  [mstaniszczak]


1.12 - 2015-07-23
=================

* Add timeout configuration option.
  [davidjb]

* Fix encoding problem in python 3.
  [cedricmessiant]

* Added overwrite option - possibility to disable overwrite output file after
  re-execute buildout.
  [mstaniszczak]


1.11 - 2014-02-07
=================

* Python 3 support for Genshi and doctests.
  [mitchellrj]

* Delete script before writing to it, this way we avoid chmod permission errors
  when the current user is not the script owner.
  [alecghica]


1.10 - 2012-02-26
=================

* Add Python 3 support using 2to3 flag in setup.
  [mitchellrj]


1.9 - 2011-06-19
================

* Add support for URL input. Use ``url =`` (instead of ``input =``) to specify URL.
  [aclark]


1.8 - 2010-06-08
================

* WARNING! Backward incompatible change for Genshi templates.
  It wasn't possible to access parts with a dash in the name, so now you have
  to use ${parts.partname} or ${parts['part-name']}. In addition it is now
  possible to access the current part with ``options``.
  [fschulze]

* Import genshi modules very late to prevent issues with zc.buildout.
  [fschulze]


1.7 - 2010-05-21
================

* Added support for genshi text templates. Use them with this as the
  recipe:
  `recipe = collective.recipe.template[genshi]:genshi`
  Use a dot between the section name and the option name instead of a colon.
  [fschulze]


1.6 - 2010-02-24
================

* Output file mode is now assumed to be octal, like chmod.
  [elro]

* Inline template can now be specified with the inline option.
  [elro]


1.5 - 2010-02-23
================

* Add support for explicitly setting the output file mode.
  [witsch]

* Add support for inline templates.
  [witsch]


1.4 - 2009-07-29
================

* Fixed the way variables in templates are substituted to allow buildout to
  determine dependencies on other parts and prepare those correctly. [tlotze]


1.3 - 2009-04-28
================

* Add support for output path creation. You can do::

    output = /path/to/target

  and intermediate path items will be created if they do not exist.
  [ulif]

* Add tests.
  [ulif]


1.2 - 2008-12-09
================

(By accident the 1.1 release was marked as 1.2. So in fact they are
the same.)

1.1 - 2008-12-09
================

* Correct handling of multiple variables in a line. Bugreport and patch from
  Roman Susi.
  [wichert]


1.0 - 2008-10-16
================

* Copy the mode of the input file to the output file. This makes it possible
  to create executable scripts.
  [wichert]

* Add missing link in README.
  [wichert]


1.0rc2 - 2008-07-04
===================

* Add a MANIFEST.in with instructions to include docs/, otherwise the package
  will not install.
  [wichert]


1.0rc1 - 2008-07-04
===================

* Initial release.
  [wichert]
