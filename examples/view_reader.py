"""Read a persisted named read view from a fresh OS process.

Invoked by ``python3 -m examples.workflows views`` as a child process, but also
runnable by hand against any database file:

    python3 -m examples.view_reader PATH VIEW_NAME [COLLECTION]

The child process opens its own SQLite handle, so the name it resolves and the
revision it reads are independent of the process that created the view.
"""
import json
import os
import sys

from strataleaf import Database


def describe(path, view_name, collection="segments"):
    with Database(path) as db:
        view = db.views.get(view_name)
        snapshot = db.views.snapshot(view_name)
        keys, cursor = [], None
        while True:
            page = db.query(collection, view=view_name, limit=2, cursor=cursor)
            keys.extend(row.key for row in page.records)
            cursor = page.cursor
            if cursor is None:
                break
        return {"pid": os.getpid(), "view": view, "head": db.revision,
                "view_keys": [item.key for item in snapshot["collections"].get(collection, [])],
                "page_keys": keys}


if __name__ == "__main__":
    print(json.dumps(describe(*sys.argv[1:]), ensure_ascii=False, sort_keys=True))
