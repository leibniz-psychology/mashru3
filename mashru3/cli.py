import argparse, re, os, subprocess, logging, shutil, sys, shlex, configparser, json, secrets, stat, random
from enum import Enum, auto, Flag
from pathlib import Path
from getpass import getuser
from datetime import datetime
from functools import partial

import yaml, pytz
from unidecode import unidecode

from .uid import uintToQuint

logger = logging.getLogger ('cli')

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
        return json.JSONEncoder.default (self, obj)

def jsonDump (o, fd=None):
	return json.dump (o, fd, cls=Encoder)

class WorkspaceException (Exception):
	pass

class InvalidWorkspace (WorkspaceException):
	pass

class Workspace:
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
				metadata=self.metadata,
				permissions=dict(getPermissions (wsdir)),
				applications=list (self.applications),
				)
		return d

	def writeMetadata (self):
		with open (self.metapath, 'w') as fd:
			yaml.dump (self.metadata, fd)

	@property
	def configdir (self):
		return self.directory / '.config'

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
	def envcmd (self):
		""" Command that starts a guix environment """
		user = 'joeuser'
		cmd = [str (self.guixbin),
				'environment', '-C', '-N',
				'-u', user,
				'-E', '^LANG$', # allow passing the current language
				'-P',
				'--no-cwd',
				f'--share={self.directory}=/home/{user}',
				]
		if self.manifestpath.is_file ():
			cmd.extend (['-m', str (self.manifestpath)])
		else:
			# make sure basic commands like `true` exist
			cmd.extend (['--ad-hoc', 'coreutils'])
		return cmd

	@classmethod
	def open (cls, d: Path):
		"""
		Verify directory d is a valid workspace and get its metadata
		"""
		ws = cls (d)
		checkfiles = [ws.guixbin, ws.metapath]
		if all (map (lambda x: x.exists (), checkfiles)):
			with open (ws.metapath) as fd:
				ws.metadata = yaml.safe_load (fd)
				return ws
		raise InvalidWorkspace ()

	@classmethod
	def create (cls, suggestedDir: Path, metadataOverride):
		stamp = now ()
		metadata = dict (created=stamp, modified=stamp, creator=getuser ())
		metadata.update (metadataOverride)

		if suggestedDir.exists ():
			if suggestedDir.is_dir ():
				# if no dir is given, create one based on the name
				# use lowercase, unicode-stripped name as directory. Special characters are
				# replaced by underscore, but no more than one successive underscore and
				# not at the beginning or the end.
				r = re.compile (r'[^a-z0-9]+')
				name = metadata.get ('name', '')
				subdir = r.sub ('_', unidecode (name.lower ())).strip ('_')
				if not subdir:
					# simply generate one
					subdir = 'unnamed_project'

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

def defaultRealm ():
	# cannot use configparser (does not support brackets) or
	# krb5_get_default_realm() (no python bindings?)
	section = None
	with open ('/etc/krb5.conf') as fd:
		for l in fd:
			l = l.strip ()
			if l.startswith ('[') and l.endswith (']'):
				section = l[1:-1].strip ()
			elif '=' in l:
				k, v = l.split ('=', 1)
				k = k.strip ()
				v = v.strip ()
				if k == 'default_realm':
					return v
	raise KeyError ()

def run (cmd, stdout=None):
	verbose = logger.getEffectiveLevel () <= logging.DEBUG
	# hide the ugly details from the user
	if not stdout and not verbose:
		stdout = subprocess.DEVNULL
	stderr = None if verbose else subprocess.DEVNULL
	logger.debug (f'running {cmd}')
	return subprocess.run (cmd, stdout=stdout, stderr=stderr, check=True)

def setPermissions (group, bits, path: Path, remove=False, default=False, recursive=False):
	""" ACL abstraction that supports NFS """
	if isNfs (path):
		cmd = ['nfs4_setfacl']
		flags = 'g'
		if '@' not in group:
			group = f'{group}@{defaultRealm()}'
		if recursive:
			cmd.append ('-R')
		if remove:
			cmd.append ('-x')
			bits = f'{group}'
		else:
			cmd.append ('-a')
			bits = f'{group}:{bits.upper()}'
		if default:
			# directory- and file-inherit
			flags += 'df'
		# allow rule
		bits = f'A:{flags}:{bits}'
		cmd.append (bits)
	else:
		# do not change mask on files, so bits=rwx won’t set x on ordinary files
		cmd = ['setfacl', '-n']
		if recursive:
			cmd.append ('-R')
		if remove:
			cmd.append ('-x')
			# removing ignores bits
			bits = f'g:{group}'
		else:
			cmd.append ('-m')
			bits = f'g:{group}:{bits}'
		if default:
			bits = f'd:{bits}'
		cmd.append (bits)
	cmd.append (str (path))
	try:
		run (cmd)
	except subprocess.CalledProcessError as e:
		logger.debug (e)
		raise Exception ('cannot set permissions')

def getPermissions (path: Path):
	if isNfs (path):
		cmd = ['nfs4_getfacl', path]
		ret = run (cmd, stdout=subprocess.PIPE)
		for l in ret.stdout.decode ('ascii').split ('\n'):
			if l.startswith ('#') or not l:
				# comment, ignore
				continue
			kind, flags, ident, bits = l.split (':', 3)
			bits = ''.join (filter (lambda x: x in {'r', 'w', 'x'}, bits))
			if kind == 'A' and 'g' in flags:
				yield ident, bits
	else:
		cmd = ['getfacl', path]
		ret = run (cmd, stdout=subprocess.PIPE)
		owner = None
		for l in ret.stdout.decode ('ascii').split ('\n'):
			if not l:
				# empty, ignore
				continue
			elif l.startswith ('default:'):
				# default permissions, ignore as well
				continue
			elif l.startswith ('# owner: '):
				_, owner = l.split (':', 1)
				owner = owner.strip ()
				continue
			elif l.startswith ('#'):
				# other comments, ignore
				continue
			try:
				kind, ident, bits = l.split (':', 2)
			except ValueError:
				logger.error (f'Cannot parse line {l}')
				raise
			bits = ''.join (filter (lambda x: x != '-', bits))
			if kind == 'group' and ident:
				yield ident, bits
			elif kind == 'user':
				# use same attribute names as NFS4 for metadata change
				yield owner, bits + 'tT'

def initWorkspace (ws, verbose=False):
	# Fix permissions. Make sure the creator has default permissions, so files
	# created by other users are accessible by default. 
	setPermissions (getuser (), 'rwx', ws.directory, default=True, recursive=True)

	# get a fresh guix unless it already exists
	if not ws.guixbin.exists ():
		logger.debug (f'Getting a fresh guix')
		os.makedirs (ws.guixdir, exist_ok=True)
		cmd = ['guix', 'pull',
				'-p', str (ws.guixdir / 'current'),
				]
		# use channel file from skeleton instead of system default if it exists
		if os.path.isfile (ws.channelpath):
			cmd.extend (['-C', ws.channelpath])
		try:
			run (cmd)
		except (subprocess.CalledProcessError, KeyboardInterrupt):
			logger.error ('Failed to initialize guix')
			raise

	# pin guix version, so copying the project will use the exact same version
	tmpChannelPath = str (ws.channelpath) + '.tmp'
	with open (tmpChannelPath, 'w') as fd:
		cmd = [str (ws.guixbin), 'describe', '-f', 'channels']
		run (cmd, stdout=fd)
	os.rename (tmpChannelPath, ws.channelpath)

	ws.writeMetadata ()

	# create symlink ~/.guix-profile, so apps can be found (unless
	# pre-initialized)
	if not ws.profilepath.exists ():
		cmd = ws.envcmd
		# don’t actually enter environment
		cmd.extend (['--', 'true'])
		run (cmd)

	return True

def formatWorkspace (args, ws):
	if args.format == Formatter.HUMAN:
		print (ws.directory)
	elif args.format == Formatter.YAML:
		yaml.dump (ws.toDict (), sys.stdout)
		sys.stdout.write ('---\n')
	elif args.format == Formatter.JSON:
		jsonDump (ws.toDict (), sys.stdout)
		sys.stdout.write ('\n')
	else:
		assert False

def docreate (args):
	ws = Workspace.create (args.directory, dict (name=' '.join (args.name)))
	logger.info (f'Creating workspace {ws.metadata["name"]} at {ws.directory}')

	try:
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
	except Exception as e:
		logger.error (f'Creating workspace failed: {e}')
		#shutil.rmtree (ws.directory)

	return 1

def dorun (args):
	""" Run program inside workspace """

	ws = Workspace.open (args.directory)

	# find the application requested
	matches = []
	for entry in sorted (ws.applications, key=lambda x: x.get ('name').lower ()):
		if not args.application:
			if args.format == Formatter.HUMAN:
				print (entry.get ('name'))
			elif args.format == Formatter.YAML:
				yaml.dump (dict (entry), sys.stdout)
				sys.stdout.write ('---\n')
			elif args.format == Formatter.JSON:
				jsonDump (dict (entry), sys.stdout)
				sys.stdout.write ('\n')
			else:
				assert False
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

	# `guix environment -P` does not work if the symlink already exists
	profileLink = ws.directory / '.guix-profile'
	if profileLink.is_symlink ():
		linkDest = os.readlink (profileLink)
		profileLink.unlink ()
	elif profileLink.exists ():
		logger.error (f'{profileLink} is not a symlink')
		return 1

	try:
		if entry:
			execcmd = entry.get ('exec')
			key = entry.get ('x-conductor-key')
		else:
			execcmd = None
			key = None
		cmd = []
		if key:
			forest = args.forest
			if not forest:
				logger.error ('No remote forest set up.')
				return 1
			if args.user:
				forest = f'{args.user}@{forest}'
			socket = entry.get ('x-conductor-socket')
			# tilde-expand is relative to homedir location inside container
			if socket.startswith ('~/'):
				socket = ws.directory / socket[2:]
			cmd += ['conductor',
					'-k', key,
					'-r', # replace
					forest,
					str (socket),
					'--',
					]
			if args.verbose:
				cmd.insert (1, '-v')
		cmd += ws.envcmd
		if execcmd:
			cmd.append ('--')
			cmd.extend (shlex.split (execcmd))
		logger.debug (' '.join (cmd))

		if args.dryRun:
			print (' '.join (map (shlex.quote, cmd)))
			return 0

		try:
			p = subprocess.Popen (cmd)
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
		# restore symlink if it does not exist any more
		if not profileLink.exists ():
			profileLink.symlink_to (linkDest)

	return ret

def dolist (args):
	""" List workspaces """
	searchPath = set (map (lambda x: Path (x).resolve (), args.searchPath))
	# if no search paths were given, use the operating directory instead
	if not searchPath:
		searchPath.add (args.directory.resolve ())
	for d in searchPath:
		logger.debug (f'searching directory {d} for workspaces')
		for root, dirs, files in os.walk (d):
			try:
				ws = Workspace.open (root)
				if args.format == Formatter.HUMAN:
					print (f'{ws.directory}: {ws.metadata["name"]}')
				elif args.format == Formatter.YAML:
					yaml.dump (ws.toDict (), sys.stdout)
					sys.stdout.write ('---\n')
				elif args.format == Formatter.JSON:
					jsonDump (ws.toDict (), sys.stdout)
					sys.stdout.write ('\n')
				else:
					assert False
			except InvalidWorkspace:
				pass

			if not args.all:
				# do not search dotfiles
				dotfiles = list (filter (lambda x: x.startswith ('.'), dirs))
				for df in dotfiles:
					logger.debug (f'removing {df} from search tree')
					dirs.remove (df)

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
		bits = 'rwx'
		# this tool cannot handle files created by other users, because only
		# the owner can setfacl them
		logger.warning ('You should only enable write mode if you know what you are doing.')
	else:
		bits = 'rx'

	for g in args.groups:
		# change all current files’s permissions
		setPermissions (g, bits, ws.directory, recursive=True,
				remove=args.remove)

		# grant default permission to group, so all new files inherit these rights
		setPermissions (g, bits, ws.directory, default=True,
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
					setPermissions (g, 'rx', p, recursive=False)
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
			'--verbose',
			# --sparse and --preallocate would be benefitial, but do not work on NFS
			source, dest]
	run (cmd)

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

def dohelp (parser, args):
	parser.print_usage ()
	return 1

def parseKV (s):
	k, v = s.split ('=', 1)
	return (k.strip (), v.strip ())

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
	parserRun.add_argument('--forest', help='conductor forest path')
	parserRun.add_argument('--dry-run', dest='dryRun', action='store_true', help='Only print action')
	parserRun.add_argument('application', nargs='?', help='Application name, omit to list available applications')
	parserRun.set_defaults(func=dorun)

	parserList = subparsers.add_parser('list', help='List all available workspaces')
	parserList.add_argument('-s', '--search-path', dest='searchPath',
			default=[], action='append', help='User')
	parserList.add_argument('-a', '--all', action='store_true', help='Search hidden directories')
	parserList.set_defaults(func=dolist)

	parserShare = subparsers.add_parser('share', help='Share workspace with other users')
	parserShare.add_argument('-x', '--remove', action='store_true', help='Unshare')
	parserShare.add_argument('-w', '--write', action='store_true', help='Grant write permissions as well')
	parserShare.add_argument('-f', '--force', action='store_true', help='Override security checks')
	parserShare.add_argument('groups', nargs='+', help='Target groups')
	parserShare.set_defaults(func=doshare)

	parserCopy = subparsers.add_parser('copy', help='Copy workspace')
	parserCopy.add_argument('dest', nargs='?', default=cwd, type=Path, help='Destination directory')
	parserCopy.set_defaults(func=docopy)

	parserModify = subparsers.add_parser('modify', help='Change workspace metadata')
	parserModify.add_argument('metadata', nargs='+', type=parseKV, help='Key-value pairs')
	parserModify.set_defaults(func=domodify)

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
	if 'forest' in args and not args.forest:
		args.forest = config.get ('forest')

	return args.func (args)

