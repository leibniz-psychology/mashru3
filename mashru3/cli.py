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

import argparse, re, os, subprocess, logging, shutil, sys, shlex, configparser, \
		json, secrets, stat, random, tempfile, traceback, time, signal
from enum import Enum, auto, Flag
from pathlib import Path
from getpass import getuser
from datetime import datetime
from functools import partial
from collections import defaultdict
from hashlib import blake2b
from base64 import b32encode
from fnmatch import fnmatchcase
from io import StringIO
from pwd import getpwuid
from grp import getgrgid

import yaml, pytz
from unidecode import unidecode
import magic
import posix1e

from .uid import uintToQuint
from .krb5 import defaultRealm
from .util import getattrRecursive, prefixes, isPrefix, parseRecfile, limit, Busy, softlock
from .manifest import modifyManifest

logger = logging.getLogger ('cli')
ZIP_PROGRAM = 'zip'
UNZIP_PROGRAM = 'unzip'
TAR_PROGRAM = 'tar'
LZIP_PROGRAM = 'lzip'
# Must support `guix environment -p`
GUIX_PROGRAM = 'guix'

def now ():
	return datetime.now (tz=pytz.utc)

class Formatter (Enum):
	HUMAN = auto ()
	YAML = auto ()
	JSON = auto ()

class Encoder (json.JSONEncoder):
	def default (self, obj):
		if isinstance(obj, datetime):
			return obj.isoformat ()
		elif isinstance (obj, Path):
			return str (obj)
		elif isinstance (obj, bytes):
			return obj.decode ('utf-8', 'replace')
		return json.JSONEncoder.default (self, obj)

def jsonDump (o, fd=None):
	return json.dump (o, fd, cls=Encoder)

class InstalledPackage:
	def __init__ (self, name, version, output, path):
		self.name = name
		self.version = version
		self.output = output
		self.path = path

	def toDict (self):
		return dict (name=self.name, version=self.version, output=self.output, path=self.path)

class WorkspaceException (Exception):
	pass

class InvalidWorkspace (WorkspaceException):
	pass

class Workspace:
	# packages that are essential to mashru3 and must always be installed
	extraPackages = ['tini']

	def __init__ (self, d, meta=None):
		# create default uid with 64 random bits
		defaultMeta = dict (
				version=1,
				_id=self.randomId (),
				)
		if meta:
			defaultMeta.update (meta)
		self.metadata = defaultMeta
		self.directory = Path (d).resolve ()

	@staticmethod
	def randomId ():
		return uintToQuint (secrets.randbelow (2**64), 4)

	def toDict (self):
		wsdir = self.directory
		d = dict (path=str (wsdir),
				profilePath=str (self.profilepath.resolve ()),
				metadata=self.metadata,
				permissions=getPermissions (wsdir),
				applications=list (self.applications),
				packages=[p.toDict () for p in self.packages],
				)
		return d

	def writeMetadata (self):
		with softlock (self.metapath.with_suffix ('.lock')):
			tmpPath = self.metapath.with_suffix ('.tmp')
			with open (tmpPath, 'w') as fd:
				yaml.dump (self.metadata, fd)
			os.rename (tmpPath, self.metapath)

	@property
	def configdir (self):
		return self.directory / '.config'

	@property
	def cachedir (self):
		return self.directory / '.cache'

	@property
	def guixdir (self):
		return self.configdir / 'guix'

	@property
	def guixbin (self):
		return self.guixdir / 'current' / 'bin' / 'guix'

	@property
	def metapath (self):
		""" Path for metadata file """
		return self.configdir / 'workspace.yaml'

	@property
	def manifestpath (self):
		return self.guixdir / 'manifest.scm'

	@property
	def channelpath (self):
		return self.guixdir / 'channels.scm'

	@property
	def profilepath (self):
		return self.directory / '.guix-profile'

	@property
	def applications (self):
		# dummy application to start a shell
		yield dict (name='Shell', exec=None, _id='org.leibniz-psychology.mashru3.shell')

		searchdirs = [self.directory / '.local' / 'share',
				self.profilepath / 'share',
				self.guixdir / 'current' / 'share']
		for datadir in map (lambda x: x / 'applications', searchdirs):
			for root, dirs, files in os.walk (datadir):
				for f in filter (lambda x: x.endswith ('.desktop'), files):
					path = os.path.join (root, f)
					config = configparser.ConfigParser ()
					config.read (path)
					entry = dict (config['Desktop Entry'])
					entry['_id'] = os.path.relpath (path, start=datadir).replace ('/', '-')
					# not checking tryexec here, because that would require
					# running guix environment
					if entry.get ('type') == 'Application':
						yield entry

	@property
	def packages (self):
		""" Get installed packages """
		if not self.guixbin.exists ():
			return []

		cmd = [str (self.guixbin), "package", "-p", self.profilepath, "-I"]
		ret = run (cmd, stdout=subprocess.PIPE)
		lines = ret.stdout.decode ('utf-8').split ('\n')
		for l in lines:
			try:
				name, version, output, path = l.split ('\t')
			except ValueError:
				continue
			yield InstalledPackage (name=name, version=version, output=output, path=path)

	@property
	def envcmd (self):
		""" Command that starts a guix environment """
		user = 'joeuser'
		cmd = [GUIX_PROGRAM,
				'environment', '-C', '-N',
				'-u', user,
				# allow passing the current language, assume GUIX_LOCPATH is
				# set properly before starting
				'-E', '^(LANG|GUIX_LOCPATH|TZDIR)$',
				'--no-cwd',
				f'--share={self.directory}=/home/{user}',
				f'--profile={self.profilepath}',
				]
		return cmd

	def ensureGuix (self):
		"""
		Ensure the guix binary matches the channel file.

		Usually calling .ensureProfile() is enough.
		"""

		with softlock (self.cachedir.joinpath (__package__ + '.ensureGuix.lock')):
			channelPath = self.channelpath
			channelMtime = channelPath.stat ().st_mtime if channelPath.exists () else 0

			guixbin = self.guixbin
			guixbinExists = guixbin.exists ()
			profilePath = self.guixdir / 'current'
			profileMtime = profilePath.lstat().st_mtime if guixbinExists else 0

			# This should work most of the time™
			if not guixbinExists or channelMtime > profileMtime:
				logger.debug (f'Getting a fresh guix, exists {guixbin.exists()}, mtime {channelMtime} >? {profileMtime}')
				os.makedirs (self.guixdir, exist_ok=True)
				# Use host guix to bootstrap workspace.
				cmd = ['guix', 'pull',
						'-p', str (profilePath),
						]
				# use channel file from skeleton instead of system default if it exists
				if os.path.isfile (channelPath):
					cmd.extend (['-C', str (channelPath)])
				try:
					run (cmd)
				except (ExecutionFailed, KeyboardInterrupt):
					logger.error ('Failed to initialize guix')
					raise

			# pin guix version, so copying the project will use the exact same version
			tmpChannelPath = str (channelPath) + '.tmp'
			with open (tmpChannelPath, 'w') as fd:
				cmd = [str (guixbin), 'describe', '-f', 'channels']
				run (cmd, stdout=fd)
			# fix mtime. Otherwise the Guix profile would be refreshed everytime we
			# run.
			os.utime (tmpChannelPath, times=(profileMtime, profileMtime))
			# atomic overwrite
			os.rename (tmpChannelPath, channelPath)

	def ensureProfile (self):
		""" Ensure the profile directory .guix-profile exists and matches the current manifest and guix """
		# we need a runnable guix
		with softlock (self.cachedir.joinpath (__package__ + '.ensureProfile.lock')):
			self.ensureGuix ()

			guixprofilePath = self.guixdir / 'current'
			guixprofileMtime = guixprofilePath.lstat().st_mtime

			profilePath = self.profilepath
			profileExists = profilePath.exists ()
			profileMtime = profilePath.lstat ().st_mtime if profileExists else 0

			manifestPath = self.manifestpath
			manifestExists = manifestPath.exists ()
			manifestMtime = manifestPath.stat ().st_mtime if manifestExists else 0

			haveExtraPackages = list (map (lambda x: x.name,
					filter (lambda x: x.name in self.extraPackages, self.packages))) == self.extraPackages

			if not profileExists or \
					manifestMtime > profileMtime or \
					guixprofileMtime > profileMtime or \
					not haveExtraPackages:
				logger.debug (f'Refreshing profile, exists {profilePath.exists()}, '
						f'mtime {manifestMtime} >? {profileMtime}, '
						f'guixmtime {guixprofileMtime} >? {profileMtime}'
						f'haveExtraPackages {haveExtraPackages}')
				cmd = [str (self.guixbin), 'package',
						'-p', str (profilePath),
						'--allow-collisions',
						]
				if manifestExists:
					cmd.extend (['-m', str (manifestPath)])
				if self.extraPackages:
					cmd.append ('-i')
					cmd.extend (self.extraPackages)
				run (cmd)
				if profilePath.exists ():
					# Guix can decide there is nothing to do and will not change
					# the symlinks. Make sure we don’t run this again by setting a
					# new c/mtime.
					now = time.time ()
					os.utime (profilePath, times=(now, now), follow_symlinks=False)

	@classmethod
	def open (cls, d: Path):
		"""
		Verify directory d is a valid workspace and get its metadata
		"""
		ws = cls (d)
		checkfiles = [ws.metapath, ]
		try:
			if all (map (lambda x: x.exists (), checkfiles)):
				with open (ws.metapath) as fd:
					ws.metadata = yaml.safe_load (fd)
					return ws
		except PermissionError:
			# .exists() call .stat(), which can fail
			pass
		raise InvalidWorkspace ()

	@staticmethod
	def nameToDir (name):
		# use lowercase, unicode-stripped name as directory. Special characters
		# are replaced by underscore, but no more than one successive
		# underscore and not at the beginning or the end.
		r = re.compile (r'[^a-z0-9]+')
		subdir = r.sub ('_', unidecode (name.lower ())).strip ('_')
		if not subdir:
			# simply generate one
			subdir = 'unnamed_project'
		return subdir

	@classmethod
	def nameToPath (cls, name, suggestedDir):
		if suggestedDir.exists ():
			if suggestedDir.is_dir ():
				# if no dir is given, create one based on the name
				subdir = cls.nameToDir (name)
				ext = ''
				while True:
					directory = suggestedDir / (subdir + ext)
					if not directory.exists ():
						break
					ext = f'_{random.randint (0, 2**16)}'
				logger.debug (f'choosing directory {directory} based on name {name}')
			else:
				raise ValueError ('Destination exists')
		else:
			# use as-as
			directory = suggestedDir

		return directory

	@classmethod
	def create (cls, suggestedDir: Path, metadataOverride):
		stamp = now ()
		metadata = dict (created=stamp, modified=stamp, creator=getuser ())
		metadata.update (metadataOverride)

		directory = cls.nameToPath (metadata.get ('name', ''), suggestedDir)

		return cls (directory, metadata)

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

class ExecutionFailed (Exception):
	pass

def run (cmd, stdout=subprocess.PIPE, permittedExitCodes=None):
	logger.debug (f'running {cmd}')
	ret = subprocess.run (cmd, stdout=stdout, stderr=subprocess.PIPE)
	permittedExitCodes = permittedExitCodes or [0]
	if ret.returncode not in permittedExitCodes:
		raise ExecutionFailed (cmd, permittedExitCodes, ret)
	return ret

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
		cmd = ['setfacl']
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
		def fromUid (uid, permset, extra=''):
			name = getpwuid (s.st_uid).pw_name
			ret = perms['user'][name] = fromPermset (permset) + extra
			return ret
		def fromGid (gid, permset):
			name = getgrgid (gid).gr_name
			ret = perms['group'][name] = fromPermset (permset)
			return ret

		acl = posix1e.ACL (file=path)
		s = path.lstat ()
		perms = dict (user=dict (), group=dict (), other='')
		for entry in acl:
			if entry.tag_type == posix1e.ACL_USER_OBJ:
				# for owner
				p = fromUid (s.st_uid, entry.permset, 'Tt')
			elif entry.tag_type == posix1e.ACL_GROUP_OBJ:
				# for file group
				fromGid (s.st_gid, entry.permset)
			elif entry.tag_type == posix1e.ACL_USER:
				# named user
				fromUid (entry.qualifier, entry.permset)
			elif entry.tag_type == posix1e.ACL_GROUP:
				# named group
				fromGid (entry.qualifier, entry.permset)
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

def initWorkspace (ws, verbose=False):
	# Fix permissions. Make sure the creator has default permissions, so files
	# created by other users are accessible by default.
	setPermissions (PermissionTarget.USER, getuser (), 'rwX', ws.directory, default=True, recursive=True)

	ws.ensureProfile ()

	ws.writeMetadata ()

	return True

def formatResult (args, r, human=None):
	if args.format == Formatter.HUMAN:
		if human:
			print (human)
	elif args.format == Formatter.YAML:
		yaml.dump (r, sys.stdout)
		sys.stdout.write ('---\n')
	elif args.format == Formatter.JSON:
		jsonDump (r, sys.stdout)
		sys.stdout.write ('\n')
	else:
		assert False

def formatWorkspace (args, ws):
	formatResult (args, ws.toDict (), f'{ws.directory}')

def docreate (args):
	ws = Workspace.create (args.directory, dict (name=' '.join (args.name)))
	logger.info (f'Creating workspace {ws.metadata["name"]} at {ws.directory}')

	skeldirs = [Path.home() / '.config' / __package__ / 'skel',
			Path ('/etc/' + __package__ + '/skel')]
	for d in skeldirs:
		if d.is_dir ():
			logger.debug (f'Copying skeleton at {d} to {ws.directory}')
			copydir (d, ws.directory)
			break
	if not ws.directory.is_dir ():
		logger.debug (f'No skeleton directory found, creating empty workspace.')
		os.makedirs (ws.directory)

	initWorkspace (ws, verbose=args.verbose)

	# finally print the workspace directory, so it can be consumed by scripts
	formatWorkspace (args, ws)

	return 0

def dorun (args):
	""" Run program inside workspace """

	ws = Workspace.open (args.directory)
	ws.ensureProfile ()

	# find the application requested
	matches = []
	for entry in sorted (ws.applications, key=lambda x: x.get ('name').lower ()):
		if not args.application:
			formatResult (args, dict (entry), entry.get ('name'))
		elif args.application.lower() in entry.get ('name').lower () or \
				args.application.lower () == entry.get ('_id').lower ():
			matches.append (entry)

	if not args.application:
		# only searching
		return 0

	if not matches:
		logger.error ('Application not found')
		return 1
	elif len (matches) > 1:
		logger.error ('Multiple applications found:')
		for m in matches:
			logger.error (m.get ('name'))
		return 1
	entry = matches[0]

	try:
		if entry:
			execcmd = entry.get ('exec')
			interfaces = set (entry.get ('interfaces', '').split (','))
			logger.debug (f'desktop file has interfaces {interfaces}')
			isConductorApp = 'org.leibniz-psychology.conductor.v1' in interfaces
		else:
			execcmd = None
		cmd = []
		socketDir = None
		socket = None
		if isConductorApp:
			conductorServer = args.conductorServer
			if not conductorServer:
				logger.error ('No remote conductor server set up.')
				return 1
			if args.user:
				conductorServer = f'{args.user}@{conductorServer}'
			socketDir = tempfile.TemporaryDirectory (prefix=__package__)
			socket = Path (socketDir.name) / '.conductor-socket'
			# use short hash of the socket path to create unique url key. Note
			# that digest_size must be chosen such that base32 does not append
			# padding and it must be short enough not to overflow hostname
			# limits (usually 64 characters).
			key = b32encode (blake2b (str (socket).encode ('utf-8'), digest_size=10).digest ()).decode ('ascii').lower ()
			cmd += ['conductor',
					'-k', key,
					'-r', # replace
					conductorServer,
					str (socket),
					'--',
					]
			if args.verbose:
				cmd.insert (1, '-v')
		cmd += ws.envcmd
		# the app creates the socket, so the entire directory must be shared,
		# not just exposed.
		if socketDir:
			cmd.append (f'--share={socketDir.name}')
		if execcmd:
			# run tini which will handle all the pid 1 stuff properly (reap
			# zombies, signal handling, …)
			cmd.extend ([
					'--',
					'tini',
					'-p', 'SIGTERM', # die with SIGTERM if parent dies
					'-s', # enable subreaping (ot really required)
					'-g', # kill process group
					'--'])
			cmd.extend (shlex.split (execcmd))
			# The -s argument is part of the .v1 interface.
			if socket:
				cmd.extend (['-s', str (socket)])
		logger.debug (' '.join (cmd))

		if args.dryRun:
			print (' '.join (map (shlex.quote, cmd)))
			return 0

		# set a proper locpath
		os.environ['GUIX_LOCPATH'] = '/home/joeuser/.guix-profile/lib/locale'
		# path for timezone data (if installed, not set via search-path)
		os.environ['TZDIR'] = '/home/joeuser/.guix-profile/share/zoneinfo'
		try:
			p = subprocess.Popen (cmd)

			# set up signal handling
			def stop (signal, frame):
				p.terminate ()
			# SSH sends SIGHUP?
			for s in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
				signal.signal (s, stop)

			p.wait ()
		except KeyboardInterrupt:
			pass

		p.terminate ()
		ret = p.wait (3)
		if ret is None:
			logger.debug ('program not responding to SIGTERM, killing')
			p.kill ()
			ret = p.wait ()
		logger.debug (f'program returned {ret}')
	finally:
		if socketDir:
			socketDir.cleanup ()

	return ret

def dolist (args):
	""" List workspaces """
	# load ignored projects
	ignored = set ()
	for path in args.ignore:
		if os.path.exists (path):
			with open (path) as fd:
				try:
					ignored.update (yaml.safe_load (fd))
				except TypeError:
					pass

	searchPath = set (map (lambda x: Path (x).resolve (), args.searchPath))
	# if no search paths were given, use the operating directory instead
	if not searchPath:
		searchPath.add (args.directory.resolve ())
	for d in searchPath:
		logger.debug (f'searching directory {d} for workspaces')
		for root, dirs, files in os.walk (d):
			try:
				ws = Workspace.open (root)

				# check if ignored
				ignoreWorkspace = False
				for i in ignored:
					kind, pattern = map (str.strip, i.split (':', 1))
					v = getattrRecursive (ws, kind)
					logger.debug (f'matching {kind} {pattern} {v}')
					if fnmatchcase (v, pattern):
						ignoreWorkspace = True
						break

				if not ignoreWorkspace:
					formatResult (args, ws.toDict (), f'{ws.directory}: {ws.metadata.get("name", "")}')
			except InvalidWorkspace:
				pass

			if not args.all:
				# do not search dotfiles
				dotfiles = list (filter (lambda x: x.startswith ('.'), dirs))
				for df in dotfiles:
					logger.debug (f'removing {df} from search tree')
					dirs.remove (df)

def doshare (args):
	""" Share a workspace with a (user) group """

	ws = Workspace.open (args.directory or os.getcwd ())
	# realpath for comparison
	homeDir = os.path.realpath (os.path.expanduser ('~')).split (os.path.sep)
	wsDir = os.path.realpath (ws.directory).split (os.path.sep)
	if not args.force and isPrefix (homeDir, wsDir):
		logger.error ('Cannot share projects in your home directory. Move them to a public space.')
		return 2

	if args.write:
		bits = 'rwX'
		# this tool cannot handle files created by other users, because only
		# the owner can setfacl them
		logger.warning ('You should only enable write mode if you know what you are doing.')
	else:
		bits = 'rX'

	for target, g in args.target:
		# change all current files’s permissions
		setPermissions (target, g, bits, ws.directory, recursive=True,
				remove=args.remove)

		# grant default permission to group, so all new files inherit these rights
		setPermissions (target, g, bits, ws.directory, default=True,
				recursive=True, remove=args.remove)

		# also grant permissions to parent directory (if possible). Cannot
		# safely remove permissions though.
		if not args.remove:
			# exclude ws directory, whose permissions we set above already.
			for p in prefixes (wsDir[:-1]):
				p = os.path.join ('/', *p)
				if not p:
					continue
				# only grant read/search permissions
				assert os.path.isdir (p), f'{p} is not a directory'
				try:
					setPermissions (target, g, 'rX', p, recursive=False)
				except Exception as e:
					logger.debug (f'Cannot set permissions on parent directory {p}')
		else:
			logger.info (f'Parent directory permissions will not be revoked automatically.')

	formatWorkspace (args, ws)

	return 0

def copydir (source: Path, dest: Path):
	""" Recursively copy directory """
	source = str (source)
	dest = str (dest)
	# until shutil.copytree does not suck any more
	if not source.endswith ('/'):
		source += '/'
	if not dest.endswith ('/'):
		dest += '/'
	cmd = ['rsync',
			'--recursive',
			'--links', # preserve symlinks
			'--group', # preserve group
			'--executability', # preserve execute bit
			# --sparse and --preallocate would be benefitial, but do not work on NFS
			'--times', # preserve mtime
			source, dest]
	# do not fail, if some files cannot be copied (23)
	run (cmd, permittedExitCodes=[0, 23])

def docopy (args):
	try:
		source = Workspace.open (args.directory)
	except WorkspaceException:
		logger.error (f'{source} is not a valid workspace')
		return 1

	meta = dict (source.metadata)
	# pick a new ID
	meta.update (dict (_id=Workspace.randomId ()))
	destination = Workspace.create (args.dest, meta)

	try:
		copydir (source.directory, destination.directory)
		destination.writeMetadata ()

		formatWorkspace (args, destination)
		return 0
	except Exception as e:
		logger.error (f'copying workspace failed: {e}')
		#shutil.rmtree (args.dest)

	return 1

def domodify (args):
	try:
		ws = Workspace.open (args.directory)
	except WorkspaceException:
		logger.error (f'{source} is not a valid workspace')
		return 1

	logger.debug (f'updating metadata with {args.metadata}')
	ws.metadata.update (args.metadata)
	# remove empty values
	remove = list (filter (lambda kv: not kv[1], args.metadata))
	logger.debug (f'removing empty keys {remove}')
	for k, v in remove:
		ws.metadata.pop (k)
	ws.writeMetadata ()

	formatWorkspace (args, ws)

	return 0

def doignore (args):
	"""
	Add workspace to locally ignored workspaces
	"""

	try:
		ws = Workspace.open (args.directory)
	except WorkspaceException:
		logger.error (f'{source} is not a valid workspace')
		return 1

	ignored = []
	if os.path.exists (args.ignore):
		with open (args.ignore) as fd:
			ignored = yaml.safe_load (fd)
			if not isinstance (ignored, list):
				ignored = []
	ignored = set (ignored)
	ignored.add (f'metadata._id:{ws.metadata["_id"]}')
	os.makedirs (os.path.dirname (args.ignore), exist_ok=True)
	with open (args.ignore, 'w') as fd:
		yaml.dump (list (ignored), fd)

	return 0

def doexport (args):
	try:
		ws = Workspace.open (args.directory)
	except WorkspaceException:
		logger.error (f'{args.directory} is not a valid workspace')
		return 1

	if args.output.exists () and not args.output.is_dir ():
		logger.error (f'Output file {args.output} exists.')
		return 1

	# resolve before chdir’ing
	output = args.output.resolve ()
	if args.output.is_dir ():
		# choose a name ourselves
		base = output
		fileExt = {'zip': 'zip', 'tar+lzip': 'tar.lz'}[args.kind]
		ext = ''
		while True:
			output = base / f'{ws.nameToDir (ws.metadata.get ("name", ""))}{ext}.{fileExt}'
			if not output.exists ():
				break
			ext = f'_{secrets.randbelow (2**64)}'

	excludePattern = [
			'.config/guix/current*',
			'.guix-profile*',
			'.cache/**',
			'.rstudio/sessions/**',
			'.JASP/temp/**', # JASP should be fixed to use .cache or /tmp
			]
	# use temp directory on the same mount, so we can easily do a rename
	# instead of copying
	tempDir = output.parent
	if args.kind == 'zip':
		with tempfile.TemporaryDirectory (dir=tempDir) as tempDir:
			tempArchive = Path (tempDir) / 'output.zip'
			logger.debug (f'using temporary file {tempArchive}')
			os.chdir (ws.directory)

			cmd = [ZIP_PROGRAM]
			for p in excludePattern:
				cmd.extend (['-x', p])
			if not args.verbose:
				cmd.append ('--quiet')
			cmd.extend ([
					'-y', # do not follow symlinks
					'-r', # recursive
					tempArchive, # output
					'.', # input
					])

			run (cmd)
			os.rename (tempArchive, output)
			formatResult (args, dict (path=output), output)
			return 0
	elif args.kind == 'tar+lzip':
		with tempfile.TemporaryDirectory (dir=tempDir) as tempDir:
			tempArchive = Path (tempDir) / 'output.tar.lz'
			# tarballs include the directory name by convention, so chdir to
			# the parent and use .name as input
			os.chdir (ws.directory.parent)

			cmd = [TAR_PROGRAM,
					f'--use-compress-program={LZIP_PROGRAM}',
					# reset owner and group info
					'--owner=joeuser:1000',
					'--group=joeuser:1000',
					'--no-acls', # no acls
					'-c', # create
					'-f', tempArchive, # output
					]
			base = ws.directory.name
			for p in excludePattern:
				cmd.append(f'--exclude={base}/{p}')
			if args.verbose:
				cmd.append ('--verbose')
			cmd.append (base) # input

			run (cmd)
			os.rename (tempArchive, output)
			formatResult (args, dict (path=output), output)
			return 0
	else:
		raise NotImplementedError ()

def doimport (args):
	# if args.dest is nonexistent it’ll be picked as workspace directory below,
	# so we have to resort to a parent directory for temporary data
	tempDir = args.dest
	while not tempDir.exists ():
		tempDir = tempDir.parent

	# detect filetype
	mime = magic.Magic (mime=True)
	t = mime.from_file (str (args.input))
	
	with tempfile.TemporaryDirectory (dir=tempDir) as tempDir:
		tempDir = Path (tempDir)
		logger.debug (f'using temp scratch directory {tempDir}')

		# create another subdirectory, because tempDir will be removed unconditionally
		unpackDir = tempDir / 'unpack'
		unpackDir.mkdir ()

		if t == 'application/zip':
			cmd = [UNZIP_PROGRAM,
					'-d', unpackDir, # change to tempdir first
					]
			if not args.verbose:
				cmd.append ('-q')
			cmd.append (args.input)
		elif t == 'application/x-lzip':
			cmd = [TAR_PROGRAM,
					f'--use-compress-program={LZIP_PROGRAM}',
					'-C', unpackDir, # change to tempdir first
					'-x', # extract
					'-f', args.input, # input
					]
			if args.verbose:
				cmd.append ('-v')
		else:
			logger.error (f'The file format {t} cannot be imported currently')
			return 1

		run (cmd)
		ws = None
		try:
			ws = Workspace.open (unpackDir)
		except InvalidWorkspace:
			# try one of the subdirectories
			for x in unpackDir.iterdir ():
				if x.is_dir():
					try:
						ws = Workspace.open (x)
						break
					except InvalidWorkspace:
						pass
		if not ws:
			logger.error (f'Cannot find valid workspace in {args.input}')
			return 1

		initWorkspace (ws, verbose=args.verbose)
		dest = ws.nameToPath (ws.metadata.get ('name', ''), args.dest)
		os.rename (ws.directory, dest)
		ws = Workspace.open (dest)
		# imported projects are considered copies, so we assign a new, separate id
		ws.metadata['_id'] = Workspace.randomId ()
		ws.writeMetadata ()
		formatWorkspace (args, ws)

def doPackageListInstalled (args):
	try:
		ws = Workspace.open (args.directory)
	except WorkspaceException:
		logger.error (f'{args.directory} is not a valid workspace')
		return 1

	# .packages attribute requires guix
	ws.ensureGuix ()

	for p in ws.packages:
		formatResult (args, p.toDict (), f'{p.name} ({p.version})')

	return 0

def doPackageSearch (args):
	try:
		ws = Workspace.open (args.directory)
	except WorkspaceException:
		logger.error (f'{args.directory} is not a valid workspace')
		return 1

	ws.ensureGuix ()

	cmd = [str (ws.guixbin), "search", args.expression]
	ret = run (cmd, stdout=subprocess.PIPE)
	for r in limit (parseRecfile (StringIO (ret.stdout.decode ('utf-8'))), args.limit):
		for k in ('dependencies', 'systems'):
			if k in r:
				r[k] = r[k].split (' ')
		formatResult (args, r, f'{r["name"]} ({r["version"]})\n  {r.get ("synopsis", "")}\n')

def doPackageModify (args):
	try:
		ws = Workspace.open (args.directory)
	except WorkspaceException:
		logger.error (f'{args.directory} is not a valid workspace')
		return 1

	with open (ws.manifestpath) as fd:
		manifest = fd.read ()

	try:
		newManifest = modifyManifest (manifest, args.packages)
	except ValueError:
		logging.error ('Cannot modify manifest.')
		return 2

	logging.debug (f'new manifest is:\n{newManifest}')
	newManifestPath = ws.manifestpath.with_suffix ('.new')
	with open (newManifestPath, 'w') as fd:
		fd.write (newManifest)
	os.rename (newManifestPath, ws.manifestpath)

	try:
		ws.ensureProfile ()
	except ExecutionFailed:
		# revert
		logger.error ('New manifest is not valid, reverting changes.')
		with open (newManifestPath, 'w') as fd:
			fd.write (manifest)
		os.rename (newManifestPath, ws.manifestpath)
		ws.ensureProfile ()
		raise

	formatWorkspace (args, ws)

def doPackageUpgrade (args):
	try:
		ws = Workspace.open (args.directory)
	except WorkspaceException:
		logger.error (f'{args.directory} is not a valid workspace')
		return 1

	with open (ws.channelpath, 'r') as fd:
		channel = fd.read ()

	# We can simply upgrade all packages by removing the commit hashes from our
	# channel file.
	newChannel = re.sub (r'\(commit\s+"[a-f0-9]+"\s*\)', '', channel)

	newChannelPath = ws.channelpath.with_suffix ('.new')
	with open (newChannelPath, 'w') as fd:
		fd.write (newChannel)
	os.rename (newChannelPath, ws.channelpath)

	try:
		ws.ensureProfile ()
	except ExecutionFailed:
		# revert
		logger.error ('Upgrade failed, reverting changes.')
		with open (newChannelPath, 'w') as fd:
			fd.write (channel)
		os.rename (newChannelPath, ws.channelpath)
		raise

	formatWorkspace (args, ws)

def dohelp (parser, args):
	parser.print_usage ()
	return 1

def parseKV (s):
	k, v = s.split ('=', 1)
	return (k.strip (), v.strip ())

def parseTarget (s):
	try:
		k, v = s.split (':', 1)
		k = PermissionTarget(k)
	except:
		k = PermissionTarget(s)
		assert k == PermissionTarget.OTHER
		v = None
	return k, v

def main ():
	cwd = Path.cwd ()

	parser = argparse.ArgumentParser(description='Manage guix workspaces.')
	parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
	parser.add_argument('-c', '--config', action='append',
			default=['/etc/' + __package__ + '/config.yaml', # system default
					os.path.expanduser ('~/.config/' + __package__ + '/config.yaml'), # user default
					],
			help='Configuration file')
	parser.add_argument('-f', '--format', default=Formatter.HUMAN,
			type=lambda x: Formatter[x.upper ()], help='Output format')
	parser.add_argument('-d', '--directory', type=Path, default=cwd, help='Workspace directory')
	parser.set_defaults (func=partial (dohelp, parser))
	subparsers = parser.add_subparsers ()

	parserCreate = subparsers.add_parser('create', help='Create a new workspace')
	parserCreate.add_argument('name', nargs='+', help='Workspace name')
	parserCreate.set_defaults(func=docreate)

	parserRun = subparsers.add_parser('run', help='Run a program inside the workspace')
	parserRun.add_argument('--user', help='conductor SSH user')
	parserRun.add_argument('--conductorServer', dest='conductorServer', help='conductor server')
	parserRun.add_argument('--dry-run', dest='dryRun', action='store_true', help='Only print action')
	parserRun.add_argument('application', nargs='?', help='Application name, omit to list available applications')
	parserRun.set_defaults(func=dorun)

	parserList = subparsers.add_parser('list', help='List all available workspaces')
	parserList.add_argument('-s', '--search-path', dest='searchPath',
			default=[], action='append', help='User')
	parserList.add_argument('-a', '--all', action='store_true', help='Search hidden directories')
	parserList.add_argument('-i', '--ignore', action='append',
			default=['/etc/' + __package__ + '/ignore.yaml', # system default
					os.path.expanduser ('~/.config/' + __package__ + '/ignore.yaml'), # user default
					],
			help='File with ignored projects')
	parserList.set_defaults(func=dolist)

	parserShare = subparsers.add_parser('share', help='Share workspace with other users')
	parserShare.add_argument('-x', '--remove', action='store_true', help='Unshare')
	parserShare.add_argument('-w', '--write', action='store_true', help='Grant write permissions as well')
	parserShare.add_argument('-f', '--force', action='store_true', help='Override security checks')
	parserShare.add_argument('target', nargs='+', type=parseTarget, help='u:username, g:groupname or o (others)')
	parserShare.set_defaults(func=doshare)

	parserCopy = subparsers.add_parser('copy', help='Copy workspace')
	parserCopy.add_argument('dest', nargs='?', default=cwd, type=Path, help='Destination directory')
	parserCopy.set_defaults(func=docopy)

	parserModify = subparsers.add_parser('modify', help='Change workspace metadata')
	parserModify.add_argument('metadata', nargs='+', type=parseKV, help='Key-value pairs')
	parserModify.set_defaults(func=domodify)

	parserIgnore = subparsers.add_parser('ignore', help='Ignore workspace')
	parserIgnore.add_argument('-i', '--ignore',
			default=os.path.expanduser ('~/.config/' + __package__ + '/ignore.yaml'), # user default
			help='File with ignored projects')
	parserIgnore.set_defaults(func=doignore)

	parserExport = subparsers.add_parser('export', help='Export workspace files or metadata')
	parserExport.add_argument ('kind', choices=('zip', 'tar+lzip'), help='Export format')
	parserExport.add_argument ('output', type=Path, help='Output file')
	parserExport.set_defaults(func=doexport)

	parserImport = subparsers.add_parser('import', help='Import workspace from archive')
	parserImport.add_argument ('input', type=Path, help='Input file')
	parserImport.add_argument ('dest', type=Path, default=cwd, help='Destination directory')
	parserImport.set_defaults(func=doimport)

	parserPackage = subparsers.add_parser('package', help='Package operations')
	subparsers = parserPackage.add_subparsers ()

	parserInstalled = subparsers.add_parser('installed', help='List installed packages')
	parserInstalled.set_defaults(func=doPackageListInstalled)

	parserSearch = subparsers.add_parser('search', help='Search available packages')
	parserSearch.add_argument('--limit', type=int, default=10, help='Limit number of search results')
	parserSearch.add_argument('expression', nargs='?', help='Search expression')
	parserSearch.set_defaults(func=doPackageSearch)

	parserModify = subparsers.add_parser('modify', help='Modify installed packages')
	parserModify.add_argument('packages', nargs=argparse.REMAINDER,
			help='Package specification, prefixed by + or - to add/remove it')
	parserModify.set_defaults(func=doPackageModify)

	parserUpgrade = subparsers.add_parser('upgrade', help='Upgrade installed packages')
	parserUpgrade.set_defaults(func=doPackageUpgrade)

	args = parser.parse_args()
	logformat = '{message}'
	if args.verbose:
		logging.basicConfig (level=logging.DEBUG, format=logformat, style='{')
	else:
		logging.basicConfig (level=logging.INFO, format=logformat, style='{')

	# read config and merge with args
	config = dict ()
	for f in args.config:
		try:
			with open (f) as fd:
				config.update (yaml.safe_load (fd))
		except FileNotFoundError:
			pass
	# XXX: how can we have a default here and still fall back to config if no
	# argument was given?
	if 'searchPath' in args:
		args.searchPath.extend (config.get ('searchPath', []))
	if 'conductorServer' in args and not args.conductorServer:
		args.conductorServer = config.get ('conductorServer')

	try:
		return args.func (args)
	except ExecutionFailed as e:
		ret = e.args[2]
		formatResult (args, dict (status='exec_error',
				command=e.args[0],
				returncode=ret.returncode,
				stdout=ret.stdout,
				stderr=ret.stderr), None)
		return 3
	except Busy:
		logger.error ('Workspace is currently busy. Try again.')
		formatResult (args, dict (status='busy'), None)
		return 4

