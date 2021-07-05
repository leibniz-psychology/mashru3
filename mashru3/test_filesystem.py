import os
from tempfile import TemporaryDirectory

import pytest

from .filesystem import softlock, Busy

def test_softlock_cleanup ():
	with TemporaryDirectory () as d:
		lockpath = os.path.join (d, 'lock')
		with softlock (lockpath):
			assert os.path.exists (lockpath)
		assert not os.path.exists (lockpath)

def test_softlock_cleanup_exception ():
	with TemporaryDirectory () as d:
		lockpath = os.path.join (d, 'lock')
		with pytest.raises (Exception):
			with softlock (lockpath):
				assert os.path.exists (lockpath)
				raise Exception ('nope')
		assert not os.path.exists (lockpath)

def test_softlock_busy ():
	with TemporaryDirectory () as d:
		lockpath = os.path.join (d, 'lock')
		with softlock (lockpath):
			with pytest.raises (Busy):
				with softlock (lockpath):
					assert False

