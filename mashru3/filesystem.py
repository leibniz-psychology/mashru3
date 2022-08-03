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

import os, logging
from pathlib import Path
from contextlib import contextmanager
from enum import Enum
from pwd import getpwuid
from grp import getgrgid

import posix1e

from .util import run, ExecutionFailed
from .config import SETFACL_PROGRAM, RSYNC_PROGRAM

logger = logging.getLogger (__name__)

def getMountPoint (path):
	""" Return mount point of path """
	path = Path (path).resolve ()
	while True:
		if path.is_mount ():
			return path
		path = path.parent

def getMount (path):
	""" Get mount point info """
	path = Path (path).resolve ()
	if not path.is_mount ():
		raise ValueError ('Not a mount point')

	ret = None
	with open ('/proc/mounts') as fd:
		for l in fd:
			source, dest, kind, attrib, _, _ = l.split (' ')
			if Path (dest).resolve () == path:
				ret = dict (source=source, dest=dest, kind=kind, attrib=attrib)
	# return the last one, which overrides(?) any previous mounts
	return ret

def isNfs (path):
	""" Check whether a path is on an NFS mount """
	return getMount (getMountPoint (path))['kind'].startswith ('nfs')

class Busy (Exception):
	pass

@contextmanager
def softlock (path):
	try:
		basename = os.path.basename (path)
		dirname = os.path.dirname (path)
		os.makedirs (dirname, exist_ok=True)
		dirfd = os.open (dirname, flags=0)
		fd = os.open (basename, flags=os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode=0o666, dir_fd=dirfd)
	except FileExistsError:
		raise Busy ()
	try:
		yield
	finally:
		try:
			os.close (fd)
			os.unlink (basename, dir_fd=dirfd)
			os.close (dirfd)
		except FileNotFoundError:
			# Ignore errors at this point.
			pass

class PermissionTarget (Enum):
	USER = 'u'
	GROUP = 'g'
	OTHER = 'o'

def setPermissions (target: PermissionTarget, qualifier: str, permissions, path: Path, remove=False, default=False, recursive=False):
	""" ACL abstraction that supports NFS (well…) """
	if isNfs (path):
		raise NotImplementedError ()
#		cmd = ['nfs4_setfacl']
#		flags = 'g'
#		if '@' not in group:
#			group = f'{group}@{defaultRealm()}'
#		if recursive:
#			cmd.append ('-R')
#		if remove:
#			cmd.append ('-x')
#			bits = f'{group}'
#		else:
#			cmd.append ('-a')
#			bits = f'{group}:{bits.upper()}'
#		if default:
#			# directory- and file-inherit
#			flags += 'df'
#		# allow rule
#		bits = f'A:{flags}:{bits}'
#		cmd.append (bits)
	else:
		if remove:
			permissions = '---'

		# use setfacl here instead of pylibacl, because it implements recursion
		# and the X permission (apply +x only to directories)
		cmd = [SETFACL_PROGRAM]
		if recursive:
			cmd.append ('-R')
		spec = target.value
		spec += ':' + (qualifier or '')
		if remove and qualifier:
			cmd.append ('-x')
			# removing ignores permission bits
		else:
			cmd.append ('-m')
			spec += ':' + permissions
		if default:
			spec = f'd:{spec}'
		cmd.append (spec)
	cmd.append (str (path))
	try:
		run (cmd)
	except ExecutionFailed as e:
		logger.debug (cmd)
		logger.debug (e)
		raise

def getPermissions (path: Path):
	if isNfs (path):
		# this codepath is currently not tested
		raise NotImplementedError ('untested')

#		cmd = ['nfs4_getfacl', path]
#		ret = run (cmd, stdout=subprocess.PIPE)
#		for l in ret.stdout.decode ('ascii').split ('\n'):
#			if l.startswith ('#') or not l:
#				# comment, ignore
#				continue
#			kind, flags, ident, bits = l.split (':', 3)
#			bits = ''.join (filter (lambda x: x in {'r', 'w', 'x'}, bits))
#			if kind == 'A' and 'g' in flags:
#				yield ident, bits
	else:
		def fromPermset (permset):
			return str (permset).replace ('-', '')

		def fromUid (dest, uid, permset, extra=''):
			name = getpwuid (s.st_uid).pw_name
			perms = fromPermset (permset) + extra
			dest.update ([(name, perms)])

		def fromGid (dest, gid, permset):
			name = getgrgid (gid).gr_name
			perms = fromPermset (permset)
			dest.update ([(name, perms)])

		acl = posix1e.ACL (file=path)
		s = path.lstat ()
		perms = dict (user=dict (), group=dict (), acl=dict (group=dict (), user=dict ()), other='')
		for entry in acl:
			if entry.tag_type == posix1e.ACL_USER_OBJ:
				# for owner
				fromUid (perms['user'], s.st_uid, entry.permset, 'Tt')
			elif entry.tag_type == posix1e.ACL_GROUP_OBJ:
				# for file group
				fromGid (perms['group'], s.st_gid, entry.permset)
			elif entry.tag_type == posix1e.ACL_USER:
				# named user
				fromUid (perms['acl']['user'], entry.qualifier, entry.permset)
			elif entry.tag_type == posix1e.ACL_GROUP:
				# named group
				fromGid (perms['acl']['group'], entry.qualifier, entry.permset)
			elif entry.tag_type == posix1e.ACL_OTHER:
				# world
				perms['other'] = fromPermset (entry.permset)
			elif entry.tag_type == posix1e.ACL_UNDEFINED_TAG:
				pass
			elif entry.tag_type == posix1e.ACL_MASK:
				# XXX: implement masking
				pass
			else:
				logger.warning (f'got unhandled ACL entry tag_type={entry.tag_type}')
		return perms


def copydir (source: Path, dest: Path):
	""" Recursively copy directory """
	source = str (source)
	dest = str (dest)
	# until shutil.copytree does not suck any more
	if not source.endswith ('/'):
		source += '/'
	if not dest.endswith ('/'):
		dest += '/'
	cmd = [RSYNC_PROGRAM,
			'--recursive',
			'--links', # preserve symlinks
			'--group', # preserve group
			'--executability', # preserve execute bit
			# --sparse and --preallocate would be benefitial, but do not work on NFS
			'--times', # preserve mtime
			source, dest]
	# do not fail, if some files cannot be copied (23)
	run (cmd, permittedExitCodes=[0, 23])

