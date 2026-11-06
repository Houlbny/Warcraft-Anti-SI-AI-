"""Small transactional local store. Secrets never belong in this database."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock


class Store:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / 'state.sqlite3'
        self.lock = RLock()
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS records (namespace TEXT, key TEXT, value TEXT NOT NULL, PRIMARY KEY(namespace,key))')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db, timeout=20)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, namespace, key, default=None):
        with self.lock, self.connect() as db:
            row = db.execute('SELECT value FROM records WHERE namespace=? AND key=?', (namespace, key)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, namespace, key, value):
        with self.lock, self.connect() as db:
            db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)', (namespace, key, json.dumps(value, ensure_ascii=False)))
        return value

    def all(self, namespace):
        with self.lock, self.connect() as db:
            rows = db.execute('SELECT value FROM records WHERE namespace=? ORDER BY rowid DESC', (namespace,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def items(self, namespace, prefix=''):
        with self.lock, self.connect() as db:
            rows = db.execute('SELECT key,value FROM records WHERE namespace=? AND substr(key,1,?)=? ORDER BY rowid DESC',
                              (namespace, len(prefix), prefix)).fetchall()
        return {key: json.loads(value) for key, value in rows}

    def put_many(self, records, *, delete_records=()):
        """Publish related records in one transaction, without partial chapters."""
        with self.lock, self.connect() as db:
            db.executemany('INSERT OR REPLACE INTO records VALUES (?,?,?)', [(namespace, key, json.dumps(value, ensure_ascii=False)) for namespace, key, value in records])
            db.executemany('DELETE FROM records WHERE namespace=? AND key=?', delete_records)

    def delete_record(self, namespace, key):
        with self.lock, self.connect() as db:
            db.execute('DELETE FROM records WHERE namespace=? AND key=?', (namespace, key))
