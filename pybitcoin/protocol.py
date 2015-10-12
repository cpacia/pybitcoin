__author__ = 'chris'
import enum
import bitcoin
from twisted.internet.protocol import Protocol, ClientFactory
from twisted.internet import reactor, task


from bitcoin.messages import *
from bitcoin.core import b2lx
from bitcoin.net import CInv
from bitcoin.wallet import CBitcoinAddress
from extensions import msg_version2, msg_filterload, msg_merkleblock

State = enum.Enum('State', ('CONNECTING', 'CONNECTED', 'SHUTDOWN'))
PROTOCOL_VERSION = 70002

messagemap["merkleblock"] = msg_merkleblock

class BitcoinProtocol(Protocol):

    def __init__(self, user_agent, inventory, bloom_filter, blockchain):
        self.user_agent = user_agent
        self.inventory = inventory
        self.bloom_filter = bloom_filter
        self.blockchain = blockchain
        self.timeouts = {}
        self.callbacks = {}
        self.state = State.CONNECTING
        self.buffer = ""

    def connectionMade(self):
        """
        Send the version message and start the handshake
        """
        self.timeouts["verack"] = reactor.callLater(5, self.response_timeout, "verack")
        self.timeouts["version"] = reactor.callLater(5, self.response_timeout, "version")
        msg_version2(PROTOCOL_VERSION, self.user_agent, nStartingHeight=self.blockchain.get_height() if self.blockchain else -1).stream_serialize(self.transport)

    def dataReceived(self, data):
        self.buffer += data
        # if self.buffer.length >= sizeof(message header)
        #   messageHeader := MessageHeader.deserialize(self.buffer)
        #   if messageHeader.payloadLength > someBigNumber
        #     throw DropConnection
        #   if self.buffer.length < messageHeader.payloadLength
        #     return
        try:
            m = MsgSerializable.from_bytes(self.buffer)
            self.buffer = ""
            if m.command == "verack":
                self.timeouts["verack"].cancel()
                del self.timeouts["verack"]
                if "version" not in self.timeouts:
                    self.on_handshake_complete()

            elif m.command == "version":
                self.version = m
                if m.nVersion < 70001:
                    self.transport.loseConnection()
                self.timeouts["version"].cancel()
                del self.timeouts["version"]
                msg_verack().stream_serialize(self.transport)
                if "verack" not in self.timeouts:
                    self.on_handshake_complete()

            elif m.command == "getdata":
                for item in m.inv:
                    if item.hash in self.inventory and item.type == 1:
                        transaction = msg_tx()
                        transaction.tx = self.inventory[item.hash]
                        transaction.stream_serialize(self.transport)

            elif m.command == "inv":
                for item in m.inv:
                    # callback tx broadcast
                    if item.type == 1 and item.hash in self.callbacks:
                        self.callbacks[item.hash](item.hash)
                        del self.callbacks[item.hash]

                    # callback subscription and download tx
                    elif item.type == 1 and item.hash not in self.inventory:
                        self.timeouts[item.hash] = reactor.callLater(5, self.response_timeout, item.hash)

                        cinv = CInv()
                        cinv.type = 1
                        cinv.hash = item.hash

                        getdata_packet = msg_getdata()
                        getdata_packet.inv.append(cinv)

                        getdata_packet.stream_serialize(self.transport)

                    # callback subscription but don't download (already have it in inventory)
                    elif item.type == 1 and item.hash in self.inventory:
                        self.inventory[item.hash][1](item.hash)

                    # download block
                    elif item.type == 2:
                        cinv = CInv()
                        cinv.type = 3
                        cinv.hash = item.hash

                        getdata_packet = msg_getdata()
                        getdata_packet.inv.append(cinv)

                        getdata_packet.stream_serialize(self.transport)

                    print "Peer %s:%s announced new %s %s" % (self.transport.getPeer().host, self.transport.getPeer().port, CInv.typemap[item.type], b2lx(item.hash))

            elif m.command == "tx":
                if m.tx.GetHash() in self.timeouts:
                    self.timeouts[m.tx.GetHash()].cancel()
                for out in m.tx.vout:
                    addr = str(CBitcoinAddress.from_scriptPubKey(out.scriptPubKey))
                    if addr in self.callbacks:
                        self.inventory[m.tx.GetHash()] = (m.tx, self.callbacks[addr])
                        self.callbacks[addr](m.tx.GetHash())

            elif m.command == "merkleblock":
                print "downloaded merkleblock"
                self.blockchain.process_block(m)

            elif m.command == "block":
                print "downloaded block"

            elif m.command == "headers":
                self.timeouts["download"].cancel()
                for header in m.headers:
                    self.blockchain.process_block(header)
                if self.blockchain.get_height() < self.version.nStartingHeight:
                    self.download_blocks(self.callbacks["download"])
                elif self.callbacks["download"]:
                    self.callbacks["download"]()

            else:
                print "Received message %s from %s:%s" % (m.command, self.transport.getPeer().host, self.transport.getPeer().port)
        except Exception:
            pass

    def on_handshake_complete(self):
        print "Connected to peer %s:%s" % (self.transport.getPeer().host, self.transport.getPeer().port)
        self.load_filter()
        self.state = State.CONNECTED

    def response_timeout(self, id):
        del self.timeouts[id]
        for t in self.timeouts.values():
            t.cancel()
        if self.state != State.SHUTDOWN:
            print "Peer %s:%s unresponsive, disconnecting..." % (self.transport.getPeer().host, self.transport.getPeer().port)
        self.transport.loseConnection()
        self.state = State.SHUTDOWN

    def download_blocks(self, callback=None):
        if self.state == State.CONNECTING:
            return task.deferLater(reactor, 1, self.download_blocks)
        if self.blockchain is not None:
            print "Downloading blocks from %s:%s" % (self.transport.getPeer().host, self.transport.getPeer().port)
            get = msg_getheaders()
            get.locator = self.blockchain.get_locator()
            get.stream_serialize(self.transport)
            if callback:
                self.callbacks["download"] = callback
                self.timeouts["download"] = reactor.callLater(10, callback)

    def send_message(self, message_obj):
        if self.state == State.CONNECTING:
            return task.deferLater(reactor, 1, self.send_message, message_obj)
        message_obj.stream_serialize(self.transport)

    def load_filter(self):
        msg_filterload(filter=self.bloom_filter).stream_serialize(self.transport)

    def add_inv_callback(self, key, cb):
        self.callbacks[key] = cb

    def connectionLost(self, reason):
        self.state = State.SHUTDOWN
        print "Connection to %s:%s closed" % (self.transport.getPeer().host, self.transport.getPeer().port)


class PeerFactory(ClientFactory):

    def __init__(self, params, user_agent, inventory, bloom_filter, disconnect_cb, blockchain):
        self.params = params
        self.user_agent = user_agent
        self.inventory = inventory
        self.bloom_filter = bloom_filter
        self.cb = disconnect_cb
        self.protocol = None
        self.blockchain = blockchain
        bitcoin.SelectParams(params)

    def buildProtocol(self, addr):
        self.protocol = BitcoinProtocol(self.user_agent, self.inventory, self.bloom_filter, self.blockchain)
        return self.protocol

    def clientConnectionFailed(self, connector, reason):
        print "Connection failed, will try a different node"
        self.cb(self)

    def clientConnectionLost(self, connector, reason):
        self.cb(self)
