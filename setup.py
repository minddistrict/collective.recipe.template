from pathlib import Path

from setuptools import find_namespace_packages
from setuptools import setup


version = '2.3.dev0'

setup(
    name='collective.recipe.template',
    version=version,
    description="Buildout recipe to generate a text file from a template",
    long_description='\n'.join(
        Path(f).read_text(encoding='utf-8')
        for f in ('README.rst',
                  'src/collective/recipe/template/README.rst',
                  'CHANGES.rst')
    ),
    classifiers=[
        "Framework :: Buildout",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    keywords='template recipe',
    author='Wichert Akkerman',
    author_email='wichert@wiggy.net',
    url='https://github.com/collective/collective.recipe.template',
    license='BSD',
    packages=find_namespace_packages('src'),
    package_dir={'': 'src'},
    include_package_data=True,
    zip_safe=False,
    python_requires='>=3.10',
    install_requires=[
        'setuptools',
        'zc.buildout',
    ],
    extras_require=dict(
        test=['zope.testing', 'zope.testrunner'],
        genshi=['Genshi>=0.7.0'],
    ),
    entry_points="""
    [zc.buildout]
    default = collective.recipe.template:Recipe
    genshi = collective.recipe.template.genshitemplate:Recipe
    """,
)
