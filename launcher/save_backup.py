"""Timestamped backups of the game's save data directory.

Each backup is a full copy of the savedata tree in its own subdirectory of a
backup root, named after the local time it was made (``YYYYmmdd-HHMMSS``, with
``-2``, ``-3``... appended on collisions). Because the names sort
chronologically, the newest backups come first in reverse lexical order and
only the most recent ``KEEP`` are retained.
"""

import datetime
from pathlib import Path
import shutil

# Number of backups kept by prune(); older ones are deleted.
KEEP = 10
# strftime format of a backup directory name; always 15 characters long.
STAMP = '%Y%m%d-%H%M%S'


def list_backups(root):
    """Return the backup directory names under ``root``, newest first.

    Every subdirectory counts as a backup. A missing ``root`` yields an empty list.
    """
    root = Path(root)
    return sorted((p.name for p in root.iterdir() if p.is_dir()), reverse=True) if root.is_dir() else []


def label(name):
    """Format a backup directory name for display.

    The timestamp prefix becomes ``dd/mm/YYYY HH:MM:SS`` and any collision
    suffix is kept, e.g. ``20240101-120000-2`` -> ``01/01/2024 12:00:00-2``.
    Names that do not start with a timestamp are returned unchanged.
    """
    try:
        return datetime.datetime.strptime(name[:15], STAMP).strftime('%d/%m/%Y %H:%M:%S') + name[15:]
    except ValueError:
        return name


def has_saves(savedata):
    """Return ``True`` if ``savedata`` is a directory containing at least one file (at any depth)."""
    savedata = Path(savedata)
    return savedata.is_dir() and any(p.is_file() for p in savedata.rglob('*'))


def prune(root, keep=KEEP):
    """Delete every backup under ``root`` except the ``keep`` newest; removal errors are ignored."""
    for name in list_backups(root)[keep:]:
        shutil.rmtree(Path(root) / name, ignore_errors=True)


def copy(savedata, root, now=None):
    """Copy the ``savedata`` tree into a new timestamped directory under ``root``.

    Does nothing when there are no saves (see :func:`has_saves`). Creates
    ``root`` if needed and never overwrites an existing backup: a numeric
    suffix is added when the timestamp is already taken. Does not prune.

    Args:
        savedata: The game's savedata directory.
        root: Directory that holds the backups.
        now: ``datetime`` used for the name; defaults to the current local time.

    Returns:
        The ``Path`` of the new backup, or ``None`` if nothing was copied.
    """
    if not has_saves(savedata):
        return None
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    base = (now or datetime.datetime.now()).strftime(STAMP)
    target, n = root / base, 2
    while target.exists():
        target, n = root / f'{base}-{n}', n + 1
    shutil.copytree(savedata, target)
    return target


def backup(savedata, root, now=None, keep=KEEP):
    """Make a backup with :func:`copy` and, if one was made, prune down to ``keep``.

    Returns:
        The ``Path`` of the new backup, or ``None`` if there were no saves.
    """
    made = copy(savedata, root, now)
    if made:
        prune(root, keep)
    return made


def restore(savedata, root, name, now=None, keep=KEEP):
    """Replace the current saves with the backup ``name``.

    First the current saves are copied into a new "safety" backup so the
    restore can be undone. The chosen backup is then copied to a sibling
    staging directory (``<savedata>.restoring``), the old savedata directory
    is deleted and the staging directory is renamed into its place. Finally
    the backups are pruned to ``keep``.

    Args:
        savedata: The game's savedata directory.
        root: Directory that holds the backups.
        name: Directory name of the backup to restore, as returned by :func:`list_backups`.
        now: ``datetime`` used to name the safety backup.
        keep: Number of backups to keep afterwards.

    Returns:
        The ``Path`` of the safety backup, or ``None`` if there were no saves to protect.

    Raises:
        ValueError: If ``name`` is empty or not an existing backup.
        OSError: If copying or renaming fails.
    """
    if not name or name not in list_backups(root):
        raise ValueError(f'Backup de saves inexistente: {name!r}')
    savedata = Path(savedata)
    source = Path(root) / name
    safety = copy(savedata, root, now)
    staging = savedata.with_name(savedata.name + '.restoring')
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(source, staging)
    shutil.rmtree(savedata, ignore_errors=True)
    staging.rename(savedata)
    prune(root, keep)
    return safety
