import argparse, re, os, subprocess, logging, shutil, sys, shlex, configparser
from enum import Enum, auto
from pathlib import Path
from getpass import getuser
import yaml

from unidecode import unidecode

logger = logging.getLogger ('cli')

class Formatter (Enum):
	HUMAN = auto ()
	YAML = auto ()

class WorkspaceException (Exception):
	pass

class InvalidWorkspace (WorkspaceException):
	pass

class Workspace:
	def __init__ (self, d, meta=None):
		defaultMeta = dict (version=1)
		if meta:
			defaultMeta.update (meta)
		self.metadata = defaultMeta
		self.directory = d

	def toDict (self):
		d = dict (path=self.directory, metadata=self.metadata)
		return d

	def writeMetadata (self):
		with open (self.metapath, 'w') as fd:
			yaml.dump (self.metadata, fd)

	@property
	def configdir (self):
		return os.path.join (self.directory, '.config')

	@property
	def guixdir (self):
		return os.path.join (self.configdir, 'guix')

	@property
	def guixbin (self):
		return os.path.join (self.guixdir, 'current', 'bin', 'guix')

	@property
	def metapath (self):
		""" Path for metadata file """
		return os.path.join (self.configdir, 'workspace.yaml')

	@property
	def manifestpath (self):
		return os.path.join (self.guixdir, 'manifest.scm')

	@property
	def channelpath (self):
		return os.path.join (self.guixdir, 'channels.scm')

	@property
	def profilepath (self):
		return os.path.join (self.directory, '.guix-profile')

	@property
	def applications (self):
		# dummy application to start a shell
		yield dict (Name='Shell', Exec=None)

		searchdirs = [(self.directory, '.local', 'share'),
				(self.profilepath, 'share'),
				(self.guixdir, 'current', 'share')]
		for datadir in map (lambda x: os.path.join (*x, 'applications'), searchdirs):
			for root, dirs, files in os.walk (datadir):
				for f in filter (lambda x: x.endswith ('.desktop'), files):
					config = configparser.ConfigParser ()
					config.read (os.path.join (root, f))
					entry = config['Desktop Entry']
					# not checking tryexec here, because that would require
					# running guix environment
					if entry.get ('Type') == 'Application':
						yield entry

	@property
	def envcmd (self):
		""" Command that starts a guix environment """
		user = 'joeuser'
		cmd = [self.guixbin,
				'environment', '-C', '-N',
				'-u', user,
				'-E', '^LANG$', # allow passing the current language
				'-P',
				'--no-cwd',
				f'--share={self.directory}=/home/{user}',
				]
		if os.path.isfile (self.manifestpath):
			cmd.extend (['-m', self.manifestpath])
		else:
			# make sure basic commands like `true` exist
			cmd.extend (['--ad-hoc', 'coreutils'])
		return cmd

	@classmethod
	def open (cls, d):
		"""
		Verify directory d is a valid workspace and get its metadata
		"""
		ws = cls (d)
		checkfiles = [ws.guixbin, ws.metapath]
		if all (map (lambda x: os.path.exists (x), checkfiles)):
			with open (ws.metapath) as fd:
				ws.metadata = yaml.safe_load (fd)
				return ws
		raise InvalidWorkspace ()

	@classmethod
	def create (cls, name, basedir):
		name = ' '.join (name)
		# use lowercase, unicode-stripped name as directory. Special characters are
		# replaced by underscore, but no more than one successive underscore and
		# not at the beginning or the end.
		r = re.compile (r'[^a-z0-9]+')
		d = r.sub ('_', unidecode (name.lower ())).strip ('_')

		if not d:
			logger.error (f'The project name is empty.')
			return 1

		base = os.path.join (basedir, d)

		return cls (base, dict(name=name))

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
	return subprocess.run (cmd, stdout=stdout, stderr=stderr, check=True)

def setPermissions (group, bits, path, remove=False, default=False, recursive=False):
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
	cmd.append (path)
	logger.debug (cmd)
	run (cmd)

def initWorkspace (ws, verbose=False):
	# Fix permissions. Make sure the creator has default permissions, so files
	# created by other users are accessible by default. 
	setPermissions (getuser (), 'rwx', ws.directory, default=True, recursive=True)

	# get a fresh guix
	logger.debug (f'Getting a fresh guix')
	os.makedirs (ws.guixdir, exist_ok=True)
	cmd = ['guix', 'pull',
			'-p', os.path.join (ws.guixdir, 'current'),
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
	tmpChannelPath = ws.channelpath + '.tmp'
	with open (tmpChannelPath, 'w') as fd:
		cmd = [ws.guixbin, 'describe', '-f', 'channels']
		run (cmd, stdout=fd)
	os.rename (tmpChannelPath, ws.channelpath)

	ws.writeMetadata ()

	# create symlink ~/.guix-profile, so apps can be found
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
	else:
		assert False

def docreate (args):
	ws = Workspace.create (args.name, basedir=args.directory)

	if os.path.exists (ws.directory):
		logger.error (f'The directory {ws.directory} already exists. '
				'Try a different workspace name.')
		return 1

	logger.info (f'Creating workspace {ws.metadata["name"]}')

	try:
		skeldirs = [os.path.expanduser ('~/.config/' + __package__ + '/skel'),
				'/etc/' + __package__ + '/skel']
		for d in skeldirs:
			if os.path.isdir (d):
				logger.debug (f'Copying skeleton at {d} to {ws.directory}')
				copydir (d, ws.directory)
				break
		if not os.path.isdir (ws.directory):
			logger.debug (f'No skeleton directory found, creating empty workspace.')
			os.makedirs (ws.directory)

		initWorkspace (ws, verbose=args.verbose)

		# finally print the workspace directory, so it can be consumed by scripts
		formatWorkspace (args, ws)

		return 0
	except:
		logger.error ('Creating workspace failed.')
		shutil.rmtree (ws.directory)

	return 1

def dorun (args):
	""" Run program inside workspace """

	ws = Workspace.open (os.getcwd ())

	# find the application requested
	matches = []
	for entry in sorted (ws.applications, key=lambda x: x.get ('Name').lower ()):
		if not args.application:
			if args.format == Formatter.HUMAN:
				print (entry.get ('Name'))
			elif args.format == Formatter.YAML:
				yaml.dump (dict (entry), sys.stdout)
				sys.stdout.write ('---\n')
			else:
				assert False
		elif args.application.lower() in entry.get ('Name').lower ():
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
			logger.error (m.get ('Name'))
		return 1
	entry = matches[0]

	# `guix environment -P` does not work if the symlink already exists
	profileLink = os.path.join (ws.directory, '.guix-profile')
	if os.path.islink (profileLink):
		linkDest = os.readlink (profileLink)
		os.unlink (profileLink)
	elif os.path.exists (profileLink):
		logger.error (f'{profileLink} is not a symlink')
		return 1

	try:
		if entry:
			execcmd = entry.get ('Exec')
			key = entry.get ('X-Conductor-Key')
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
			socket = entry.get ('X-Conductor-Socket')
			# tilde-expand is relative to homedir location inside container
			if socket.startswith ('~/'):
				socket = os.path.join (ws.directory, socket[2:])
			cmd += ['conductor',
					'-k', key,
					'-r', # replace
					forest,
					socket,
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
		if not os.path.exists (profileLink):
			os.symlink (linkDest, profileLink)

	return ret

def dolist (args):
	""" List workspaces """
	for d in args.searchPath:
		logger.debug (f'searching directory {d} for workspaces')
		for root, dirs, files in os.walk (d):
			try:
				ws = Workspace.open (root)
				if args.format == Formatter.HUMAN:
					print (f'{ws.directory}: {ws.metadata["name"]}')
				elif args.format == Formatter.YAML:
					yaml.dump (ws.toDict (), sys.stdout)
					sys.stdout.write ('---\n')
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

def doshare (args):
	""" Share a workspace with a (user) group """

	ws = Workspace.open (os.getcwd ())

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

def copydir (source, dest):
	""" Recursively copy directory """
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
	if os.path.isdir (args.dest):
		# if the destination is a directory, assume we want to copy into that directory
		dest = os.path.join (args.dest, os.path.basename (args.source))
	elif os.path.exists (args.dest):
		logger.error (f'Destination {args.dest} exists')
		return 1
	else:
		dest = args.dest

	try:
		ws = Workspace.open (args.source)
	except WorkspaceException:
		logger.error (f'{args.source} is not a valid workspace')
		return 1

	try:
		copydir (args.source, args.dest)

		ws = Workspace.open (args.dest)

		if os.path.islink (ws.profilepath):
			os.unlink (ws.profilepath)
		else:
			assert False
		initWorkspace (ws, args.verbose)

		formatWorkspace (args, ws)
		return 0
	except:
		logger.error (f'copying workspace failed')
		shutil.rmtree (ws.directory)

	return 1

def main ():
	cwd = os.getcwd ()

	parser = argparse.ArgumentParser(description='Manage guix workspaces.')
	parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
	parser.add_argument('-c', '--config', action='append',
			default=['/etc/' + __package__ + '/config.yaml', # system default
					os.path.expanduser ('~/.config/' + __package__ + '/config.yaml'), # user default
					],
			help='Configuration file')
	parser.add_argument('-f', '--format', default=Formatter.HUMAN,
			type=lambda x: Formatter[x.upper ()], help='Output format')
	subparsers = parser.add_subparsers ()

	parserCreate = subparsers.add_parser('create', help='Create a new workspace')
	parserCreate.add_argument('-d', '--directory', default=cwd, help='Base directory')
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
			default=[cwd], action='append', help='User')
	parserList.add_argument('-a', '--all', action='store_true', help='Search hidden directories')
	parserList.set_defaults(func=dolist)

	parserShare = subparsers.add_parser('share', help='Share workspace with other users')
	parserShare.add_argument('-x', '--remove', action='store_true', help='Unshare')
	parserShare.add_argument('-w', '--write', action='store_true', help='Grant write permissions as well')
	parserShare.add_argument('groups', nargs='+', help='Target groups')
	parserShare.set_defaults(func=doshare)

	parserCopy = subparsers.add_parser('copy', help='Copy workspace')
	parserCopy.add_argument('source', help='Source directory')
	parserCopy.add_argument('dest', nargs='?', default=cwd, help='Destination directory')
	parserCopy.set_defaults(func=docopy)

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

