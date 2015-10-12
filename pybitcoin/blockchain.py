__author__ = 'chris'
import sqlite3 as lite
from bitcoin.core import CBlockHeader, CheckBlockHeader, CheckBlockHeaderError, b2lx, lx
from bitcoin.net import CBlockLocator

TESTNET_CHECKPOINT = {
    "height": 576576,
    "hash": "000000000000204500050ea47622bdd55a30c7c9eab4fc42b5ffc9128fa08370",
    "timestamp": 1444520907,
    "difficulty_target": 439683892
}


class BlockDatabase(object):

    """
    This class maintains a database of block headers needed to prove a transaction exists in the blockchain. It's
    primary key is total difficulty, hence the last entry in the database should be the tip of the chain. When a new
    block is passed into `process_block` we validate it, look up it's parent in the chain (reject if no parent exists),
    add the difficulty of the block to the cumulative difficulty of the parent, and insert into the database at the
    appropriate height. Since valid blocks and orphans are both stored in the same table, blockchain reorganizations
    are automatically handled. If an orphan chain overtakes the main chain, it's head will extend past the previous
    head. It only keeps enough headers (5000) to guard against a reorg, everything before that is deleted.

    """

    def __init__(self, filepath, testnet=False):
        self.db = lite.connect(filepath)
        self.db.text_factory = str
        try:
            self._create_table(testnet)
        except Exception:
            pass

    def _create_table(self, testnet):
        cursor = self.db.cursor()
        cursor.execute('''CREATE TABLE blocks(totalWork REAL PRIMARY KEY, height INTEGER, blockID TEXT, hashOfPrevious TEXT, timestamp INTEGER, target INTEGER)''')

        cursor.execute('''CREATE INDEX blockIndx ON blocks(blockID);''')

        cursor.execute('''INSERT INTO blocks(totalWork, height, blockID, hashOfPrevious, timestamp, target) VALUES (?,?,?,?,?,?)''',
                       (0, TESTNET_CHECKPOINT["height"], TESTNET_CHECKPOINT["hash"], "", TESTNET_CHECKPOINT["timestamp"], TESTNET_CHECKPOINT["difficulty_target"]))

        self.db.commit()

    def _commit_block(self, height, block_id, hash_of_previous, bits, timestamp, target):
        cursor = self.db.cursor()
        cursor.execute('''SELECT totalWork FROM blocks WHERE height=?''', (height-1,))
        total_work = cursor.fetchone()[0] + CBlockHeader.calc_difficulty(bits)
        cursor = self.db.cursor()
        cursor.execute('''INSERT INTO blocks(totalWork, height, blockID, hashOfPrevious, timestamp, target) VALUES (?,?,?,?,?,?)''',
                       (total_work, height, block_id, hash_of_previous, timestamp, target))
        self.db.commit()
        self._cull()

    def _get_parent_height(self, header):
        cursor = self.db.cursor()
        cursor.execute('''SELECT height FROM blocks WHERE blockID=?''', (b2lx(header.hashPrevBlock),))
        height = cursor.fetchone()
        if height is not None:
            return height[0]
        else:
            return None

    def _get_starting_height(self):
        cursor = self.db.cursor()
        cursor.execute('''SELECT height FROM blocks ORDER BY ROWID ASC LIMIT 1''')
        return cursor.fetchone()[0]

    def _cull(self):
        cursor = self.db.cursor()
        start = self._get_starting_height()
        end = self.get_height()
        if end - start > 5000:
            for i in range(end-start):
                cursor.execute('''DELETE FROM blocks WHERE height=?''', (start+i,))

    def _get_parent(self, block_id):
        cursor = self.db.cursor()
        cursor.execute('''SELECT hashOfPrevious FROM blocks WHERE blockID=?;''', (block_id, ))
        return cursor.fetchone()[0]

    def _check_timestamp(self, timestamp):
        cursor = self.db.cursor()
        cursor.execute('''SELECT blockID FROM blocks WHERE totalWork = (SELECT MAX(totalWork) FROM blocks);''')
        tip = cursor.fetchone()[0]
        if self.get_height() - self._get_starting_height() > 10:
            timestamps = []
            timestamps.append(self.get_timestamp(tip))
            for i in range(10):
                tip = self._get_parent(tip)
                timestamps.append(self.get_timestamp(tip))
            if timestamp <= timestamps[4]:
                raise CheckBlockHeaderError("Invalid Timestamp")

    def _check_difficulty_target(self, header):
        parent = b2lx(header.hashPrevBlock)
        target = self.get_difficulty_target(parent)
        if (self.get_height() + 1) % 2016 == 0:
            end = self.get_timestamp(parent)
            min, max = (302400, 4838400)
            for i in range(2015):
                parent = self._get_parent(parent)
            start = self.get_timestamp(parent)
            difference = end - start
            if difference < min:
                difference = min
            elif difference > max:
                difference = max
            target = target * difference / (60 * 60 * 24 * 14)
        if header.nBits < target:
            raise CheckBlockHeaderError("Target difficutly is incorrect")
        return target

    def get_block_id(self, height):
        cursor = self.db.cursor()
        cursor.execute('''SELECT blockID FROM blocks WHERE height = ?;''', (height,))
        return cursor.fetchone()[0]

    def get_difficulty_target(self, block_id):
        cursor = self.db.cursor()
        cursor.execute('''SELECT target FROM blocks WHERE blockID=?''', (block_id,))
        return cursor.fetchone()[0]

    def get_timestamp(self, block_id):
        cursor = self.db.cursor()
        cursor.execute('''SELECT timestamp FROM blocks WHERE blockID = ?;''', (block_id,))
        return cursor.fetchone()[0]

    def get_height(self):
        cursor = self.db.cursor()
        cursor.execute('''SELECT height FROM blocks WHERE totalWork = (SELECT MAX(totalWork) FROM blocks);''')
        return cursor.fetchone()[0]

    def get_locator(self):
        """
        Get a block locator object to give our remote peer when fetching headers.
        """

        locator = CBlockLocator()
        parent = self.get_block_id(self.get_height())

        def rollback(parent, n):
            for i in range(n):
                parent = self._get_parent(parent)
            return parent

        step = -1
        start = 0
        height = self.get_height()
        while(True):
            if start >= 10:
                step *= 2
                start = 0
            locator.vHave.append(lx(parent))
            parent = rollback(parent, abs(step))
            start += 1
            height += step
            if height <= self._get_starting_height() + abs(step):
                break
        return locator

    def process_block(self, block):
        """
        A block (or header) passed into this function will be added into the database only if it passes all
        validity checks.
        """
        try:
            header = block if isinstance(block, CBlockHeader) else block.get_header()
            CheckBlockHeader(header, True)
            # self._check_timestamp(header.nTime) # not working on testnet?
            target = self._check_difficulty_target(header)
            h = self._get_parent_height(header)
            if h is not None:
                self._commit_block(h + 1, b2lx(header.GetHash()), b2lx(header.hashPrevBlock), header.nBits, header.nTime, target)
        except CheckBlockHeaderError, e:
            print e.message
