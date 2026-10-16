"""Conversation message tree.

Messages form a tree through ``parent_id`` (``NULL`` for a first message).
Editing a message adds a sibling under the same parent; regenerating adds a
sibling answer. A conversation shows exactly one *active path* from a root to
``conversations.active_leaf_id``. Each message remembers which child was last
selected under it (``active_child_id``), so switching to a sibling brings back
the most recently viewed continuation of that branch.

``position`` is the node's depth (0 for roots); siblings are ordered by
creation time. ``parent_id`` deliberately has no ``ON DELETE CASCADE``: SQLite
cascades recurse once per level and fail on chains deeper than ~1000 messages,
so subtree deletion is done here explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..db import Database

NODE_COLUMNS = "id, parent_id, active_child_id, created_at, role, status"


@dataclass
class Tree:
    nodes: dict[str, dict]
    children: dict[str | None, list[str]]

    def kids(self, parent_id: str | None) -> list[str]:
        return self.children.get(parent_id, [])

    def descend(self, start: str) -> str:
        """Follow last-selected children (or the newest child) down to a leaf."""
        node = start
        seen = {node}
        while True:
            kids = self.kids(node)
            if not kids:
                return node
            preferred = self.nodes[node].get("active_child_id")
            nxt = preferred if preferred in kids else kids[-1]
            if nxt in seen:  # defensive: never loop on corrupt data
                return node
            seen.add(nxt)
            node = nxt

    def path_to(self, leaf: str) -> list[str]:
        path: list[str] = []
        node: str | None = leaf
        while node is not None and node in self.nodes and node not in path:
            path.append(node)
            node = self.nodes[node]["parent_id"]
        return list(reversed(path))

    def subtree(self, root: str) -> list[str]:
        out, stack = [], [root]
        while stack:
            node = stack.pop()
            out.append(node)
            stack.extend(self.kids(node))
        return out

    def siblings(self, node: str) -> list[str]:
        return self.kids(self.nodes[node]["parent_id"])


def load(db: Database, conversation_id: str) -> Tree:
    rows = db.all(
        f"SELECT {NODE_COLUMNS} FROM messages WHERE conversation_id = ? ORDER BY created_at, rowid", (conversation_id,)
    )
    nodes = {r["id"]: r for r in rows}
    children: dict[str | None, list[str]] = {}
    for r in rows:
        parent = r["parent_id"] if r["parent_id"] in nodes else None
        r["parent_id"] = parent
        children.setdefault(parent, []).append(r["id"])
    return Tree(nodes, children)


def active_leaf(db: Database, conversation_id: str, tree: Tree | None = None) -> str | None:
    tree = tree or load(db, conversation_id)
    if not tree.nodes:
        return None
    row = db.one("SELECT active_leaf_id FROM conversations WHERE id = ?", (conversation_id,))
    leaf = row["active_leaf_id"] if row else None
    if leaf in tree.nodes:
        # A stored leaf that has since gained children (e.g. a generation that
        # finished on this branch) continues to its newest descendant.
        return tree.descend(leaf)
    roots = tree.kids(None)
    return tree.descend(roots[-1]) if roots else None


def active_path(db: Database, conversation_id: str, tree: Tree | None = None) -> list[str]:
    tree = tree or load(db, conversation_id)
    leaf = active_leaf(db, conversation_id, tree)
    return tree.path_to(leaf) if leaf else []


def activate(db: Database, conversation_id: str, node_id: str, conn=None, tree: Tree | None = None) -> list[str]:
    """Make the branch through ``node_id`` active; returns the new active path."""
    tree = tree or load(db, conversation_id)
    leaf = tree.descend(node_id)
    path = tree.path_to(leaf)
    execute = conn.execute if conn is not None else db.execute
    for parent, child in zip(path, path[1:]):
        if tree.nodes[parent].get("active_child_id") != child:
            execute("UPDATE messages SET active_child_id = ? WHERE id = ?", (child, parent))
    execute("UPDATE conversations SET active_leaf_id = ? WHERE id = ?", (leaf, conversation_id))
    return path


def delete_subtree(db: Database, conversation_id: str, node_id: str) -> tuple[int, list[str]]:
    """Delete a message and all its descendants, then repair the active path.

    Returns ``(deleted_count, new_active_path)``. When the deleted node was on
    the active path, the view moves to the parent's newest remaining branch
    (or the parent itself)."""
    tree = load(db, conversation_id)
    if node_id not in tree.nodes:
        return 0, active_path(db, conversation_id, tree)
    doomed = tree.subtree(node_id)
    path_before = active_path(db, conversation_id, tree)
    parent = tree.nodes[node_id]["parent_id"]
    with db.transaction() as conn:
        for start in range(0, len(doomed), 500):
            chunk = doomed[start : start + 500]
            conn.execute(f"DELETE FROM messages WHERE id IN ({','.join('?' * len(chunk))})", tuple(chunk))
        if parent:
            conn.execute("UPDATE messages SET active_child_id = NULL WHERE id = ? AND active_child_id = ?", (parent, node_id))
    tree = load(db, conversation_id)
    if node_id in path_before:
        if parent and parent in tree.nodes:
            path = activate(db, conversation_id, parent, tree=tree)
        else:
            roots = tree.kids(None)
            path = activate(db, conversation_id, roots[-1], tree=tree) if roots else []
            if not roots:
                db.execute("UPDATE conversations SET active_leaf_id = NULL WHERE id = ?", (conversation_id,))
    else:
        path = active_path(db, conversation_id, tree)
    return len(doomed), path
