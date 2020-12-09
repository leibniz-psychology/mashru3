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

def getattrRecursive (obj, name):
	"""
	Recursive version of getattr, which splits name at dots and recurses
	"""
	def getattrOrGetitem (obj, name):
		try:
			return getattr (obj, name)
		except AttributeError:
			return obj[name]

	try:
		thisName, other = name.split ('.', 1)
		return getattrRecursive (getattrOrGetitem(obj, thisName), other)
	except ValueError:
		return getattrOrGetitem (obj, name)

def prefixes (l):
	""" Get all prefixes for list l, i.e. [1, 2, 3] → [1], [1, 2], [1, 2, 3] """
	p = []
	for e in l:
		p.append (e)
		yield list (p)

def isPrefix (a, b):
	""" Return true if a is prefix of b """
	if len (a) > len (b):
		return False
	return all (map (lambda x: x[0] == x[1], zip (a, b)))

