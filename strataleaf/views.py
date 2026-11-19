from dataclasses import dataclass

from .codec import dumps, loads, name
from .errors import Conflict, InvalidDocument, NotFound

MAX_COLLECTIONS = 256


@dataclass(frozen=True)
class ReadView:
    name: str
    revision: int
    collections: tuple


def normalize_collections(collections):
    if not isinstance(collections, (list, tuple)) or not collections:
        raise InvalidDocument("a read view requires a nonempty list of collections")
    if len(collections) > MAX_COLLECTIONS:
        raise InvalidDocument(f"a read view may name at most {MAX_COLLECTIONS} collections")
    members = []
    for collection in collections:
        name(collection)
        if collection in members:
            raise InvalidDocument("duplicate collection in read view: " + collection)
        members.append(collection)
    return tuple(sorted(members))


def lookup(connection, view_name):
    """Resolve a persisted view inside an existing read transaction."""
    name(view_name)
    row = connection.execute("SELECT name,revision,collections FROM read_views WHERE name=?", (view_name,)).fetchone()
    if row is None:
        raise NotFound("unknown read view: " + view_name)
    return ReadView(row[0], row[1], tuple(loads(row[2])))


def describe(view):
    return {"name": view.name, "revision": view.revision, "collections": list(view.collections)}


def resolve(connection, collection, revision, view):
    """Return the effective revision for a read, enforcing view membership.

    A caller may supply either a raw revision or a view name, never both. The
    view row is read in the same SQLite read transaction as the caller's data,
    so the name-to-revision mapping and the visible snapshot cannot disagree.
    """
    if view is None:
        return revision, None
    if revision is not None:
        raise InvalidDocument("specify either a read view or a revision, not both")
    resolved = lookup(connection, view)
    if collection is not None and collection not in resolved.collections:
        raise InvalidDocument("collection is not part of read view: " + collection)
    return resolved.revision, resolved


class Views:
    """Durable, database-scoped names for one revision over a set of collections.

    A view is catalog metadata rather than a document revision: creating or
    dropping one never advances ``head``. It survives reopening the file and
    blocks retention in the same way a pin does, so its revision remains
    readable until the name is dropped.
    """
    def __init__(self, store):
        self.store = store

    def create(self, view_name, collections, revision=None):
        from .mvcc import checked_revision
        name(view_name)
        members = normalize_collections(collections)
        with self.store.atomic() as connection:
            if connection.execute("SELECT 1 FROM read_views WHERE name=?", (view_name,)).fetchone():
                raise Conflict("read view already exists: " + view_name)
            target = checked_revision(self.store, revision)
            missing = [collection for collection in members
                       if not connection.execute("SELECT 1 FROM collections WHERE name=?", (collection,)).fetchone()]
            if missing:
                raise NotFound("unknown collection for read view: " + ",".join(missing))
            connection.execute("INSERT INTO read_views VALUES (?,?,?)",
                               (view_name, target, dumps(list(members))))
        return describe(ReadView(view_name, target, members))

    def get(self, view_name):
        with self.store.read() as connection:
            return describe(lookup(connection, view_name))

    def list(self):
        with self.store.read() as connection:
            return [describe(ReadView(row["name"], row["revision"], tuple(loads(row["collections"]))))
                    for row in connection.execute("SELECT * FROM read_views ORDER BY name")]

    def snapshot(self, view_name):
        """Read every member collection at the view revision in one transaction."""
        from .mvcc import checked_revision, record, visible_rows
        with self.store.read() as connection:
            resolved = lookup(connection, view_name)
            revision = checked_revision(self.store, resolved.revision)
            documents = {}
            for collection in resolved.collections:
                if not connection.execute("SELECT 1 FROM collections WHERE name=?", (collection,)).fetchone():
                    raise NotFound("unknown collection for read view: " + collection)
                documents[collection] = [record(row) for row in visible_rows(connection, collection, revision)]
            return {"name": resolved.name, "revision": revision, "collections": documents}

    def drop(self, view_name):
        name(view_name)
        with self.store.atomic() as connection:
            row = connection.execute("SELECT revision,collections FROM read_views WHERE name=?", (view_name,)).fetchone()
            if row is None:
                raise NotFound("unknown read view: " + view_name)
            connection.execute("DELETE FROM read_views WHERE name=?", (view_name,))
        return describe(ReadView(view_name, row["revision"], tuple(loads(row["collections"]))))
