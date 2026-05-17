"""Category management for library primitives and components."""

import json
import shutil
from pathlib import Path
from typing import Literal

LibType = Literal["primitives", "components"]


class CategoryError(Exception):
    """Raised for invalid category operations."""


class CategoryManager:
    """Filesystem-level operations for library category directories.

    Operates on a single *primary root* (the first writable library root).
    Read-only (built-in) roots are reflected in listings but cannot be mutated.

    Directory layout::

        <root>/primitives/<category>/<name>.json
        <root>/components/<category>/<name>.json
    """

    def __init__(self, primary_root: Path, readonly_roots: list[Path] | None = None):
        self.primary_root = primary_root
        self.readonly_roots = readonly_roots or []

    # ------------------------------------------------------------------ #
    # Queries                                                              #
    # ------------------------------------------------------------------ #

    def list_categories(self, lib_type: LibType) -> list[str]:
        """Return sorted list of category names visible across all roots."""
        cats: set[str] = set()
        for root in [self.primary_root] + self.readonly_roots:
            type_dir = root / lib_type
            if type_dir.exists():
                for p in type_dir.iterdir():
                    if p.is_dir():
                        cats.add(p.name)
        return sorted(cats)

    def list_items(self, lib_type: LibType, category: str) -> list[str]:
        """Return sorted list of JSON stem names in a category across all roots."""
        names: set[str] = set()
        for root in [self.primary_root] + self.readonly_roots:
            cat_dir = root / lib_type / category
            if cat_dir.exists():
                names.update(p.stem for p in cat_dir.glob("*.json"))
        return sorted(names)

    def item_count(self, lib_type: LibType, category: str) -> int:
        """Return total number of items in the category across all roots."""
        return len(self.list_items(lib_type, category))

    def category_exists(self, lib_type: LibType, category: str) -> bool:
        for root in [self.primary_root] + self.readonly_roots:
            if (root / lib_type / category).is_dir():
                return True
        return False

    def is_primary_category(self, lib_type: LibType, category: str) -> bool:
        """Return True if the category exists in the primary (writable) root."""
        return (self.primary_root / lib_type / category).is_dir()

    # ------------------------------------------------------------------ #
    # Mutations (primary root only)                                        #
    # ------------------------------------------------------------------ #

    def create_category(self, lib_type: LibType, name: str) -> Path:
        """Create a new category directory in the primary root.

        Raises:
            CategoryError: if *name* is blank, contains path separators, or
                the category already exists in the primary root.
        """
        self._validate_name(name)
        cat_dir = self.primary_root / lib_type / name
        if cat_dir.exists():
            raise CategoryError(f"Category '{name}' already exists.")
        cat_dir.mkdir(parents=True)
        return cat_dir

    def rename_category(self, lib_type: LibType, old_name: str, new_name: str) -> None:
        """Rename a category in the primary root.

        Updates the ``category`` field (or ``component_config.category``) inside
        every JSON file so they stay in sync with the directory structure.

        Raises:
            CategoryError: if *old_name* does not exist in the primary root, or
                *new_name* already exists in the primary root.
        """
        self._validate_name(new_name)
        old_dir = self.primary_root / lib_type / old_name
        new_dir = self.primary_root / lib_type / new_name

        if not old_dir.exists():
            raise CategoryError(f"Category '{old_name}' not found in primary root.")
        if new_dir.exists():
            raise CategoryError(f"Category '{new_name}' already exists.")

        old_dir.rename(new_dir)
        self._update_category_field(lib_type, new_dir, new_name)

    def delete_category(self, lib_type: LibType, name: str, *, force: bool = False) -> None:
        """Delete a category directory from the primary root.

        Args:
            force: If True, delete even when items are present (moves nothing —
                the caller is responsible for ensuring designs are not in use).
                If False (default), raises ``CategoryError`` when the category
                is non-empty.

        Raises:
            CategoryError: if the category does not exist in the primary root, or
                is non-empty and *force* is False.
        """
        cat_dir = self.primary_root / lib_type / name
        if not cat_dir.exists():
            raise CategoryError(f"Category '{name}' not found in primary root.")

        if not force and any(cat_dir.glob("*.json")):
            count = sum(1 for _ in cat_dir.glob("*.json"))
            raise CategoryError(
                f"Category '{name}' contains {count} item(s). "
                "Delete or move items first, or use force=True."
            )

        shutil.rmtree(cat_dir)

    def move_item(
        self,
        lib_type: LibType,
        item_name: str,
        from_category: str,
        to_category: str,
    ) -> Path:
        """Move a JSON file between categories in the primary root.

        Updates the ``category`` field inside the file after moving.

        Returns:
            The new file path.

        Raises:
            CategoryError: if source file or target directory does not exist.
        """
        src_dir = self.primary_root / lib_type / from_category
        dst_dir = self.primary_root / lib_type / to_category

        # Find the file (stem = item_name, but actual filename may differ)
        src_file = self._find_item_file(src_dir, item_name)
        if src_file is None:
            raise CategoryError(
                f"Item '{item_name}' not found in category '{from_category}'."
            )
        if not dst_dir.exists():
            raise CategoryError(f"Target category '{to_category}' does not exist.")

        dst_file = dst_dir / src_file.name
        src_file.rename(dst_file)
        self._update_category_field_single(lib_type, dst_file, to_category)
        return dst_file

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _validate_name(name: str) -> None:
        if not name or not name.strip():
            raise CategoryError("Category name must not be empty.")
        if any(c in name for c in r"/\:*?\"<>|"):
            raise CategoryError(f"Category name '{name}' contains invalid characters.")
        if name != name.strip():
            raise CategoryError("Category name must not have leading/trailing whitespace.")

    @staticmethod
    def _find_item_file(directory: Path, item_name: str) -> Path | None:
        """Find a JSON file whose stem matches item_name (case-insensitive)."""
        if not directory.exists():
            return None
        target = item_name.lower()
        for f in directory.glob("*.json"):
            # Check exact match or name-derived match
            if f.stem == target or f.stem == item_name:
                return f
        # Fallback: look for the component name inside the JSON
        for f in directory.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                if data.get("name") == item_name:
                    return f
            except Exception:
                pass
        return None

    @staticmethod
    def _update_category_field(lib_type: LibType, cat_dir: Path, category: str) -> None:
        """Update the category field in all JSON files inside *cat_dir*."""
        for json_file in cat_dir.glob("*.json"):
            CategoryManager._update_category_field_single(lib_type, json_file, category)

    @staticmethod
    def _update_category_field_single(lib_type: LibType, json_file: Path, category: str) -> None:
        try:
            data = json.loads(json_file.read_text())
            if lib_type == "components" and "component_config" in data:
                data["component_config"]["category"] = category
            else:
                data["category"] = category
            json_file.write_text(json.dumps(data, indent=4))
        except Exception as e:
            print(f"Warning: could not update category field in {json_file}: {e}")
