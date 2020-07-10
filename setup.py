from setuptools import setup

setup(
    name='mashru3',
    version='0.1',
    author='Lars-Dominik Braun',
    author_email='ldb@leibniz-psychology.org',
    #url='https://',
    packages=['mashru3'],
    #license='LICENSE.txt',
    description='Workspace manager for guix and conductor',
    long_description=open('README.rst').read(),
    long_description_content_type='text/x-rst',
    install_requires=[
		'unidecode',
		'pyyaml',
    ],
    python_requires='>=3.7',
    entry_points={
    'console_scripts': [
            'workspace = mashru3.cli:main',
            ],
    },
)
