"""SQLite persistence: mutable threads and receipts never rewrite historical versions."""
from collections.abc import MutableMapping
from contextlib import contextmanager, closing
from copy import deepcopy
import json
import sqlite3
import zlib


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


class Records(MutableMapping):
    """Transaction-local identity map. Nested edits are detected on flush."""
    def __init__(self, connection, table, cache=None, original=None):
        self.connection, self.table = connection, table
        self.cache = {} if cache is None else cache
        self.original = {} if original is None else original
        self.removed = set()

    def __getitem__(self, key):
        if key in self.removed:
            raise KeyError(key)
        if key not in self.cache:
            row = self.connection.execute(f'SELECT payload FROM {self.table} WHERE id=?', (key,)).fetchone()
            if row is None:
                raise KeyError(key)
            self.original[key] = row[0]
            try:
                payload = zlib.decompress(row[0]) if isinstance(row[0], bytes) else row[0]
                self.cache[key] = json.loads(payload)
            except (zlib.error, ValueError, UnicodeDecodeError) as error:
                raise ValueError('批注记录读取失败') from error
        return self.cache[key]

    def __setitem__(self, key, value):
        self.removed.discard(key)
        self.cache[key] = value

    def __delitem__(self, key):
        self[key]
        self.removed.add(key)
        self.cache.pop(key, None)

    def __iter__(self):
        keys = dict.fromkeys(row[0] for row in self.connection.execute(f'SELECT id FROM {self.table}'))
        keys.update(dict.fromkeys(self.cache))
        return iter(k for k in keys if k not in self.removed)

    def __len__(self):
        return sum(1 for _ in self)

    def __copy__(self):
        result = Records(self.connection, self.table, dict(self.cache), dict(self.original))
        result.removed = set(self.removed)
        return result

    def __deepcopy__(self, memo):
        return {key: deepcopy(self[key], memo) for key in self}

    def active_ids(self):
        ids = dict.fromkeys(row[0] for row in self.connection.execute(
            'SELECT id FROM threads WHERE deleted=0 ORDER BY rowid'))
        for key, value in self.cache.items():
            if value.get('deleted_at'):
                ids.pop(key, None)
            else:
                ids[key] = None
        return [key for key in ids if key not in self.removed]

    def flush(self):
        changed = []
        for key in self.removed:
            changed.append(key)
            self.connection.execute(f'DELETE FROM {self.table} WHERE id=?', (key,))
        for key, value in self.cache.items():
            payload = encode(value)
            if self.table == 'requests' and len(payload) > 4096:
                payload = zlib.compress(payload.encode('utf-8'), level=1)
            if payload == self.original.get(key):
                continue
            changed.append(key)
            if self.table == 'threads':
                self.connection.execute('INSERT INTO threads(id,payload,deleted) VALUES(?,?,?) '
                    'ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,deleted=excluded.deleted',
                    (key, payload, int(bool(value.get('deleted_at')))))
            else:
                self.connection.execute(f'INSERT INTO {self.table}(id,payload) VALUES(?,?) '
                    'ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (key, payload))
            self.original[key] = payload
        self.removed.clear()
        return changed


class Database:
    def __init__(self, path, project_id, schema_version):
        self.path, self.project_id, self.schema_version = path, project_id, schema_version

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        connection.execute('PRAGMA synchronous=FULL')
        connection.execute('PRAGMA foreign_keys=ON')
        return connection

    def initialize(self, legacy=None):
        with closing(self.connect()) as connection:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('CREATE TABLE IF NOT EXISTS metadata(id INTEGER PRIMARY KEY CHECK(id=1), '
                               'project_id TEXT NOT NULL,schema_version TEXT NOT NULL,revision INTEGER NOT NULL)')
            connection.execute('CREATE TABLE IF NOT EXISTS threads(id TEXT PRIMARY KEY,payload TEXT NOT NULL,deleted INTEGER NOT NULL)')
            connection.execute('CREATE INDEX IF NOT EXISTS threads_deleted ON threads(deleted)')
            for table in ('versions', 'requests'):
                connection.execute(f'CREATE TABLE IF NOT EXISTS {table}(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
            connection.execute('CREATE TABLE IF NOT EXISTS changes(revision INTEGER NOT NULL,thread_id TEXT NOT NULL,PRIMARY KEY(revision,thread_id))')
            connection.execute('CREATE TABLE IF NOT EXISTS preflights(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
            if not connection.execute('SELECT 1 FROM metadata').fetchone():
                data = legacy or {'revision': 0, 'threads': {}, 'versions': {}, 'requests': {}}
                connection.execute('INSERT INTO metadata VALUES(1,?,?,?)',
                                   (self.project_id, self.schema_version, data['revision']))
                for table in ('threads', 'versions', 'requests'):
                    records = Records(connection, table)
                    records.update(data.get(table, {}))
                    records.flush()
            connection.commit()

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT project_id,schema_version,revision FROM metadata WHERE id=1').fetchone()
            if row is None or row[:2] != (self.project_id, self.schema_version):
                raise ValueError('批注数据库身份无效')
            data = {'schema_version': row[1], 'project_id': row[0], 'revision': row[2],
                    **{table: Records(connection, table) for table in ('threads', 'versions', 'requests')}}
            yield data
            connection.commit()
        except BaseException:
            connection.rollback()
            if "rollback_source" in locals().get("data", {}):
                data["rollback_source"]()
            raise
        finally:
            connection.close()

    @staticmethod
    def persist(data):
        changed = data['threads'].flush()
        for table in ('versions', 'requests'):
            data[table].flush()
        data['threads'].connection.executemany('INSERT OR IGNORE INTO changes(revision,thread_id) VALUES(?,?)',
                                             [(data['revision'], key) for key in changed])
        data['threads'].connection.execute('UPDATE metadata SET revision=? WHERE id=1', (data['revision'],))
