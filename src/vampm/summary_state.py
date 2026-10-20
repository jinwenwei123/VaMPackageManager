"""Durable summary completion, independent of labels and physical library paths."""
import json

from .util import stamp


class SummaryState:
    @staticmethod
    def setup(db):
        db.executescript('''
            CREATE TABLE IF NOT EXISTS summary_state (
                pkgkey TEXT PRIMARY KEY, completed INTEGER NOT NULL DEFAULT 0,
                fingerprints TEXT NOT NULL DEFAULT '[]', run_id TEXT NOT NULL DEFAULT '', updated TEXT);
            CREATE INDEX IF NOT EXISTS idx_summary_run ON summary_state(run_id);
        ''')
        # Never infer completion from old descriptions. This seeds each UID only
        # once; subsequent app starts retain successful completion records.
        db.execute('''INSERT OR IGNORE INTO summary_state(pkgkey)
            SELECT DISTINCT pkgkey FROM files WHERE area IN ('active','vault')''')
        db.execute('INSERT OR IGNORE INTO settings VALUES(?,?)',('summary_tracking_version','1'))

    @staticmethod
    def fingerprint(row):
        return f"{row['size']}:{row['mtime']}"

    @staticmethod
    def observe(db,key,size,mtime):
        db.execute('INSERT OR IGNORE INTO summary_state(pkgkey) VALUES(?)',(key,))
        state=db.execute('SELECT completed,fingerprints FROM summary_state WHERE pkgkey=?',(key,)).fetchone()
        if state['completed'] and f'{size}:{mtime}' not in json.loads(state['fingerprints']):
            # A new version has a new key; a changed copy under the same key
            # invalidates completion without erasing the useful old description.
            db.execute('UPDATE summary_state SET completed=0,updated=? WHERE pkgkey=?',(stamp(),key))

    @classmethod
    def complete(cls,state,rows,summary):
        if not state or not state['completed'] or not summary or not str(summary).strip():return False
        known=set(json.loads(state['fingerprints']))
        return all(cls.fingerprint(r) in known for r in rows)

    @classmethod
    def save(cls,db,key,summary,source,run_id=None):
        old=db.execute('SELECT run_id FROM summary_state WHERE pkgkey=?',(key,)).fetchone()
        rows=db.execute("SELECT size,mtime FROM files WHERE pkgkey=? AND area IN ('active','vault')",(key,)).fetchall()
        state={'completed':int(bool(str(summary).strip()) and source in ('ai','manual')),
               'fingerprints':json.dumps(sorted({cls.fingerprint(r) for r in rows})),
               'run_id':run_id if run_id is not None else (old['run_id'] if old else ''),'updated':stamp()}
        db.execute('''INSERT OR REPLACE INTO summary_state VALUES(?,?,?,?,?)''',
                   (key,state['completed'],state['fingerprints'],state['run_id'],state['updated']))
        return state
