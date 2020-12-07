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

from .util import getattrRecursive, prefixes, isPrefix

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

