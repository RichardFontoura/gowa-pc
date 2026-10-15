import datetime
from pathlib import Path
import shutil

KEEP = 10
STAMP = '%Y%m%d-%H%M%S'


def list_backups(root):
    root = Path(root)
    return sorted((p.name for p in root.iterdir() if p.is_dir()), reverse=True) if root.is_dir() else []


def label(name):
    try:
        return datetime.datetime.strptime(name[:15], STAMP).strftime('%d/%m/%Y %H:%M:%S') + name[15:]
    except ValueError:
        return name


def has_saves(savedata):
    savedata = Path(savedata)
    return savedata.is_dir() and any(p.is_file() for p in savedata.rglob('*'))


def prune(root, keep=KEEP):
    for name in list_backups(root)[keep:]:
        shutil.rmtree(Path(root) / name, ignore_errors=True)


def copy(savedata, root, now=None):
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
    made = copy(savedata, root, now)
    if made:
        prune(root, keep)
    return made


def restore(savedata, root, name, now=None, keep=KEEP):
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
