from __future__ import annotations

import json
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any


def _vlog(verbose: bool, message: str) -> None:
    if verbose:
        print(message, flush=True)


def _get_nested(document: dict[str, Any], path: str, verbose: bool = True) -> Any:
    current: Any = document
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _matches(document: dict[str, Any], query: dict[str, Any], verbose: bool = True) -> bool:
    for key, expected in query.items():
        value = _get_nested(document, key, verbose=verbose)
        if isinstance(expected, dict) and not any(part.startswith("$") for part in expected):
            if not isinstance(value, dict) or not _matches(value, expected, verbose=verbose):
                return False
        elif value != expected:
            return False
    return True


def _ensure_list(container: dict[str, Any], key: str, verbose: bool = True) -> list[dict[str, Any]]:
    container.setdefault(key, [])
    return container[key]


class EmbeddedMongoStore:
    def __init__(self, file_path: str | Path, verbose: bool = True):
        self.file_path = Path(file_path)
        self.verbose = verbose
        _vlog(self.verbose, f"store: open {self.file_path}")
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load(verbose=verbose)

    def _load(self, verbose: bool = True) -> dict[str, Any]:
        if not self.file_path.exists():
            return {"collections": {}}
        with self.file_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _save(self, verbose: bool = True) -> None:
        _vlog(self.verbose and verbose, "store: save")
        tmp_path = self.file_path.with_suffix(self.file_path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(self._data, handle, indent=2, ensure_ascii=True)
        tmp_path.replace(self.file_path)

    def collection(self, name: str, verbose: bool = True) -> list[dict[str, Any]]:
        collections = self._data.setdefault("collections", {})
        return _ensure_list(collections, name)

    def insert_one(self, collection: str, document: dict[str, Any], verbose: bool = True) -> dict[str, Any]:
        _vlog(self.verbose and verbose, f"store: insert {collection}")
        doc = deepcopy(document)
        doc.setdefault("_id", uuid.uuid4().hex)
        self.collection(collection, verbose=verbose).append(doc)
        self._save(verbose=verbose)
        return deepcopy(doc)

    def find(self, collection: str, query: dict[str, Any] | None = None, verbose: bool = True) -> list[dict[str, Any]]:
        _vlog(self.verbose and verbose, f"store: find {collection}")
        query = query or {}
        return [deepcopy(doc) for doc in self.collection(collection, verbose=verbose) if _matches(doc, query)]

    def find_one(self, collection: str, query: dict[str, Any] | None = None, verbose: bool = True) -> dict[str, Any] | None:
        _vlog(self.verbose and verbose, f"store: find one {collection}")
        found = self.find(collection, query, verbose=verbose)
        return found[0] if found else None

    def replace_one(
        self,
        collection: str,
        query: dict[str, Any],
        document: dict[str, Any],
        upsert: bool = False,
        verbose: bool = True,
    ) -> dict[str, Any]:
        _vlog(self.verbose and verbose, f"store: replace {collection}")
        docs = self.collection(collection, verbose=verbose)
        for index, existing in enumerate(docs):
            if _matches(existing, query):
                replacement = deepcopy(document)
                replacement.setdefault("_id", existing.get("_id", uuid.uuid4().hex))
                docs[index] = replacement
                self._save(verbose=verbose)
                return deepcopy(replacement)
        if upsert:
            return self.insert_one(collection, document, verbose=verbose)
        raise KeyError("Document not found")

    def update_one(
        self,
        collection: str,
        query: dict[str, Any],
        update: dict[str, Any],
        upsert: bool = False,
        verbose: bool = True,
    ) -> dict[str, Any]:
        _vlog(self.verbose and verbose, f"store: update {collection}")
        docs = self.collection(collection, verbose=verbose)
        for index, existing in enumerate(docs):
            if _matches(existing, query):
                updated = deepcopy(existing)
                for op, payload in update.items():
                    if op == "$set":
                        for key, value in payload.items():
                            target = updated
                            parts = key.split(".")
                            for part in parts[:-1]:
                                target = target.setdefault(part, {})
                            target[parts[-1]] = deepcopy(value)
                    elif op == "$push":
                        for key, value in payload.items():
                            target = updated
                            parts = key.split(".")
                            for part in parts[:-1]:
                                target = target.setdefault(part, {})
                            target.setdefault(parts[-1], []).append(deepcopy(value))
                docs[index] = updated
                self._save(verbose=verbose)
                return deepcopy(updated)
        if upsert:
            base = deepcopy(query)
            for op, payload in update.items():
                if op == "$set":
                    for key, value in payload.items():
                        target = base
                        parts = key.split(".")
                        for part in parts[:-1]:
                            target = target.setdefault(part, {})
                        target[parts[-1]] = deepcopy(value)
            return self.insert_one(collection, base, verbose=verbose)
        raise KeyError("Document not found")

    def save_run(self, run: dict[str, Any], verbose: bool = True) -> dict[str, Any]:
        _vlog(self.verbose and verbose, "store: save run")
        return self.insert_one("runs", run, verbose=verbose)

    def save_stage_output(self, run_id: str, stage: str, payload: dict[str, Any], verbose: bool = True) -> dict[str, Any]:
        _vlog(self.verbose and verbose, f"store: save stage {stage}")
        return self.insert_one(
            "stage_outputs",
            {
                "run_id": run_id,
                "stage": stage,
                "payload": payload,
            },
            verbose=verbose,
        )
