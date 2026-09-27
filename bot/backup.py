"""Бэкап (база + медиа в один zip) и восстановление."""
import asyncio
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from .app import App

KEEP_LOCAL = 3  # на сервере лежат только 3 последних архива, старые удаляются сами


def _zip(db_copy: Path, media_dir: Path, target: Path, with_media: bool) -> None:
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(db_copy, "bot.db")
        if with_media and media_dir.exists():
            for f in sorted(media_dir.iterdir()):
                if f.is_file() and not f.name.endswith(".tmp"):
                    zf.write(f, f"media/{f.name}", compress_type=zipfile.ZIP_STORED)  # медиа уже сжаты


async def make_backup(app: App, with_media: bool = True) -> Path:
    await app.flush()
    backup_dir = app.config.backup_dir
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"backup_{stamp}{'' if with_media else '_db'}.zip"
    with tempfile.TemporaryDirectory() as tmp:
        db_copy = Path(tmp) / "bot.db"
        await app.db.backup_to(db_copy)
        await asyncio.to_thread(_zip, db_copy, app.config.media_dir, target, with_media)
    old = sorted(backup_dir.glob("backup_*.zip"))[:-KEEP_LOCAL]
    for f in old:
        f.unlink(missing_ok=True)
    return target


def _validate_and_extract(zip_path: Path, dest: Path) -> None:
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if "bot.db" not in names:
            raise ValueError("в архиве нет bot.db")
        for name in names:
            if name != "bot.db" and not (name.startswith("media/") and "/" not in name[6:] and ".." not in name):
                raise ValueError(f"лишний файл в архиве: {name}")
        zf.extractall(dest)
    conn = sqlite3.connect(dest / "bot.db")
    try:
        conn.execute("SELECT COUNT(*) FROM items").fetchone()
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("база в архиве повреждена")
    except sqlite3.DatabaseError as e:
        raise ValueError(f"это не база бота: {e}")
    finally:
        conn.close()


def restore_files(zip_path: Path, db_path: Path, media_dir: Path) -> None:
    """Подменяет базу и дописывает медиа. Бот в это время не должен писать в базу."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        _validate_and_extract(zip_path, tmp_dir)
        if db_path.exists():
            shutil.copy2(db_path, db_path.with_name(f"bot.db.before_restore_{datetime.now():%Y%m%d_%H%M%S}"))
        for suffix in ("-wal", "-shm"):
            db_path.with_name(db_path.name + suffix).unlink(missing_ok=True)
        shutil.copy2(tmp_dir / "bot.db", db_path)
        media_dir.mkdir(parents=True, exist_ok=True)
        src_media = tmp_dir / "media"
        if src_media.exists():
            for f in src_media.iterdir():
                if not (media_dir / f.name).exists():
                    shutil.copy2(f, media_dir / f.name)


async def restore_backup(app: App, zip_path: Path) -> None:
    await app.flush()
    await app.db.close()
    try:
        await asyncio.to_thread(restore_files, zip_path, app.config.db_path, app.config.media_dir)
    finally:
        await app.db.connect()  # применит миграции, если бэкап со старой версии
        from .seed import apply_seed
        await apply_seed(app.db)
        await app.store.reload()
        await app.media.load(app.media.bot_id)
        await app.load_users()
        app.screens.clear()
