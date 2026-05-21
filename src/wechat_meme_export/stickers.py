from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


@dataclass(frozen=True)
class StickerItem:
    number: int
    path: Path
    md5: str = ""
    caption: str = ""
    product_id: str = ""


def load_stickers(images_dir: Path, index_csv: Path | None = None) -> list[StickerItem]:
    if index_csv and index_csv.exists():
        items: list[StickerItem] = []
        with index_csv.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                if row.get("status") not in {None, "", "exported"}:
                    continue
                output = row.get("output") or ""
                path = Path(output) if output else images_dir / f"{row.get('md5', '')}.png"
                if not path.exists():
                    matches = list(images_dir.glob(f"{row.get('md5', '')}.*"))
                    if not matches:
                        continue
                    path = matches[0]
                if path.suffix.lower() in IMAGE_SUFFIXES:
                    items.append(
                        StickerItem(
                            number=len(items) + 1,
                            path=path,
                            md5=row.get("md5", ""),
                            caption=row.get("caption", ""),
                            product_id=row.get("product_id", ""),
                        )
                    )
        return items

    files = sorted(path for path in images_dir.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES)
    return [StickerItem(number=index + 1, path=path, md5=path.stem) for index, path in enumerate(files)]
