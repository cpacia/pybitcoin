__author__ = 'chris'
import bitcoin
import random
from io import BytesIO
from random import shuffle
from protocol import PeerFactory
from twisted.internet import reactor, defer, task
from discovery import dns_discovery
from binascii import unhexlify
from extensions import BloomFilter
from bitcoin.core import CTransaction
from bitcoin.net import CInv
from bitcoin.messages import msg_inv
from bitcoin import base58
from blockchain import BlockDatabase


class BitcoinClient(object):

    def __init__(self, addrs, params="mainnet", blockchain=None, user_agent="/pyBitcoin:0.1/", max_connections=10):
        self.addrs = addrs
        self.params = params
        self.blockchain = blockchain
        self.user_agent = user_agent
        self.max_connections = max_connections
        self.peers = []
        self.inventory = {}
        self.pending_txs = {}
        self.subscriptions = {}
        self.bloom_filter = BloomFilter(10, 0.1, random.getrandbits(32), BloomFilter.UPDATE_NONE)
        self._connect_to_peers()
        if self.blockchain: self._start_chain_download()
        bitcoin.SelectParams(params)

    def _connect_to_peers(self):
        if len(self.peers) < self.max_connections:
            shuffle(self.addrs)
            for i in range(self.max_connections - len(self.peers)):
                if len(self.addrs) > 0:
                    addr = self.addrs.pop(0)
                    peer = PeerFactory(self.params, self.user_agent, self.inventory, self.subscriptions,
                                       self.bloom_filter, self._on_peer_disconnected, self.blockchain)
                    reactor.connectTCP(addr[0], addr[1], peer)
                    self.peers.append(peer)

    def _start_chain_download(self):
        if self.peers[0].protocol is None:
            return task.deferLater(reactor, 1, self._start_chain_download)
        self.peers[0].protocol.download_blocks(self.check_for_more_blocks)

    def check_for_more_blocks(self):
        for peer in self.peers:
            if peer.protocol.version.nStartingHeight > self.blockchain.get_height():
                print "still more to download"
                peer.protocol.download_blocks(self.check_for_more_blocks)
                break

    def _on_peer_disconnected(self, peer):
        self.peers.remove(peer)
        self._connect_to_peers()

    def broadcast_tx(self, tx):
        """
        Sends the tx to half our peers and waits for half of the remainder to
        announce it via inv packets before calling back.
        """
        def on_peer_anncounce(txid):
            self.subscriptions[txhash]["announced"] += 1
            if self.subscriptions[txhash]["announced"] >= self.subscriptions[txhash]["ann_threshold"]:
                if self.subscriptions[txid]["timeout"].active():
                    self.subscriptions[txid]["timeout"].cancel()
                    self.subscriptions[txid]["deferred"].callback(True)

        d = defer.Deferred()
        transaction = CTransaction.stream_deserialize(BytesIO(unhexlify(tx)))
        txhash = transaction.GetHash()
        self.inventory[txhash] = transaction

        cinv = CInv()
        cinv.type = 1
        cinv.hash = txhash

        inv_packet = msg_inv()
        inv_packet.inv.append(cinv)

        self.bloom_filter.insert(txhash)
        self.subscriptions[txhash] = {
            "announced": 0,
            "ann_threshold": len(self.peers)/4,
            "callback": on_peer_anncounce,
            "confirmations": 0,
            "in_blocks": [],
            "deferred": d,
            "timeout": reactor.callLater(10, d.callback, False)
        }

        for peer in self.peers[len(self.peers)/2:]:
            peer.protocol.load_filter()
        for peer in self.peers[:len(self.peers)/2]:
            peer.protocol.send_message(inv_packet)

        return d

    def subscribe_address(self, address, callback):
        """
        Listen on an address for transactions. Since we can't validate unconfirmed
        txs we will only callback if the tx is announced by a majority of our peers.
        """

        def on_peer_announce(txhash):
            if self.subscriptions[txhash]["announced"] < self.subscriptions[txhash]["ann_threshold"]:
                self.subscriptions[txhash]["announced"] += 1
                if self.subscriptions[txhash]["announced"] >= self.subscriptions[txhash]["ann_threshold"]:
                    callback(self.subscriptions[txhash]["tx"], self.subscriptions[txhash]["confirmations"])
                    self.subscriptions[txhash]["last_confirmation"] = self.subscriptions[txhash]["confirmations"]
            elif self.subscriptions[txhash]["confirmations"] > self.subscriptions[txhash]["last_confirmation"]:
                self.subscriptions[txhash]["last_confirmation"] = self.subscriptions[txhash]["confirmations"]
                callback(self.subscriptions[txhash]["tx"], self.subscriptions[txhash]["in_blocks"], self.subscriptions[txhash]["confirmations"])

        self.subscriptions[address] = (len(self.peers)/2, on_peer_announce)
        self.bloom_filter.insert(base58.decode(address)[1:21])
        for peer in self.peers:
            peer.protocol.load_filter()

    def unsubscribe_address(self, address):
        """
        Unsubscribe to an address. Will update the bloom filter to reflect its
        state before the address was inserted.
        """
        if address in self.subscriptions:
            self.bloom_filter.remove(base58.decode(address)[1:21])
            for peer in self.peers:
                peer.protocol.load_filter()
            del self.subscriptions[address]


if __name__ == "__main__":
    # Connect to testnet
    bd = BlockDatabase("blocks.db", testnet=True)
    BitcoinClient(dns_discovery(True), params="testnet", blockchain=bd)
    reactor.run()
