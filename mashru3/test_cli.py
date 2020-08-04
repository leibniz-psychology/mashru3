from .cli import prefixes, isPrefix

def test_prefixes ():
	assert list (prefixes ([])) == []
	assert list (prefixes ([1, 2, 3])) == [[1], [1, 2], [1, 2, 3]]

def test_isPrefix ():
	assert isPrefix ('abc', 'abc')
	assert not isPrefix ('abc', 'ab')
	assert isPrefix ([1, 2], [1, 2, 3])
	assert not isPrefix ([1, 3], [1, 2, 3])

