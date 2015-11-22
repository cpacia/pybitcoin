__author__ = 'chris'
"""
Copyright (c) 2015 Chris Pacia
"""
import enum
import bitcoin
import traceback
from twisted.internet.protocol import Protocol, ClientFactory
from twisted.internet import reactor, task

from bitcoin.messages import *
from bitcoin.core import b2lx
from bitcoin.net import CInv
from bitcoin.wallet import CBitcoinAddress
from extensions import msg_version2, msg_filterload, msg_merkleblock, MsgHeader
from io import BytesIO
from log import Logger

State = enum.Enum('State', ('CONNECTING', 'DOWNLOADING', 'CONNECTED', 'SHUTDOWN'))
PROTOCOL_VERSION = 70002

messagemap["merkleblock"] = msg_merkleblock


class BitcoinProtocol(Protocol):

    def __init__(self, user_agent, inventory, subscriptions, bloom_filter, blockchain):
        self.user_agent = user_agent
        self.inventory = inventory
        self.subscriptions = subscriptions
        self.bloom_filter = bloom_filter
        self.blockchain = blockchain
        self.download_count = 1
        self.download_tracker = [0, 0]
        self.timeouts = {}
        self.callbacks = {}
        self.state = State.CONNECTING
        self.version = None
        self.buffer = ""
        self.log = Logger(system=self)

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
                if self.blockchain is not None:
                    self.to_download = self.version.nStartingHeight - self.blockchain.get_height()
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
                    # This is either an announcement of tx we broadcast ourselves or a tx we have already downloaded.
                    # In either case we only need to callback here.
                    if item.type == 1 and item.hash in self.subscriptions:
                        self.subscriptions[item.hash]["callback"](item.hash)

                    # This is the first time we are seeing this txid. Let's download it and check to see if it sends
                    # coins to any addresses in our subscriptions.
                    elif item.type == 1 and item.hash not in self.inventory:
                        self.timeouts[item.hash] = reactor.callLater(5, self.response_timeout, item.hash)

                        cinv = CInv()
                        cinv.type = 1
                        cinv.hash = item.hash

                        getdata_packet = msg_getdata()
                        getdata_packet.inv.append(cinv)

                        getdata_packet.stream_serialize(self.transport)

                    # The peer announced a new block. Unlike txs, we should download it, even if we've previously
                    # downloaded it from another peer, to make sure it doesn't contain any txs we didn't know about.
                    elif item.type == 2 or item.type == 3:
                        if self.state == State.DOWNLOADING:
                            self.download_tracker[0] += 1
                        cinv = CInv()
                        cinv.type = 3
                        cinv.hash = item.hash

                        getdata_packet = msg_getdata()
                        getdata_packet.inv.append(cinv)

                        getdata_packet.stream_serialize(self.transport)

                    if self.state != State.DOWNLOADING:
                        self.log.debug("Peer %s:%s announced new %s %s" % (self.transport.getPeer().host, self.transport.getPeer().port, CInv.typemap[item.type], b2lx(item.hash)))

            elif m.command == "tx":
                if m.tx.GetHash() in self.timeouts:
                    self.timeouts[m.tx.GetHash()].cancel()
                for out in m.tx.vout:
                    try:
                        addr = str(CBitcoinAddress.from_scriptPubKey(out.scriptPubKey))
                    except Exception:
                        addr = None

                    if addr in self.subscriptions:
                        if m.tx.GetHash() not in self.subscriptions:
                            # It's possible the first time we are hearing about this tx is following block
                            # inclusion. If this is the case, let's make sure we include the correct number
                            # of confirmations.
                            in_blocks = self.inventory[m.tx.GetHash()] if m.tx.GetHash() in self.inventory else []
                            confirms = []
                            if len(in_blocks) > 0:
                                for block in in_blocks:
                                    confirms.append(self.blockchain.get_confirmations(block))
                            self.subscriptions[m.tx.GetHash()] = {
                                "announced": 0,
                                "ann_threshold": self.subscriptions[addr][0],
                                "confirmations": max(confirms) if len(confirms) > 0 else 0,
                                "last_confirmation": 0,
                                "callback": self.subscriptions[addr][1],
                                "in_blocks": in_blocks,
                                "tx": m.tx
                            }
                            self.subscriptions[addr][1](m.tx.GetHash())
                        if m.tx.GetHash() in self.inventory:
                            del self.inventory[m.tx.GetHash()]

            elif m.command == "merkleblock":
                if self.blockchain is not None:
                    self.blockchain.process_block(m.block)
                    if self.state != State.DOWNLOADING:
                        self.blockchain.save()
                    # check for block inclusion of subscribed txs
                    for match in m.block.get_matched_txs():
                        if match in self.subscriptions:
                            self.subscriptions[match]["in_blocks"].append(m.block.GetHash())
                        else:
                            # stick the hash here in case this is the first we are hearing about this tx.
                            # when the tx comes over the wire after this block, we will append this hash.
                            self.inventory[match] = [m.block.GetHash()]
                    # run through subscriptions and callback with updated confirmations
                    for txid in self.subscriptions:
                        try:
                            confirms = []
                            for block in self.subscriptions[txid]["in_blocks"]:
                                confirms.append(self.blockchain.get_confirmations(block))
                            self.subscriptions[txid]["confirmations"] = max(confirms)
                            self.subscriptions[txid]["callback"](txid)
                        except Exception:
                            pass

                    # If we are in the middle of an initial chain download, let's check to see if we have
                    # either reached the end of the download or if we need to loop back around and make
                    # another get_blocks call.
                    if self.state == State.DOWNLOADING:
                        if self.download_count % 50 == 0 or int((self.download_count / float(self.to_download))*100) == 100:
                            # TODO: let's update to a chain download listener here.
                            percent = int((self.download_count / float(self.to_download))*100)
                            if percent == 100:
                                self.log.info("Chain download 100% complete")
                        self.download_count += 1
                        self.download_tracker[1] += 1
                        # We've downloaded every block in the inv packet and still have more to go.
                        if (self.download_tracker[0] == self.download_tracker[1] and
                           self.blockchain.get_height() < self.version.nStartingHeight):
                            if self.timeouts["download"].active():
                                self.timeouts["download"].cancel()
                            self.download_blocks(self.callbacks["download"])
                        # We've downloaded everything so let's callback to the client.
                        elif self.blockchain.get_height() >= self.version.nStartingHeight:
                            self.blockchain.save()
                            self.state = State.CONNECTED
                            self.callbacks["download"]()
                            if self.timeouts["download"].active():
                                self.timeouts["download"].cancel()

            elif m.command == "headers":
                if self.timeouts["download"].active():
                    self.timeouts["download"].cancel()
                for header in m.headers:
                    # If this node sent a block with no parent then disconnect from it and callback
                    # on client.check_for_more_blocks.
                    if self.blockchain.process_block(header) is None:
                        self.blockchain.save()
                        self.callbacks["download"]()
                        self.transport.loseConnection()
                        return
                    if self.download_count % 50 == 0 or int((self.download_count / float(self.to_download))*100) == 100:
                        # TODO: let's update to a chain download listener here.
                        percent = int((self.download_count / float(self.to_download))*100)
                        if percent == 100:
                            self.log.info("Chain download 100% complete")
                    self.download_count += 1
                # The headers message only comes in batches of 500 blocks. If we still have more blocks to download
                # loop back around and call get_headers again.
                if self.blockchain.get_height() < self.version.nStartingHeight:
                    self.download_blocks(self.callbacks["download"])
                else:
                    self.blockchain.save()
                    self.callbacks["download"]()
                    self.state = State.CONNECTED

            elif m.command == "ping":
                msg_pong(nonce=m.nonce).stream_serialize(self.transport)

            else:
                self.log.debug("Received message %s from %s:%s" % (m.command, self.transport.getPeer().host, self.transport.getPeer().port))

            if len(self.buffer) >= 24: self.dataReceived("")
        except Exception:
            traceback.print_exc()

    def on_handshake_complete(self):
        self.log.info("Connected to peer %s:%s" % (self.transport.getPeer().host, self.transport.getPeer().port))
        self.load_filter()
        self.state = State.CONNECTED

    def response_timeout(self, id):
        if id == "download":
            self.callbacks["download"]()
        del self.timeouts[id]
        for t in self.timeouts.values():
            if t.active():
                t.cancel()
        if self.state != State.SHUTDOWN:
            self.log.warning("Peer %s:%s unresponsive, disconnecting..." % (self.transport.getPeer().host, self.transport.getPeer().port))
        self.transport.loseConnection()
        self.state = State.SHUTDOWN

    def download_blocks(self, callback):
        if self.state == State.CONNECTING:
            return task.deferLater(reactor, 1, self.download_blocks, callback)
        if self.blockchain is not None:
            self.log.info("Downloading blocks from %s:%s" % (self.transport.getPeer().host, self.transport.getPeer().port))
            self.state = State.DOWNLOADING
            self.callbacks["download"] = callback
            self.timeouts["download"] = reactor.callLater(30, self.response_timeout, "download")
            if len(self.subscriptions) > 0:
                get = msg_getblocks()
                self.download_tracker = [0, 0]
            else:
                get = msg_getheaders()
            get.locator = self.blockchain.get_locator()
            get.stream_serialize(self.transport)

    def send_message(self, message_obj):
        if self.state == State.CONNECTING:
            return task.deferLater(reactor, 1, self.send_message, message_obj)
        message_obj.stream_serialize(self.transport)

    def load_filter(self):
        msg_filterload(filter=self.bloom_filter).stream_serialize(self.transport)

    def connectionLost(self, reason):
        self.state = State.SHUTDOWN
        self.log.info("Connection to %s:%s closed" % (self.transport.getPeer().host, self.transport.getPeer().port))


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
        self.log.warning("Connection failed, will try a different node")
        self.cb(self)

    def clientConnectionLost(self, connector, reason):
        self.cb(self)
