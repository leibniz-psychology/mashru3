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

import secrets, os, configparser, subprocess, time, logging, re, contextlib, grp, pwd
from pathlib import Path
from getpass import getuser
from collections import UserDict
from contextlib import contextmanager

import yaml, importlib_resources
from unidecode import unidecode

from .uid import uintToQuint
from .filesystem import getPermissions, setPermissions, PermissionTarget, softlock, copydir
from .util import run, ExecutionFailed, now
from .config import GUIX_PROGRAM

logger = logging.getLogger (__name__)

class ModificationAwareDict (UserDict):
	def __init__ (self, *args, **kwargs):
		super ().__init__ (*args, **kwargs)
		self.modified = False

	def __setitem__ (self, k, v):
		self.modified = True
		return super().__setitem__ (k, v)

	def __delitem__ (self, k):
		self.modified = True
		return super().__delitem__ (k)

	def update (self, *pargs, **kwargs):
		self.modified = True
		return super().update (*pargs, **kwargs)

	def pop (self, *pargs, **kwargs):
		self.modified = True
		return super().pop (*pargs, **kwargs)

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

class WorkspacePackageBuildFailure (WorkspaceException):
	""" Package failed to build """
	pass

class WorkspaceBroken (WorkspaceException):
	""" manifest.scm is broken """
	pass

def storePathToPackageName (p):
	""" Extract the package name from a Guix store path """
	m = re.match (r'/gnu/store/[a-z0-9]+-([a-z0-9-]+)-[0-9-.]+(\.drv)?', p)
	return m.group (1)

class Workspace:
	# packages that are essential to mashru3 and must always be installed
	extraPackages = {'tini'}
	relConfigDir = Path ('.config')
	relCacheDir = Path ('.cache')
	relProfilePath = Path ('.guix-profile')
	relGuixDir = relConfigDir / 'guix'
	relGuixBin = relGuixDir / 'current' / 'bin' / 'guix'
	relChannelsPath = relGuixDir / 'channels.scm'
	relManifestPath = relGuixDir / 'manifest.scm'
	relMetaPath = relConfigDir / 'workspace.yaml'

	def __init__ (self, d, meta=None):
		self.resetMetadata ()
		if meta:
			self.metadata.update (meta)
		self.directory = Path (d).resolve ()
		self.dirfd = os.open (self.directory, flags=0)

	def __del__ (self):
		os.close (self.dirfd)

	def resetMetadata (self):
		stamp = now ()
		self.metadata = ModificationAwareDict (
				version=1,
				_id=self.randomId (),
				created=stamp,
				modified=stamp,
				creator=getuser (),
				)

	@staticmethod
	def randomId ():
		return uintToQuint (secrets.randbelow (2**64), 4)

	def toDict (self):
		wsdir = self.directory
		permissions = getPermissions (wsdir)
		users, groups = self.usersGroupsFromPermissions (permissions)
		try:
			packages = [p.toDict () for p in self.packages]
		except Exception as e:
			logging.error (f'Failed to retrieve packages: {e}')
			packages = None

		d = dict (path=str (wsdir),
				profilePath=str (self.profilepath.resolve ()),
				metadata=self.metadata.data,
				permissions=permissions,
				groups=groups,
				users=users,
				applications=list (self.applications),
				packages=packages,
				)
		return d

	@staticmethod
	def usersGroupsFromPermissions (permissions):
		groups = dict ()
		users = dict ()

		visitGroups = set (permissions.get('acl', dict()).get ('group', dict()).keys ())
		visitGroups.update (permissions.get ('group', dict ()).keys ())
		visitUsers = set (permissions.get ('acl', dict()).get ('user', dict()).keys ())
		visitUsers.update (permissions.get ('user', dict ()).keys ())
		for name in visitGroups:
			try:
				g = grp.getgrnam (name)
				groups[name] = dict (name=g.gr_name, gid=g.gr_gid, members=g.gr_mem)
				visitUsers.update (g.gr_mem)
			except KeyError:
				pass
		for name in visitUsers:
			try:
				u = pwd.getpwnam (name)
				users[name] = dict (name=u.pw_name, uid=u.pw_uid, gid=u.pw_gid,
						homedir=u.pw_dir, shell=u.pw_shell, gecos=u.pw_gecos)
			except KeyError:
				pass
		return users, groups

	def _writeMetadata (self):
		if self.metadata.modified:
			with self.chdir ():
				with softlock (self.relMetaPath.with_suffix ('.lock')):
					tmpPath = self.relMetaPath.with_suffix ('.tmp')
					with open (tmpPath, 'w') as fd:
						yaml.dump (self.metadata.data, fd)
					os.rename (tmpPath, self.relMetaPath)
				self.metadata.modified = False

	@property
	def guixdir (self):
		return self.directory / self.relGuixDir

	@property
	def metapath (self):
		""" Path for metadata file """
		return self.directory / self.relMetaPath

	@property
	def profilepath (self):
		return self.directory / self.relProfilePath

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
					config = configparser.ConfigParser (interpolation=None)
					config.read (path)
					entry = dict (config['Desktop Entry'])
					entry['_id'] = os.path.relpath (path, start=datadir).replace ('/', '-')
					# not checking tryexec here, because that would require
					# running guix environment
					if entry.get ('type') == 'Application':
						yield entry

	@property
	def packages (self):
		with self.chdir ():
			""" Get installed packages """
			if not self.relGuixBin.exists ():
				return []

			cmd = [str (self.relGuixBin), "package", "-p", self.relProfilePath, "-I"]
			ret = run (cmd, stdout=subprocess.PIPE)
			lines = ret.stdout.decode ('utf-8').split ('\n')
			for l in lines:
				try:
					# Guix tries to align columns with one or more tabs
					name, version, output, path = re.split (r'\s+', l)
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

	@contextmanager
	def chdir (self):
		""" Context-manager changing the current working directory to
		the workspace directory """
		oldWorkingDir = os.getcwd ()
		os.chdir (self.dirfd)

		yield

		try:
			os.chdir (oldWorkingDir)
		except FileNotFoundError:
			# Nothing we can do to go back.
			pass

	def ensureGuix (self):
		"""
		Ensure the guix binary matches the channel file.

		Usually calling .ensureProfile() is enough.
		"""

		with self.chdir ():
			with softlock (self.relCacheDir.joinpath (__package__ + '.ensureGuix.lock')):
				channelPath = self.relChannelsPath
				channelMtime = channelPath.stat ().st_mtime if channelPath.exists () else 0

				guixbin = self.relGuixBin
				guixbinExists = guixbin.exists ()
				profilePath = self.relGuixDir / 'current'
				profileMtime = profilePath.lstat().st_mtime if guixbinExists else 0

				# This should work most of the time™
				if not guixbinExists or channelMtime > profileMtime:
					logger.debug (f'Getting a fresh guix, exists {guixbin.exists()}, mtime {channelMtime} >? {profileMtime}')
					os.makedirs (self.relGuixDir, exist_ok=True)
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
		def decodeFailure (e):
			""" Figure out what went wrong. Guix does not have a programmatic way
			of doing so, so parse stdout. """
			stderr = e.args[2].stderr.decode ('utf-8')
			m = re.findall (r"^guix package: error: build of `(/[^ ']+\.drv)' failed$",
					stderr, re.M)
			if m:
				raise WorkspacePackageBuildFailure (
						list (map (storePathToPackageName, m))) from None
			if "guix package: error: failed to load '.config/guix/manifest.scm'" in stderr:
				# Maybe a syntax error, but not sure.
				raise WorkspaceBroken ()

			raise e

		with self.chdir ():
			with softlock (self.relCacheDir.joinpath (__package__ + '.ensureProfile.lock')):
				self.ensureGuix ()

				guixprofilePath = self.relGuixDir / 'current'
				guixprofileMtime = guixprofilePath.lstat().st_mtime

				profilePath = self.relProfilePath
				profileExists = profilePath.exists ()
				profileMtime = profilePath.lstat ().st_mtime if profileExists else 0

				manifestPath = self.relManifestPath
				manifestExists = manifestPath.exists ()
				manifestMtime = manifestPath.stat ().st_mtime if manifestExists else 0

				haveExtraPackages = set (map (lambda x: x.name,
						filter (lambda x: x.name in self.extraPackages, self.packages))) == self.extraPackages

				if not profileExists or \
						manifestMtime > profileMtime or \
						guixprofileMtime > profileMtime or \
						not haveExtraPackages:
					logger.debug (f'Refreshing profile, exists {profilePath.exists()}, '
							f'mtime {manifestMtime} >? {profileMtime}, '
							f'guixmtime {guixprofileMtime} >? {profileMtime}'
							f'haveExtraPackages {haveExtraPackages}')
					cmd = [str (self.relGuixBin), 'package',
							'-p', str (profilePath),
							'--allow-collisions',
							]
					if manifestExists:
						cmd.extend (['-m', str (manifestPath)])
					if self.extraPackages:
						cmd.append ('-i')
						cmd.extend (self.extraPackages)
					try:
						run (cmd)
						if profilePath.exists ():
							# Guix can decide there is nothing to do and will not change
							# the symlinks. Make sure we don’t run this again by setting a
							# new c/mtime.
							now = time.time ()
							try:
								os.utime (profilePath, times=(now, now), follow_symlinks=False)
							except PermissionError:
								# This can happen if we’re not the owner of	a project. Nothing we
								# can do, so ignore.
								pass
					except ExecutionFailed as e:
						decodeFailure (e)

	def ensureGcroots (self):
		""" Make sure all store references are protected from the garbage collector """
		with importlib_resources.files (__package__).joinpath ('scripts/addRoots.scm') as script:
			cmd = [GUIX_PROGRAM, 'repl', '--', script, self.directory]
			run (cmd)

	@classmethod
	@contextlib.contextmanager
	def open (cls, d: Path):
		"""
		Verify directory d is a valid workspace and get its metadata
		"""
		ws = cls (d)

		try:
			checkfiles = [ws.metapath, ]
			allExist = all (map (lambda x: x.exists (), checkfiles))
		except PermissionError:
			# .exists() call .stat(), which can fail
			raise InvalidWorkspace (f'Insufficient permissions to access {ws.metapath}')

		if allExist:
			with open (ws.metapath) as fd:
				try:
					ws.metadata.update (yaml.safe_load (fd))
					# Just read it from a file.
					ws.metadata.modified = False
				except yaml.YAMLError as e:
					raise InvalidWorkspace (f'Metadata {ws.metapath} cannot be parsed')

				try:
					yield ws
				finally:
					ws.close ()
		else:
			raise InvalidWorkspace (f'Lacks required files {checkfiles}')

	def close (self):
		self._writeMetadata ()

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
					ext = f'_{secrets.randbelow (2**16)}'
				logger.debug (f'choosing directory {directory} based on name {name}')
			else:
				raise ValueError ('Destination exists')
		else:
			# use as-as
			directory = suggestedDir

		return directory

	@classmethod
	@contextlib.contextmanager
	def create (cls, directory: Path):
		ws = cls (directory)
		os.makedirs (ws.directory)
		ws.ensurePermissions ()
		ws.ensureProfile ()
		# ensureProfile may not register with the gc.
		ws.ensureGcroots ()

		try:
			yield ws
		finally:
			ws.close ()

	def ensurePermissions (self):
		setPermissions (PermissionTarget.USER, getuser (), 'rwX', self.directory,
				default=True, recursive=True)

	@contextlib.contextmanager
	def copy (self, directory: Path):
		copydir (self.directory, directory)

		with self.__class__.open (directory) as ws:
			ws.ensurePermissions ()
			# make sure the new workspace is usable
			ws.ensureProfile ()
			# ensureProfile may not register with the gc.
			ws.ensureGcroots ()

			try:
				yield ws
			finally:
				pass

	def move (self, destination: Path):
		""" Move workspace to a different directory (on the same filesystem) """
		os.rename (self.directory, destination)
		self.directory = destination
		# Move GC references to new location.
		self.ensureGcroots ()

