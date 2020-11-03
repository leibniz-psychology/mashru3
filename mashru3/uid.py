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

# see https://arxiv.org/html/0901.4016 on how to build proquints (human
# pronouncable unique ids)
toConsonant = 'bdfghjklmnprstvz'
toVowel = 'aiou'

def u16ToQuint (v):
    """ Transform a 16 bit unsigned integer into a single quint """
    assert 0 <= v < 2**16
    # quints are “big-endian”
    return ''.join ([
            toConsonant[(v>>(4+2+4+2))&0xf],
            toVowel[(v>>(4+2+4))&0x3],
            toConsonant[(v>>(4+2))&0xf],
            toVowel[(v>>4)&0x3],
            toConsonant[(v>>0)&0xf],
            ])

def uintToQuint (v, length=2):
    """ Turn any integer into a proquint with fixed length """
    assert 0 <= v < 2**(length*16)

    return '-'.join (reversed ([u16ToQuint ((v>>(x*16))&0xffff) for x in range (length)]))

