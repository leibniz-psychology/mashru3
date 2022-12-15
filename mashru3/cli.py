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

import argparse, re, os, subprocess, logging, shutil, sys, shlex, \
		json, secrets, tempfile, traceback, signal, itertools
from enum import Enum, auto, Flag
from pathlib import Path
from datetime import datetime
from functools import partial
from collections import defaultdict
from hashlib import blake2b
from base64 import b32encode
from fnmatch import fnmatchcase
from io import StringIO
from functools import wraps

import yaml
import magic

from .krb5 import defaultRealm
from .util import getattrRecursive, prefixes, isPrefix, parseRecfile, limit, run, ExecutionFailed, now
from .filesystem import Busy, softlock, setPermissions, PermissionTarget
from .manifest import modifyManifest
from .workspace import (Workspace, WorkspaceException, InvalidWorkspace,
		WorkspacePackageBuildFailure, WorkspaceBroken)
from .config import *

logger = logging.getLogger ('cli')

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

def withWorkspace (f):
	""" Decorator which opens the source workspace from args """
	@wraps (f)
	def wrapper (args, *largs, **kwargs):
		try:
			with Workspace.open (args.directory) as ws:
				return f (args, ws, *largs, **kwargs)
		except InvalidWorkspace as e:
			logger.error (f'{args.directory.resolve()} is not a valid workspace: {e.args[0]}')
	return wrapper

def doCreate (args):
	name = ' '.join (args.name)
	directory = Workspace.nameToPath (name, args.directory)
	logger.info (f'Creating workspace {name} at {directory}')

	skeldirs = [Path.home() / '.config' / __package__ / 'skel',
			Path ('/etc/' + __package__ + '/skel')]
	for d in skeldirs:
		if d.is_dir ():
			try:
				with Workspace.open (d) as source:
					logger.debug (f'Copying skeleton at {d} to {directory}')
					with source.copy (directory) as destination:
						# start with a clean state, not officially a copy.
						destination.resetMetadata ()
						destination.metadata['name'] = name
						formatWorkspace (args, destination)
						return 0
			except InvalidWorkspace as e:
				# just try the next one
				logger.warning (f'Skeleton directory {d} is invalid: {e.args[0]}')

	logger.debug (f'No skeleton directory found, creating empty workspace.')
	with Workspace.create (directory) as destination:
		destination.metadata['name'] = name
		formatWorkspace (args, destination)

	return 0

@withWorkspace
def doRun (args, ws):
	""" Run program inside workspace """

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

def doList (args):
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
				with Workspace.open (root) as ws:
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

					# All subdirectories belong to this workspace, no nested workspaces.
					dirs.clear ()
			except InvalidWorkspace:
				pass

			if not args.all:
				# do not search dotfiles
				dotfiles = list (filter (lambda x: x.startswith ('.'), dirs))
				for df in dotfiles:
					logger.debug (f'removing {df} from search tree')
					dirs.remove (df)

@withWorkspace
def doShare (args, ws):
	""" Share a workspace with a (user) group """

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

@withWorkspace
def doCopy (args, source):
	directory = Workspace.nameToPath (source.metadata.get ('name', ''), args.dest)
	logger.info (f'Copying workspace {source.directory} to {directory}')
	with source.copy (directory) as destination:
		# pick a new ID
		destination.metadata['_id'] = Workspace.randomId ()

		backupDir = destination.directory / '.backup'
		if backupDir.exists ():
			extraEnv = {'BORG_RELOCATED_REPO_ACCESS_IS_OK': 'no',
					'BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK': 'yes'}
			env = os.environ.copy ()
			env.update (extraEnv)
			run ([BORG_PROGRAM, 'config', backupDir, 'id', secrets.token_hex (32)], env=env)
		formatWorkspace (args, destination)
		return 0

	return 1

@withWorkspace
def doModify (args, ws):
	logger.debug (f'updating metadata with {args.metadata}')
	ws.metadata.update (args.metadata)
	# remove empty values
	remove = list (filter (lambda kv: not kv[1], args.metadata))
	logger.debug (f'removing empty keys {remove}')
	for k, v in remove:
		ws.metadata.pop (k)

	formatWorkspace (args, ws)

	return 0

@withWorkspace
def doIgnore (args, ws):
	"""
	Add workspace to locally ignored workspaces
	"""

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

@withWorkspace
def doExport (args, ws):
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
			tempDir = Path (tempDir)
			tempArchive = tempDir / 'output.zip'
			logger.debug (f'using temporary file {tempArchive}')

			# Create and archive lockfile.
			try:
				os.chdir (tempDir)
				with open ('renv.lock', 'w') as fd:
					json.dump (ws.renvLockfile (), fd)
				cmd = [ZIP_PROGRAM,
						tempArchive, # output
						'renv.lock']
				run (cmd)
			except Exception as e:
				# Ignore any errors, so we can at least export data.
				logger.warning (f'Cannot write renv.lock file: {e}')

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
			tempDir = Path (tempDir)
			tempArchive = tempDir / 'output.tar.lz'
			base = ws.directory.name
			haveRenv = False

			# Create lockfile. Will be archived later.
			try:
				os.mkdir (tempDir / base)
				os.chdir (tempDir / base)
				with open ('renv.lock', 'w') as fd:
					json.dump (ws.renvLockfile (), fd)
				haveRenv = True
			except Exception as e:
				# Ignore any errors, so we can at least export data.
				logger.warning (f'Cannot write renv.lock file: {e}')

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
			for p in excludePattern:
				cmd.append(f'--exclude={base}/{p}')
			if args.verbose:
				cmd.append ('--verbose')
			cmd.append (base) # input
			if haveRenv:
				cmd.extend (['-C', tempDir, os.path.join (base, 'renv.lock')])

			run (cmd)
			os.rename (tempArchive, output)
			formatResult (args, dict (path=output), output)
			return 0
	else:
		raise NotImplementedError ()

def doImport (args):
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
		possibleRoots = filter (lambda x: x.is_dir (), itertools.chain ([unpackDir], unpackDir.iterdir ()))
		found = False
		for r in possibleRoots:
			try:
				logger.debug (f'trying to open {r} as workspace')
				with Workspace.open (r) as ws:
					dest = ws.nameToPath (ws.metadata.get ('name', ''), args.dest)
					ws.move (dest)
					# Re-create state from imported profile
					ws.ensureProfile ()
					found = True
					break
			except InvalidWorkspace:
				pass
		if not found:
			logger.error (f'Cannot find valid workspace in {args.input}')
			return 1

		with Workspace.open (dest) as ws:
			# imported projects are considered copies, so we assign a new, separate id
			ws.metadata['_id'] = Workspace.randomId ()
			formatWorkspace (args, ws)

@withWorkspace
def doPackageListInstalled (args, ws):
	# .packages attribute requires guix
	ws.ensureGuix ()

	for p in ws.packages:
		formatResult (args, p.toDict (), f'{p.name} ({p.version})')

	return 0

@withWorkspace
def doPackageSearch (args, ws):
	ws.ensureGuix ()

	with ws.chdir ():
		cmd = [str (ws.relGuixBin), "search"] + args.expression
		ret = run (cmd, stdout=subprocess.PIPE)
		for r in limit (parseRecfile (StringIO (ret.stdout.decode ('utf-8'))), args.limit):
			for k in ('dependencies', 'systems', 'outputs'):
				if k in r:
					if r[k]:
						r[k] = r[k].replace ('\n', ' ').split (' ')
					else:
						del r[k]
			for k in ('license', ):
				if k in r:
					if r[k]:
						r[k] = r[k].split (', ')
					else:
						del r[k]
			for k in ('relevance', ):
				if k in r:
					r[k] = int (r[k])
			formatResult (args, r, f'{r["name"]} ({r["version"]})\n  {r.get ("synopsis", "")}\n')

@withWorkspace
def doPackageModify (args, ws):
	with ws.chdir ():
		with open (ws.relManifestPath) as fd:
			manifest = fd.read ()

		try:
			newManifest = modifyManifest (manifest, args.packages)
		except ValueError:
			logging.error ('Cannot modify manifest.')
			return 2

		logging.debug (f'new manifest is:\n{newManifest}')
		newManifestPath = ws.relManifestPath.with_suffix ('.new')
		with open (newManifestPath, 'w') as fd:
			fd.write (newManifest)
		os.rename (newManifestPath, ws.relManifestPath)

		try:
			ws.ensureProfile ()
		except Exception:
			# revert
			logger.error ('New manifest is not valid, reverting changes.')
			with open (newManifestPath, 'w') as fd:
				fd.write (manifest)
			os.rename (newManifestPath, ws.relManifestPath)
			ws.ensureProfile ()
			raise

		formatWorkspace (args, ws)

@withWorkspace
def doPackageUpgrade (args, ws):
	with ws.chdir ():
		with open (ws.relChannelsPath, 'r') as fd:
			channel = fd.read ()

		# We can simply upgrade all packages by removing the commit hashes from our
		# channel file.
		newChannel = re.sub (r'\(commit\s+"[a-f0-9]+"\s*\)', '', channel)

		newChannelPath = ws.relChannelsPath.with_suffix ('.new')
		with open (newChannelPath, 'w') as fd:
			fd.write (newChannel)
		os.rename (newChannelPath, ws.relChannelsPath)

		try:
			ws.ensureProfile ()
		except Exception:
			# revert
			logger.error ('Upgrade failed, reverting changes.')
			with open (newChannelPath, 'w') as fd:
				fd.write (channel)
			os.rename (newChannelPath, ws.relChannelsPath)
			raise

		formatWorkspace (args, ws)

def doHelp (parser, args):
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
	parser.set_defaults (func=partial (doHelp, parser))
	subparsers = parser.add_subparsers ()

	parserCreate = subparsers.add_parser('create', help='Create a new workspace')
	parserCreate.add_argument('name', nargs='*', help='Workspace name')
	parserCreate.set_defaults(func=doCreate)

	parserRun = subparsers.add_parser('run', help='Run a program inside the workspace')
	parserRun.add_argument('--user', help='conductor SSH user')
	parserRun.add_argument('--conductorServer', dest='conductorServer', help='conductor server')
	parserRun.add_argument('--dry-run', dest='dryRun', action='store_true', help='Only print action')
	parserRun.add_argument('application', nargs='?', help='Application name, omit to list available applications')
	parserRun.set_defaults(func=doRun)

	parserList = subparsers.add_parser('list', help='List all available workspaces')
	parserList.add_argument('-s', '--search-path', dest='searchPath',
			default=[], action='append', help='User')
	parserList.add_argument('-a', '--all', action='store_true', help='Search hidden directories')
	parserList.add_argument('-i', '--ignore', action='append',
			default=['/etc/' + __package__ + '/ignore.yaml', # system default
					os.path.expanduser ('~/.config/' + __package__ + '/ignore.yaml'), # user default
					],
			help='File with ignored projects')
	parserList.set_defaults(func=doList)

	parserShare = subparsers.add_parser('share', help='Share workspace with other users')
	parserShare.add_argument('-x', '--remove', action='store_true', help='Unshare')
	parserShare.add_argument('-w', '--write', action='store_true', help='Grant write permissions as well')
	parserShare.add_argument('-f', '--force', action='store_true', help='Override security checks')
	parserShare.add_argument('target', nargs='+', type=parseTarget, help='u:username, g:groupname or o (others)')
	parserShare.set_defaults(func=doShare)

	parserCopy = subparsers.add_parser('copy', help='Copy workspace')
	parserCopy.add_argument('dest', nargs='?', default=cwd, type=Path, help='Destination directory')
	parserCopy.set_defaults(func=doCopy)

	parserModify = subparsers.add_parser('modify', help='Change workspace metadata')
	parserModify.add_argument('metadata', nargs='+', type=parseKV, help='Key-value pairs')
	parserModify.set_defaults(func=doModify)

	parserIgnore = subparsers.add_parser('ignore', help='Ignore workspace')
	parserIgnore.add_argument('-i', '--ignore',
			default=os.path.expanduser ('~/.config/' + __package__ + '/ignore.yaml'), # user default
			help='File with ignored projects')
	parserIgnore.set_defaults(func=doIgnore)

	parserExport = subparsers.add_parser('export', help='Export workspace files or metadata')
	parserExport.add_argument ('kind', choices=('zip', 'tar+lzip'), help='Export format')
	parserExport.add_argument ('output', type=Path, help='Output file')
	parserExport.set_defaults(func=doExport)

	parserImport = subparsers.add_parser('import', help='Import workspace from archive')
	parserImport.add_argument ('input', type=Path, help='Input file')
	parserImport.add_argument ('dest', type=Path, default=cwd, help='Destination directory')
	parserImport.set_defaults(func=doImport)

	parserPackage = subparsers.add_parser('package', help='Package operations')
	subparsers = parserPackage.add_subparsers ()

	parserInstalled = subparsers.add_parser('installed', help='List installed packages')
	parserInstalled.set_defaults(func=doPackageListInstalled)

	parserSearch = subparsers.add_parser('search', help='Search available packages')
	parserSearch.add_argument('--limit', type=int, default=10, help='Limit number of search results')
	parserSearch.add_argument('expression', nargs='+', help='Search expressions')
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
	except WorkspacePackageBuildFailure as e:
		ret = e.args[0]
		formatResult (args, dict (status='package_build_error',
				packages=ret))
		return 5
	except WorkspaceBroken as e:
		formatResult (args, dict (status='workspace_broken'))
		return 5

