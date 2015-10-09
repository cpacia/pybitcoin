__author__ = 'chris'
import bitcoin
import random
from io import BytesIO
from random import shuffle
from protocol import PeerFactory
from twisted.internet import reactor, defer
from discovery import dns_discovery
from binascii import unhexlify
from messages import BloomFilter
from bitcoin.core import CTransaction
from bitcoin.net import CInv
from bitcoin.messages import msg_inv
from bitcoin import base58

class BitcoinClient(object):

    def __init__(self, addrs, params="mainnet", user_agent="/pyBitcoin:0.1/", max_connections=10):
        self.addrs = addrs
        self.params = params
        self.user_agent = user_agent
        self.max_connections = max_connections
        self.peers = []
        self.inventory = {}
        self.pending_txs = {}
        self.subscriptions = {}
        self.bloom_filter = BloomFilter(10, 0.1, random.getrandbits(32), BloomFilter.UPDATE_NONE)
        self.connect_to_peers()
        bitcoin.SelectParams(params)

    def connect_to_peers(self):
        if len(self.peers) < self.max_connections:
            shuffle(self.addrs)
            for i in range(self.max_connections - len(self.peers)):
                if len(self.addrs) > 0:
                    addr = self.addrs.pop(0)
                    peer = PeerFactory(self.params, self.user_agent, self.inventory,
                                       self.bloom_filter, self.on_peer_disconnected)
                    reactor.connectTCP(addr[0], addr[1], peer)
                    self.peers.append(peer)

    def on_peer_disconnected(self, peer):
        self.peers.remove(peer)
        self.connect_to_peers()

    def broadcast_tx(self, tx):
        """
        Sends the tx to half our peers and waits for half of the remainder to
        announce it via inv packets before calling back.
        """
        def on_peer_anncounce(txid):
            self.pending_txs[txid][0] += 1
            if self.pending_txs[txid][0] >= self.pending_txs[txid][1]:
                if self.pending_txs[txid][3].active():
                    self.pending_txs[txid][3].cancel()
                    self.pending_txs[txid][2].callback(True)
                    self.bloom_filter.remove(txid)
                    for peer in self.peers:
                        del peer.protocol.callbacks[txid]
                        peer.protocol.load_filter()
                    del self.pending_txs[txid]


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
        self.pending_txs[txhash] = [0, len(self.peers)/4, d, reactor.callLater(10, d.callback, False)]

        for peer in self.peers[len(self.peers)/2:]:
            peer.protocol.load_filter()
            peer.protocol.add_inv_callback(txhash, on_peer_anncounce)
        for peer in self.peers[:len(self.peers)/2]:
            peer.protocol.send_message(inv_packet)

        return d

    def subscribe_address(self, address, callback):
        """
        Listen on an address for transactions. Since we can't validate unconfirmed
        txs we will only callback if the tx is announced by a majority of our peers.
        """
        def on_peer_announce(tx):
            txhash = tx.GetHash()
            if txhash in self.subscriptions[address][0] and self.subscriptions[address][0][txhash][0] != "complete":
                self.subscriptions[address][0][txhash][0] += 1
                if self.subscriptions[address][0][txhash][0] >= self.subscriptions[address][0][txhash][1]:
                    self.subscriptions[address][0][txhash][0] = "complete"
                    self.subscriptions[address][1](tx)
            elif txhash not in self.subscriptions[address][0]:
                self.subscriptions[address][0][txhash] = [1, len(self.peers)/2]

        self.subscriptions[address] = [{}, callback]
        self.bloom_filter.insert(base58.decode(address)[1:21])
        for peer in self.peers:
            peer.protocol.add_inv_callback(address, on_peer_announce)
            peer.protocol.load_filter()

    def unsubscribe_address(self, address):
        """
        Unsubscribe to an address. Will update the bloom filter to reflect its
        state before the address was inserted.
        """
        if address in self.subscriptions:
            self.bloom_filter.remove(base58.decode(address)[1:21])
            for peer in self.peers:
                del peer.protocol.callbacks[address]
                peer.protocol.load_filter()
            del self.subscriptions[address]


if __name__ == "__main__":
    # Connect to testnet
    BitcoinClient(dns_discovery(True), params="testnet")
    reactor.run()
