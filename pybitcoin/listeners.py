__author__ = 'chris'
"""
Copyright (c) 2015 Chris Pacia
"""
from zope.interface import Interface


class DownloadListener(Interface):
    """
    Listen for blockchain download events and track the progress.
    """

    def download_started(peer, blocks_left):
        """
        Called when the blockchain download starts.

        Args:
            peer: the ip/port `tuple` of the download peer's address.
            blocks_left: the approximate number of blocks to download.
        """

    def on_block_downloaded(peer, block, blocks_left):
        """
        Called after validating each block.

        Args:
            peer: the ip/port `tuple` of the download peer's address.
            block: the block (or header) which was just downloaded.
            blocks_left: the approximate number of blocks left to download.
        """

    def progress(percent, blocks_downloaded):
        """
        Called after downloading each blocks and shows the percentage complete.

        Args:
            percent: the percent complete.
            blocks_downloaded: total number of blocks downloaded so far.
        """

    def download_complete():
        """
        Called when the download is complete.
        """


class PeerEventListener(Interface):
    """
    Listen for connections/disconnections from peers.
    """

    def on_peer_connected(peer, peer_count):
        """
        Called when we connect to a peer.

        Args:
            peer: the ip/port `tuple` of the peer's address.
            peer_count: number of connected peers.
        """

    def on_peer_disconnected(peer, peer_count):
        """
        Called when we disconnect from a peer.

        Args:
            peer: the ip/port `tuple` of the peer's address.
            peer_count: number of connected peers.
        """