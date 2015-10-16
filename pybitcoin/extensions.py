__author__ = 'chris'
import random
import struct
import bitcoin
import math
from bitcoin.core import CBlockHeader, b2x
from bitcoin.messages import msg_version, MsgSerializable
from bitcoin.core.serialize import VarStringSerializer, VarIntSerializer, ser_read
from bitcoin.bloom import CBloomFilter
from hashlib import sha256
from io import BytesIO

PROTO_VERSION = 70002


class MsgHeader(MsgSerializable):
    """
    A class for just the message header.
    """

    def __init__(self, command, msglen, checksum):
        self.params = bitcoin.params.MESSAGE_START
        self.command = command
        self.msglen = msglen
        self.checksum = checksum

    @classmethod
    def from_bytes(cls, b, protover=PROTO_VERSION):
        f = BytesIO(b)
        return MsgHeader.stream_deserialize(f, protover=protover)

    @classmethod
    def stream_deserialize(cls, f, protover=PROTO_VERSION):
        recvbuf = ser_read(f, 4 + 12 + 4 + 4)

        # check magic
        if recvbuf[:4] != bitcoin.params.MESSAGE_START:
            raise ValueError("Invalid message start '%s', expected '%s'" %
                             (b2x(recvbuf[:4]), b2x(bitcoin.params.MESSAGE_START)))

        # remaining header fields: command, msg length, checksum
        command = recvbuf[4:4+12].split(b"\x00", 1)[0]
        msglen = struct.unpack(b"<i", recvbuf[4+12:4+12+4])[0]
        checksum = recvbuf[4+12+4:4+12+4+4]
        return MsgHeader(command, msglen, checksum)


class msg_version2(msg_version):
    """
    An extension of the python-bitcoinlib message class which bumps
    the version number and adds the relay boolean. Also changes the
    default services to zero.
    """

    def __init__(self, protover=PROTO_VERSION, user_agent="/pyBitcoin0.1/", nStartingHeight=-1):
        super(msg_version2, self).__init__(protover)
        self.nStartingHeight = nStartingHeight
        self.relay = False
        self.nServices = 0
        self.strSubVer = user_agent
        self.addrFrom.nServices = 0
        self.addrFrom.ip = "127.0.0.1"
        self.addrTo.ip = "127.0.0.1"

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
            self.filter = BloomFilter(3, 0.01, random.getrandbits(32), CBloomFilter.UPDATE_NONE)
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


class BloomFilter(CBloomFilter):
    """
    An extension of the python-bitcoinlib CBloomFilter class to allow for
    removal of inserted objects.
    """

    def __init__(self, nElements, nFPRate, nTweak, nFlags):
        super(BloomFilter, self).__init__(nElements, nFPRate, nTweak, nFlags)
        self._elements = []
        self.nFPRate = nFPRate
        self.nElements = nElements

    __bit_mask = bytearray([0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80])
    def insert(self, elem):
        """Insert an element in the filter.
        elem may be a COutPoint or bytes
        """
        if isinstance(elem, bitcoin.core.COutPoint):
            elem = elem.serialize()

        if len(self.vData) == 1 and self.vData[0] == 0xff:
            return

        for i in range(0, self.nHashFuncs):
            nIndex = self.bloom_hash(i, elem)
            # Sets bit nIndex of vData
            self.vData[nIndex >> 3] |= self.__bit_mask[7 & nIndex]

        self._elements.append(elem)

    def remove(self, elem):
        """
        Remove an element from the bloom filter. Works by clearing the filter and re-inserting
        the elements that weren't removed.
        """
        LN2SQUARED = 0.4804530139182014246671025263266649717305529515945455
        LN2 = 0.6931471805599453094172321214581765680755001343602552
        if elem in self._elements:
            self._elements.remove(elem)
            self.vData = bytearray(int(min(-1  / LN2SQUARED * self.nElements * math.log(self.nFPRate), self.MAX_BLOOM_FILTER_SIZE * 8) / 8))
            self.nHashFuncs = int(min(len(self.vData) * 8 / self.nElements * LN2, self.MAX_HASH_FUNCS))

            for element in self._elements:
                self.insert(element)


class CMerkleBlock(CBlockHeader):
    """
    The merkle block returned to spv clients when a filter is set on the remote peer.
    """

    __slots__ = ['nTX', 'vHashes', 'vFlags']

    def __init__(self, nVersion=3, hashPrevBlock=b'\x00'*32, hashMerkleRoot=b'\x00'*32, nTime=0, nBits=0, nNonce=0, nTX=0, vHashes=(), vFlags=()):
        """Create a new block"""
        super(CMerkleBlock, self).__init__(nVersion, hashPrevBlock, hashMerkleRoot, nTime, nBits, nNonce)

        object.__setattr__(self, 'nTX', nTX)
        object.__setattr__(self, 'vHashes', vHashes)
        object.__setattr__(self, 'vFlags', vFlags)

    @classmethod
    def stream_deserialize(cls, f):

        def bits(f, n):
            ret = []
            bytes = (ord(b) for b in f.read(n))
            for b in bytes:
                for i in xrange(8):
                    ret.append((b >> i) & 1)
            return ret

        self = super(CMerkleBlock, cls).stream_deserialize(f)

        nTX = struct.unpack('<L', ser_read(f, 4))[0]
        nHashes = VarIntSerializer.stream_deserialize(f)
        vHashes = []
        for i in range(nHashes):
            vHashes.append(ser_read(f, 32))
        nFlags = VarIntSerializer.stream_deserialize(f)
        vFlags = bits(f, nFlags)
        object.__setattr__(self, 'nTX', nTX)
        object.__setattr__(self, 'vHashes', vHashes)
        object.__setattr__(self, 'vFlags', vFlags)

        return self

    def stream_serialize(self, f):
        super(CMerkleBlock, self).stream_serialize(f)
        f.write(struct.pack('<L', self.nTX))
        VarIntSerializer.stream_serialize(len(self.vHashes), f)
        for hash in self.vHashes:
            f.write(hash)
        VarIntSerializer.stream_serialize(len(self.vFlags)/8, f)
        bin_string = ""
        for bit in self.vFlags:
            bin_string += str(bit)
            if len(bin_string) == 8:
                f.write(struct.pack('B', int(bin_string[::-1], 2)))
                bin_string = ""

    def get_matched_txs(self):
        """
        Return a list of transaction hashes that matched the filter. These txs
        have been validated against the merkle tree structure and are definitely
        in the block. However, the block hash still needs to be checked against
        the best chain in the block database.
        """
        # TODO: perform a number of checks to make sure everything is formatted properly
        def getTreeWidth(transaction_count, height):
            return (transaction_count + (1 << height) - 1) >> height

        matched_hashes = []

        def recursive_extract_hashes(height, pos):
            parent_of_match = bool(self.vFlags.pop(0))
            if height == 0 or not parent_of_match:
                hash = self.vHashes.pop(0)
                if height == 0 and parent_of_match:
                    matched_hashes.append(hash)
                return hash
            else:
                left = recursive_extract_hashes(height - 1, pos * 2)
                if pos * 2 + 1 < getTreeWidth(self.nTX, height-1):
                    right = recursive_extract_hashes(height - 1, pos * 2 + 1)
                    if left == right:
                        raise Exception("Invalid Merkle Tree")
                else:
                    right = left
                return sha256(sha256(left+right).digest()).digest()

        height = 0
        while getTreeWidth(self.nTX, height) > 1:
            height += 1

        calculated_root = recursive_extract_hashes(height, 0)
        if calculated_root == self.get_header().hashMerkleRoot:
            return matched_hashes
        else:
            return None

    def get_header(self):
        """Return the block header
        Returned header is a new object.
        """
        return CBlockHeader(nVersion=self.nVersion,
                            hashPrevBlock=self.hashPrevBlock,
                            hashMerkleRoot=self.hashMerkleRoot,
                            nTime=self.nTime,
                            nBits=self.nBits,
                            nNonce=self.nNonce)

    def GetHash(self):
        """Return the block hash
        Note that this is the hash of the header, not the entire serialized
        block.
        """
        try:
            return self._cached_GetHash
        except AttributeError:
            _cached_GetHash = self.get_header().GetHash()
            object.__setattr__(self, '_cached_GetHash', _cached_GetHash)
            return _cached_GetHash

class msg_merkleblock(MsgSerializable):
    """
    The MerkleBlock network message
    """
    command = b"merkleblock"

    def __init__(self, protover=PROTO_VERSION):
        super(msg_merkleblock, self).__init__(protover)
        self.block = CMerkleBlock()

    @classmethod
    def msg_deser(cls, f, protover=PROTO_VERSION):
        c = cls()
        c.block = CMerkleBlock.stream_deserialize(f)
        return c

    def msg_ser(self, f):
        self.block.stream_serialize(f)

    def __repr__(self):
        return "msg_merkleblock(header=%s)" % (repr(self.block.get_header()))
