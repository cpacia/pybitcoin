__author__ = 'chris'
import enum
import bitcoin
from twisted.internet.protocol import Protocol, ClientFactory
from twisted.internet import reactor, task


from bitcoin.messages import *
from bitcoin.core import b2lx
from bitcoin.net import CInv
from bitcoin.wallet import CBitcoinAddress
from extensions import msg_version2, msg_filterload, msg_merkleblock, MsgHeader
from io import BytesIO

State = enum.Enum('State', ('CONNECTING', 'CONNECTED', 'SHUTDOWN'))
PROTOCOL_VERSION = 70002

messagemap["merkleblock"] = msg_merkleblock


class BitcoinProtocol(Protocol):

    def __init__(self, user_agent, inventory, subscriptions, bloom_filter, blockchain):
        self.user_agent = user_agent
        self.inventory = inventory
        self.subscriptions = subscriptions
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
        header = MsgHeader.from_bytes(self.buffer)
        if len(self.buffer) < header.msglen + 24:
            return
        try:
            stream = BytesIO(self.buffer)
            m = MsgSerializable.stream_deserialize(stream)
            self.buffer = stream.read()
            if m.command == "verack":
                self.timeouts["verack"].cancel()
                del self.timeouts["verack"]
                if "version" not in self.timeouts:
                    self.on_handshake_complete()

            elif m.command == "version":
                self.version = m
                if m.nVersion < 70001 or m.nServices != 1:
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
                    # callback tx
                    if item.type == 1 and item.hash in self.subscriptions:
                        self.subscriptions[item.hash]["callback"](item.hash)

                    # download tx and check subscription
                    elif item.type == 1 and item.hash not in self.inventory:
                        self.timeouts[item.hash] = reactor.callLater(5, self.response_timeout, item.hash)

                        cinv = CInv()
                        cinv.type = 1
                        cinv.hash = item.hash

                        getdata_packet = msg_getdata()
                        getdata_packet.inv.append(cinv)

                        getdata_packet.stream_serialize(self.transport)

                    # download block
                    elif item.type == 2 or item.type == 3:
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
                    if addr in self.subscriptions:
                        if m.tx.GetHash() not in self.subscriptions:
                            self.subscriptions[m.tx.GetHash()] = {
                                "announced": 0,
                                "ann_threshold": self.subscriptions[addr][0],
                                "confirmations": 0,
                                "callback": self.subscriptions[addr][1],
                                "in_blocks": self.inventory[m.tx.GetHash()] if m.tx.GetHash() in self.inventory else [],
                                "tx": m.tx
                            }
                            self.subscriptions[addr][1](m.tx.GetHash())
                        if m.tx.GetHash() in self.inventory:
                            del self.inventory[m.tx.GetHash()]

            elif m.command == "merkleblock":
                if self.blockchain is not None:
                    self.blockchain.process_block(m.block)
                    # check for block inclusion of subscribed txs
                    for match in m.block.get_matched_txs():
                        if match in self.subscriptions:
                            self.subscriptions[match]["in_blocks"].append(m.block.GetHash())
                        else:
                            # stick the hash here in case this is a tx we missed on broadcast.
                            # when the tx comes over the wire after this block, we will append this hash.
                            self.inventory[match] = [m.block.GetHash()]
                    # run through subscriptions and callback with updated confirmations
                    for txid in self.subscriptions:
                        if len(txid) == 32:
                            confirms = []
                            for block in self.subscriptions[txid]["in_blocks"]:
                                confirms.append(self.blockchain.get_confirmations(block))
                            self.subscriptions[txid]["confirmations"] = max(confirms)
                            self.subscriptions[txid]["callback"](txid)

            elif m.command == "headers":
                to_download = self.version.nStartingHeight - self.blockchain.get_height()
                if len(m.headers) > to_download:
                    self.timeouts["download"].cancel()
                    self.callbacks["download"]()
                    self.response_timeout("download")
                    return
                i = 1
                for header in m.headers:
                    self.blockchain.process_block(header)
                    if i % 50 == 0 or int((i / float(to_download))*100) == 100:
                        print "Chain download %s%% complete" % int((i / float(to_download))*100)
                    i += 1
                if self.blockchain.get_height() < self.version.nStartingHeight:
                    self.download_blocks(self.callbacks["download"])
                elif self.callbacks["download"]:
                    self.timeouts["download"].cancel()
                    self.callbacks["download"]()

            elif m.command == "ping":
                msg_pong(nonce=m.nonce).stream_serialize(self.transport)

            else:
                print "Received message %s from %s:%s" % (m.command, self.transport.getPeer().host, self.transport.getPeer().port)

            if len(self.buffer) > 0: self.dataReceived("")
        except Exception, e:
            print e.message


    def on_handshake_complete(self):
        print "Connected to peer %s:%s" % (self.transport.getPeer().host, self.transport.getPeer().port)
        self.load_filter()
        self.state = State.CONNECTED

    def response_timeout(self, id):
        del self.timeouts[id]
        for t in self.timeouts.values():
            if t.active():
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
            if len(self.subscriptions) > 0:
                get = msg_getblocks()
            else:
                get = msg_getheaders()
            get.locator = self.blockchain.get_locator()
            get.stream_serialize(self.transport)
            if callback:
                self.callbacks["download"] = callback
                self.timeouts["download"] = reactor.callLater(30, callback)

    def send_message(self, message_obj):
        if self.state == State.CONNECTING:
            return task.deferLater(reactor, 1, self.send_message, message_obj)
        message_obj.stream_serialize(self.transport)

    def load_filter(self):
        msg_filterload(filter=self.bloom_filter).stream_serialize(self.transport)

    def connectionLost(self, reason):
        self.state = State.SHUTDOWN
        print "Connection to %s:%s closed" % (self.transport.getPeer().host, self.transport.getPeer().port)


class PeerFactory(ClientFactory):

    def __init__(self, params, user_agent, inventory, subscriptions, bloom_filter, disconnect_cb, blockchain):
        self.params = params
        self.user_agent = user_agent
        self.inventory = inventory
        self.subscriptions = subscriptions
        self.bloom_filter = bloom_filter
        self.cb = disconnect_cb
        self.protocol = None
        self.blockchain = blockchain
        bitcoin.SelectParams(params)

    def buildProtocol(self, addr):
        self.protocol = BitcoinProtocol(self.user_agent, self.inventory, self.subscriptions, self.bloom_filter, self.blockchain)
        return self.protocol

    def clientConnectionFailed(self, connector, reason):
        print "Connection failed, will try a different node"
        self.cb(self)

    def clientConnectionLost(self, connector, reason):
        self.cb(self)
