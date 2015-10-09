__author__ = 'chris'
import enum
import bitcoin
from twisted.internet.protocol import Protocol, ClientFactory
from twisted.internet import reactor

from bitcoin.messages import *
from bitcoin.core import b2lx
from bitcoin.net import CInv
from bitcoin.wallet import CBitcoinAddress
from extensions import msg_version2, msg_filterload

State = enum.Enum('State', ('CONNECTING', 'CONNECTED', 'SHUTDOWN'))
PROTOCOL_VERSION = 70002

class BitcoinProtocol(Protocol):

    def __init__(self, user_agent, inventory, bloom_filter):
        self.user_agent = user_agent
        self.inventory = inventory
        self.bloom_filter = bloom_filter
        self.timeouts = {}
        self.callbacks = {}
        self.state = State.CONNECTING

    def connectionMade(self):
        """
        Send the version message and start the handshake
        """
        self.timeouts["verack"] = reactor.callLater(5, self.response_timeout, "verack")
        self.timeouts["version"] = reactor.callLater(5, self.response_timeout, "version")
        msg_version2(PROTOCOL_VERSION, self.user_agent).stream_serialize(self.transport)

    def dataReceived(self, data):
        try:
            m = MsgSerializable.from_bytes(data)

            if m.command == "verack":
                self.timeouts["verack"].cancel()
                del self.timeouts["verack"]
                if "version" not in self.timeouts:
                    self.on_handshake_complete()

            elif m.command == "version":
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
                    if item.hash in self.callbacks:
                        self.callbacks[item.hash](item.hash)
                        del self.callbacks[item.hash]
                        
                    elif item.type == 1 and item.hash not in self.inventory:
                        self.timeouts[item.hash] = reactor.callLater(5, self.response_timeout, item.hash)

                        cinv = CInv()
                        cinv.type = 1
                        cinv.hash = item.hash

                        getdata_packet = msg_getdata()
                        getdata_packet.inv.append(cinv)

                        getdata_packet.stream_serialize(self.transport)

                    elif item.hash in self.inventory:
                        self.inventory[item.hash][1](item.hash)

                    print "Peer %s:%s announced new %s %s" % (self.transport.getPeer().host, self.transport.getPeer().port, CInv.typemap[item.type], b2lx(item.hash))

            elif m.command == "tx":
                if m.tx.GetHash() in self.timeouts:
                    self.timeouts[m.tx.GetHash()].cancel()
                for out in m.tx.vout:
                    addr = str(CBitcoinAddress.from_scriptPubKey(out.scriptPubKey))
                    if addr in self.callbacks:
                        self.inventory[m.tx.GetHash()] = (m.tx, self.callbacks[addr])
                        self.callbacks[addr](m.tx.GetHash())

            else:
                print "Received message %s from %s:%s" % (m.command, self.transport.getPeer().host, self.transport.getPeer().port)

        except Exception:
            print "Error handling message"

    def on_handshake_complete(self):
        print "Connected to peer %s:%s" % (self.transport.getPeer().host, self.transport.getPeer().port)
        self.state = State.CONNECTED
        self.load_filter()

    def response_timeout(self, id):
        del self.timeouts[id]
        for t in self.timeouts.values():
            t.cancel()
        if self.state != State.SHUTDOWN:
            print "Peer unresponsive, disconnecting..."
        self.transport.loseConnection()
        self.state = State.SHUTDOWN

    def send_message(self, message_obj):
        if self.state == State.CONNECTING:
            reactor.callLater(1, self.send_message, message_obj)
        else:
            message_obj.stream_serialize(self.transport)

    def load_filter(self):
        msg_filterload(filter=self.bloom_filter).stream_serialize(self.transport)

    def add_inv_callback(self, key, cb):
        self.callbacks[key] = cb

    def connectionLost(self, reason):
        self.state = State.SHUTDOWN
        print "Connection to %s:%s closed" % (self.transport.getPeer().host, self.transport.getPeer().port)


class PeerFactory(ClientFactory):

    def __init__(self, params, user_agent, inventory, bloom_filter, disconnect_cb):
        self.params = params
        self.user_agent = user_agent
        self.inventory = inventory
        self.bloom_filter = bloom_filter
        self.cb = disconnect_cb
        self.protocol = None
        bitcoin.SelectParams(params)

    def buildProtocol(self, addr):
        self.protocol = BitcoinProtocol(self.user_agent, self.inventory, self.bloom_filter)
        return self.protocol

    def clientConnectionFailed(self, connector, reason):
        print "Connection failed, will try a different node"
        self.cb(self)

    def clientConnectionLost(self, connector, reason):
        self.cb(self)
