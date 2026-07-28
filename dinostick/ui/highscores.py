"""Local best runs, persisted with Kivy's JsonStore.

Deliberately tiny, and deliberately forgiving in the same way ``settings`` is:
this is a leaderboard for one phone, not a database. A missing file, an
unreadable one, or one written by a future version all fall back to an empty
table rather than raising. Nobody should ever fail to reach the menu because
their high scores would not parse.

Storage only -- no widgets. The menu's card lives in ``screens/menu.py``.

Kept in memory as well as on disk. The menu reads ``top()`` on every entry and
the table is at most ``KEEP`` rows, so re-reading a file for that would be
silly; the file exists so the numbers survive a restart, not as the working
copy.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

from kivy.storage.jsonstore import JsonStore

FILENAME = "highscores.json"

# Rows kept on disk. Ten is enough to make "did I beat my third best?" a real
# question and small enough that the whole table is one trivial write.
KEEP = 10
# Rows the menu card shows. Vertical space on a landscape phone is the scarcest
# resource in this whole UI -- see screens/menu.py.
SHOWN = 3

# JsonStore is a key/value store; the whole table lives under one key. A key
# per score would mean sorting a directory of entries to answer any question.
_KEY = "table"

_store: JsonStore | None = None
_scores: list[dict[str, Any]] = []


def _sorted(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Best first. Distance breaks a tie -- two runs can score the same."""
    return sorted(rows,
                  key=lambda r: (int(r.get("score", 0)),
                                 float(r.get("distance", 0.0))),
                  reverse=True)


def load(user_data_dir: str) -> list[dict[str, Any]]:
    """Open (or create) the store and read the table into memory."""
    global _store, _scores
    _scores = []
    try:
        os.makedirs(user_data_dir, exist_ok=True)
        _store = JsonStore(os.path.join(user_data_dir, FILENAME))
        if _store.exists(_KEY):
            rows = _store.get(_KEY).get("scores", [])
            if isinstance(rows, list):
                _scores = _sorted([r for r in rows if isinstance(r, dict)])[:KEEP]
    except Exception:
        # A corrupt file, a read-only directory, a JsonStore that cannot write
        # its parent. Play on with an empty table held in memory.
        _store = None
    return _scores


def top(count: int = SHOWN) -> list[dict[str, Any]]:
    return _scores[:max(0, count)]


def best() -> int:
    return int(_scores[0].get("score", 0)) if _scores else 0


def is_empty() -> bool:
    return not _scores


def submit(score: int, distance: float = 0.0, seconds: float | None = None,
           players: int = 1, mode: str = "local",
           token: str | None = None) -> tuple[str | None, int | None]:
    """Record a finished run. Returns ``(token, rank)``.

    ``rank`` is the 1-based position in the table, or None if the run did not
    make the top ``KEEP``.

    ``token`` is what makes this safe to call twice for the same run, which
    happens routinely: a networked joiner ends its own run the moment it hears
    about the death, and the host's GAME_OVER arrives a moment later with the
    authoritative score. Passing the token back from the first call UPDATES
    that row instead of adding a second one, so a corrected score never shows
    up as two entries a few points apart.
    """
    score = int(score)
    if score <= 0:
        # Quitting out of a run that never started is not a high score.
        return token, None

    row = {
        "score": score,
        "distance": round(float(distance), 1),
        "seconds": None if seconds is None else round(float(seconds), 1),
        "players": int(players),
        "mode": str(mode),
        "at": time.strftime("%Y-%m-%d"),
        "run": token or uuid.uuid4().hex,
    }

    rows = [r for r in _scores if r.get("run") != row["run"]]
    rows.append(row)
    _replace(_sorted(rows)[:KEEP])

    rank = next((i + 1 for i, r in enumerate(_scores)
                 if r.get("run") == row["run"]), None)
    return row["run"], rank


def _replace(rows: list[dict[str, Any]]) -> None:
    global _scores
    _scores = rows
    if _store is None:
        return
    try:
        _store.put(_KEY, scores=_scores)
    except Exception:
        pass  # read-only storage: the table still works for this session


def clear() -> None:
    """Wipe the table. Nothing in the UI calls this; it is here for testing."""
    _replace([])
