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

