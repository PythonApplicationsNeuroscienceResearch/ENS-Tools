"""Image file access, including images stored inside .zip archives.

The lab delivers one archive per slide ('Lame 424C_Images jpeg traitées auto
contrast.zip').  Images can be used directly from the archives: a member is
addressed as ``<archive path>::<member name>``.  Extracted files work as well;
when both exist, the extracted file is used.
"""
from __future__ import annotations

import io
import os
import shutil
import zipfile
from typing import Dict, Tuple

from PIL import Image

from .naming import IMAGE_EXTENSIONS, image_key

SEP = "::"


# === Paths of plain files and of images stored inside ZIP archives ===========
def split(path: str) -> Tuple[str, str]:
    """('archive.zip', 'member') for an archive member, (path, '') otherwise."""
    if SEP in path:
        archive, member = path.split(SEP, 1)
        return archive, member
    return path, ""


def display_name(path: str) -> str:
    """File name shown to the user (the member name for an image stored in a ZIP)."""
    archive, member = split(path)
    return os.path.basename(member or archive)


def container(path: str) -> str:
    """Real file on disk (the archive for a member)."""
    return split(path)[0]


def exists(path: str) -> bool:
    """True if the image exists, either as a plain file or as a member of a ZIP archive."""
    if not path:
        return False
    archive, member = split(path)
    if not member:
        return os.path.isfile(archive)
    if not os.path.isfile(archive):
        return False
    try:
        with zipfile.ZipFile(archive) as z:
            z.getinfo(member)
        return True
    except (KeyError, zipfile.BadZipFile, OSError):
        return False


def signature(path: str) -> str:
    """String that changes when the image changes (used for thumbnail caching)."""
    archive, member = split(path)
    st = os.stat(archive)
    return f"{os.path.abspath(archive)}|{member}|{st.st_size}|{int(st.st_mtime)}"


def source(path: str):
    """Object accepted by PIL.Image.open: a file path, or the bytes of an archive member."""
    archive, member = split(path)
    if not member:
        return archive
    with zipfile.ZipFile(archive) as z:      # closed immediately (no file lock on Windows)
        return io.BytesIO(z.read(member))


def open_image(path: str) -> Image.Image:
    """Open and fully load an image (file or archive member)."""
    with Image.open(source(path)) as im:
        im.load()
        return im.copy()


def copy_to(path: str, folder: str) -> str:
    """Copy the image into ``folder`` (extracting it from its archive if needed)."""
    dest = os.path.join(folder, display_name(path))
    archive, member = split(path)
    if not member:
        shutil.copy2(archive, dest)
    else:
        with zipfile.ZipFile(archive) as z, open(dest, "wb") as fh:
            fh.write(z.read(member))
    return dest


# === Folder scan =============================================================
def scan(root: str) -> Dict[str, str]:
    """Index every image below ``root`` (files and .zip members) by canonical key."""
    files: Dict[str, str] = {}
    members: Dict[str, str] = {}
    if not root or not os.path.isdir(root):
        return files

    def better(old: str, new: str) -> bool:
        # JPEG preferred over TIFF for the same image (single analysed plane, smaller).
        """True when the new file should replace the old one for the same image (JPEG preferred
        over TIFF).
        """
        return not old.lower().endswith((".jpg", ".jpeg")) and new.lower().endswith((".jpg", ".jpeg"))

    for folder, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith((".", "_backups", "__MACOSX"))]
        for n in sorted(names):
            if n.startswith(("~$", "._")):
                continue
            full = os.path.join(folder, n)
            low = n.lower()
            if low.endswith(IMAGE_EXTENSIONS):
                k = image_key(n)
                if k not in files or better(files[k], full):
                    files[k] = full
            elif low.endswith(".zip"):
                try:
                    with zipfile.ZipFile(full) as z:
                        for info in z.infolist():
                            m = info.filename
                            base = os.path.basename(m)
                            if info.is_dir() or "__MACOSX" in m or base.startswith("._"):
                                continue
                            if m.lower().endswith(IMAGE_EXTENSIONS):
                                k = image_key(base)
                                p = f"{full}{SEP}{m}"
                                if k not in members or better(members[k], p):
                                    members[k] = p
                except (zipfile.BadZipFile, OSError):
                    continue
    for k, p in members.items():
        if k not in files:
            files[k] = p
    return files
