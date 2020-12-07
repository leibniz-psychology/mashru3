# Copyright 2020 Leibniz Institute for Psychology
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

"""
ctypes binding to libkrb5, specifically for retrieving the default realm found
in /etc/krb5.conf.
"""

from ctypes import CDLL, POINTER, pointer, c_void_p, c_int32, c_char_p
from ctypes.util import find_library

libkrb5 = CDLL (find_library ('krb5'))
krb5_init_context = libkrb5.krb5_init_context
krb5_init_context.argtypes = [POINTER(c_void_p)]
krb5_init_context.restype = c_int32

krb5_free_context = libkrb5.krb5_free_context
krb5_free_context.argtypes = [c_void_p]

krb5_get_default_realm = libkrb5.krb5_get_default_realm
krb5_get_default_realm.argtypes = [c_void_p, POINTER(c_char_p)]
krb5_get_default_realm.restype = c_int32

krb5_free_default_realm = libkrb5.krb5_free_default_realm
krb5_free_default_realm.argtypes = [c_void_p, c_char_p]

def defaultRealm ():
	ctx = c_void_p ()
	res = krb5_init_context (pointer (ctx))
	if res != 0:
		raise Exception (res)

	realm = c_char_p ()
	res = krb5_get_default_realm (ctx, pointer (realm))
	if res != 0:
		raise Exception (res)
	ret = realm.value.decode ('ascii')

	krb5_free_default_realm (ctx, realm)
	krb5_free_context (ctx)

	return ret

