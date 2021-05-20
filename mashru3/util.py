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

import re, os
from contextlib import contextmanager

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

def parseRecfile (fd):
	""" Simple recfile parser """
	record = dict ()
	lastkey = None
	for l in fd:
		l = l.rstrip ('\n')
		if not l:
			# new record
			yield record
			record = dict ()
			lastkey = None
			continue
		if l.startswith ('#'):
			# ignore comments
			continue

		if l.startswith ('+ ') and lastkey:
			# continuation
			record[lastkey] += '\n' + l[2:]
			continue

		k, v = re.split (r':(?:[\t ]|$)', l, maxsplit=1)
		record[k] = v
		lastkey = k
	if record:
		yield record

def limit (it, n):
	i = 0
	for v in it:
		i += 1
		if i > n:
			break

		yield v

class Busy (Exception):
	pass

@contextmanager
def softlock (path):
	try:
		os.makedirs (os.path.dirname (path), exist_ok=True)
		fd = os.open (path, flags=os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode=0o666)
	except FileExistsError:
		raise Busy ()
	try:
		yield
	finally:
		os.close (fd)
		os.unlink (path)

