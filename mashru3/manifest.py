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

import re, logging

def modifyManifest (manifest, specs):
	""" Modify a manifest, based on specs, which are strings prefixed by + or - """

	# XXX: Obviously having a proper Scheme parser would be nice here, but a
	# few regexes are less code for now.
	r = re.compile (r'(\(specifications->manifest\s+\'\()(.*)\)\)', re.DOTALL)

	def modifyPackages (m):
		l = m.group (2)

		for a in specs:
			if a.startswith ('+'):
				s = f'"{a[1:]}"'
				if s not in l:
					l += s + '\n'
				else:
					logging.debug ('Package "{a}" already exists')
			elif a.startswith ('-'):
				s = f'"{a[1:]}"'
				l = l.replace (s, '')
			else:
				# no prefix means replace all
				l = f'"{a}"'

		return f'{m.group(1)}{l}))'

	ret, replacements = r.subn (modifyPackages, manifest)
	if replacements == 0:
		raise ValueError ('Cannot parse manifest')
	return ret


