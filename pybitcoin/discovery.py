__author__ = 'chris'
import dns.resolver

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
    addrs = []
    for seed in TESTNET3_SEEDS if testnet else MAINNET_SEEDS:
        answers = dns.resolver.query(seed)
        for addr in answers:
            addrs.append((str(addr), 18333 if testnet else 8333))
    print "DNS discovery returned %s peers" % len(addrs)
    return addrs
