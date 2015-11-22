__author__ = 'chris'
"""
Copyright (c) 2015 Chris Pacia
"""
import dns.resolver
from log import Logger

TESTNET3_SEEDS = [
    "testnet-seed.bitcoin.schildbach.de",
    "testnet-seed.bitcoin.petertodd.org"
]

MAINNET_SEEDS = [
    "seed.bitcoin.sipa.be",
    "dnsseed.bluematt.me",
    "dnsseed.bitcoin.dashjr.org",
    "seed.bitcoinstats.com",
    "seed.bitnodes.io"
]


def dns_discovery(testnet=False):
    log = Logger(system="Discovery")
    addrs = []
    for seed in TESTNET3_SEEDS if testnet else MAINNET_SEEDS:
        answers = dns.resolver.query(seed)
        for addr in answers:
            addrs.append((str(addr), 18333 if testnet else 8333))
    log.info("DNS discovery returned %s peers" % len(addrs))
    return addrs
