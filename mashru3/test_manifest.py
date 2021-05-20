# Copyright 2019–2021 Leibniz Institute for Psychology
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

import pytest

from .manifest import modifyManifest

@pytest.mark.parametrize("manifest,specs,expected", [
	pytest.param ("(specifications->manifest '())", [], "(specifications->manifest '())", id='noop'),
	pytest.param ("(specifications->manifest\n'())", [], "(specifications->manifest\n'())", id='whitespace'),
	pytest.param ("(specifications->manifest '())", ['+foobar'], "(specifications->manifest '(\"foobar\"\n))", id='add-single'),
	pytest.param ("(specifications->manifest '(\"foobar\"))", ['-foobar'], "(specifications->manifest '())", id='remove-single'),
	pytest.param ("""(specifications->manifest
;; Comment
'("foobar"))""", ["-foobar"], """(specifications->manifest
;; Comment
'())""", id='comment', marks=pytest.mark.xfail),
	pytest.param ("", ['-foobar'], ValueError, id='no-manifest'),
	])
def test_modifyManifest (manifest, specs, expected):
	if isinstance (expected, type):
		with pytest.raises (expected):
			modifyManifest (manifest, specs)
	else:
		assert modifyManifest (manifest, specs) == expected

