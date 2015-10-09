# pybitcoin
Ultra-lightweight bitcoin client and library

This library allows you to create a bitcoin client that connects directly to the p2p network. 
It handles the bare minimum network messages necessary to connect to the network, broadcast and download transactions.

## Installation

```
python setup.py install
```

## Usage

```python
from pybitcoin.client import BitcoinClient

# connect to your own node on localhost
BitcoinClient([("localhost", 8333)])
reactor.run()
```

```python
from pybitcoin.client import BitcoinClient
from pybitcoin.discovery import dns_discovery

# Alternatively query the dns seeds for peers
# We will connect to the testnet in this example
client = BitcoinClient(dns_discovery(True), params="testnet")
reactor.run()
```

```python
# broadcast a transaction
def on_broadcast_complete(success):
    if success:
        print "Broadcast successful"

tx = "01000000014bbb4302d919ac7612d6c52093fa2f411e231869295064baa9d2bfc562a2a914000000008b483045022100f6b8fce5db5c3b8a9b92e1f74f45959df860068a056c2e8c9425cadb83c4e7cd022055aa3476fa2d915cf4efe6850bba5392b07e6f95241c6c10bd88a451aa2bf2cd014104cfc882f3e582f6698544545e4d52f4798ec7e96e2fbb9a6927361de22d383b7e071ccd3c0f12e904ac2214feb2002dd64af190161bb3e942a5920ce211986c46ffffffff0110270000000000001976a914e7c1345fc8f87c68170b3aa798a956c2fe6a9eff88ac00000000"
client.broadcast_tx(tx).addCallback(on_broadcast_complete)
```

```python
# subscribe to an address
def on_tx_received(tx):
    print tx

client.subscribe_address("n2eMqTT929pb1RDNuqEnxdaLau1rxy3efi", on_tx_received)
```

## TODO
Download the block headers and verify the merkle proofs of the subscribed addresses
