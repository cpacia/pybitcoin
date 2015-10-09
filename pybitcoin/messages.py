__author__ = 'chris'
import random
import struct
from bitcoin.messages import msg_version, MsgSerializable
from bitcoin.core.serialize import VarStringSerializer
from bitcoin.bloom import CBloomFilter


PROTO_VERSION = 70002


class msg_version2(msg_version):
    """
    An extension of the python-bitcoinlib message class which bumps
    the version number and adds the relay boolean. Also changes the
    default services to zero.
    """

    def __init__(self, protover=PROTO_VERSION, user_agent="/pyBitcoin0.1/"):
        super(msg_version2, self).__init__(protover)
        self.relay = False
        self.nServices = 0
        self.strSubVer = user_agent
        self.addrFrom.nServices = 0

    def msg_ser(self, f):
        f.write(struct.pack(b"<i", self.nVersion))
        f.write(struct.pack(b"<Q", self.nServices))
        f.write(struct.pack(b"<q", self.nTime))
        self.addrTo.stream_serialize(f, True)
        self.addrFrom.stream_serialize(f, True)
        f.write(struct.pack(b"<Q", self.nNonce))
        VarStringSerializer.stream_serialize(self.strSubVer, f)
        f.write(struct.pack(b"<i", self.nStartingHeight))
        f.write(struct.pack('?', self.relay))


class msg_filterload(MsgSerializable):
    """
    A filter load message that is missing from python-bitcoinlib
    """
    command = b"filterload"

    def __init__(self, protover=PROTO_VERSION, filter=None):
        super(msg_filterload, self).__init__(protover)
        self.protover = protover
        if not filter:
            self.filter = CBloomFilter(3, 0.01, random.getrandbits(32), CBloomFilter.UPDATE_NONE)
        else:
            self.filter = filter

    @classmethod
    def msg_deser(cls, f, protover=PROTO_VERSION):
        c = cls()
        c.filter = CBloomFilter.stream_deserialize(f)
        return c

    def msg_ser(self, f):
        self.filter.stream_serialize(f)

    def __repr__(self):
        return "msg_filterload(vData=%i nHashFunctions=%i nTweak=%i nFlags=%i" % (self.filter.vData, self.filter.nHashFunctions, self.filter.nTweak, self.filter.nFlags)
