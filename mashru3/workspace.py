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

import secrets, os, configparser, subprocess, time, logging, re
from pathlib import Path
from getpass import getuser

import yaml, importlib_resources
from unidecode import unidecode

from .uid import uintToQuint
from .filesystem import getPermissions, setPermissions, PermissionTarget, softlock
from .util import run, ExecutionFailed, now
from .config import GUIX_PROGRAM

logger = logging.getLogger (__name__)

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

	def ensureGcroots (self):
		""" Make sure all store references are protected from the garbage collector """
		with importlib_resources.files (__package__).joinpath ('scripts/addRoots.scm') as script:
			cmd = [GUIX_PROGRAM, 'repl', '--', script, self.directory]
			run (cmd)

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
					ext = f'_{secrets.randbelow (2**16)}'
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

def initWorkspace (ws, verbose=False):
	# Fix permissions. Make sure the creator has default permissions, so files
	# created by other users are accessible by default.
	setPermissions (PermissionTarget.USER, getuser (), 'rwX', ws.directory, default=True, recursive=True)

	ws.ensureProfile ()

	ws.writeMetadata ()

	return True