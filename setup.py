from setuptools import setup

setup(
    name='mashru3',
    version='0.1',
    author='Lars-Dominik Braun',
    author_email='ldb@leibniz-psychology.org',
    url='https://github.com/leibniz-psychology/mashru3',
    packages=['mashru3'],
    description='Workspace manager for guix and conductor',
    long_description=open('README.rst').read(),
    long_description_content_type='text/x-rst',
    install_requires=[
        'unidecode',
        'pyyaml',
        'pytz',
        'python-magic',
		'pylibacl',
    ],
    python_requires='>=3.7',
    entry_points={
    'console_scripts': [
            'workspace = mashru3.cli:main',
            ],
    },
    classifiers = [
        'License :: OSI Approved :: MIT License',
        'Development Status :: 4 - Beta',
        'Operating System :: POSIX :: Linux',
        'Programming Language :: Python :: 3',
        ],
)
