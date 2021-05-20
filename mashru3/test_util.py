# Copyright 2019–2020 Leibniz Institute for Psychology
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from io import StringIO
import os

import pytest
from tempfile import TemporaryDirectory

from .util import getattrRecursive, prefixes, isPrefix, limit, parseRecfile, softlock, Busy

def test_prefixes ():
	assert list (prefixes ([])) == []
	assert list (prefixes ([1, 2, 3])) == [[1], [1, 2], [1, 2, 3]]

def test_isPrefix ():
	assert isPrefix ('abc', 'abc')
	assert not isPrefix ('abc', 'ab')
	assert isPrefix ([1, 2], [1, 2, 3])
	assert not isPrefix ([1, 3], [1, 2, 3])

def test_getattrRecursive ():
    o = dict (a=dict (b=dict (c=1)), d=2)

    assert getattrRecursive (o, 'a.b.c') == 1
    assert getattrRecursive (o, 'd') == 2

def test_limit ():
	assert list (limit ([1, 2, 3], 0)) == []
	assert list (limit ([1, 2, 3], 1)) == [1]
	assert list (limit ([1, 2, 3], 2)) == [1, 2]

# Examples taken from recutil documentation:
# https://www.gnu.org/software/recutils/manual/The-Rec-Format.html#The-Rec-Format
# We do not support record descriptors.
@pytest.mark.parametrize("rec,expected", [
	pytest.param ("""Foo: bar1
+ bar2
+  bar3""", [dict(Foo="bar1\nbar2\n bar3")], id='continuation'),
	pytest.param ("Foo:\tbar1\n", [dict(Foo="bar1")], id='tab-sep'),
	pytest.param ("""Name: Ada Lovelace
Age: 36

Name: Peter the Great
Age: 53

Name: Matusalem
Age: 969""", [dict(Name="Ada Lovelace", Age="36"),
		dict(Name="Peter the Great", Age="53"),
		dict(Name="Matusalem", Age="969")], id='multi-record'),
	# XXX: Should use multidict here?
	pytest.param ("""Name: John Smith
Email: john.smith@foomail.com
Email: john@smith.name""", [dict(Name='John Smith',
		Email='john.smith@foomail.com' #, Email='john@smith.name'
		)],
		id='multi-value', marks=pytest.mark.xfail),
	pytest.param ("""Name: Jose E. Marchesi
# Occupation: Software Engineer
# Severe lack of brain capacity
# Fired on 02/01/2009 (without compensation)
Occupation: Unoccupied""", [dict(Name='Jose E. Marchesi', Occupation='Unoccupied')], id='comments'),
	pytest.param ("""Foo:

Bar:""", [dict(Foo=""), dict(Bar='')], id='empty-field'),
	])
def test_parseRecfile (rec, expected):
	assert list (parseRecfile (StringIO (rec))) == expected

def test_softlock_cleanup ():
	with TemporaryDirectory () as d:
		lockpath = os.path.join (d, 'lock')
		with softlock (lockpath):
			assert os.path.exists (lockpath)
		assert not os.path.exists (lockpath)

def test_softlock_cleanup_exception ():
	with TemporaryDirectory () as d:
		lockpath = os.path.join (d, 'lock')
		with pytest.raises (Exception):
			with softlock (lockpath):
				assert os.path.exists (lockpath)
				raise Exception ('nope')
		assert not os.path.exists (lockpath)

def test_softlock_busy ():
	with TemporaryDirectory () as d:
		lockpath = os.path.join (d, 'lock')
		with softlock (lockpath):
			with pytest.raises (Busy):
				with softlock (lockpath):
					assert False

