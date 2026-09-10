#!/usr/bin/env python3
"""Manifest helper for the Obsidian wiki — normalize paths and compute ingest deltas.

Pure stdlib, no dependencies. Optional accelerator for the ingest skills: the
markdown instructions still work without it, but this makes the manifest steps
deterministic and testable.

Source keys in `.manifest.json` follow the portable key contract (see
`.skills/llm-wiki/SKILL.md`): vault-relative for in-vault sources
(`Raw/x.pdf`), home-relative for sources under `$HOME` (`~/.claude/...`), or a
namespaced pseudo-key (`repo:`/`url:`/`agent:`/`src:`) for sources with no
filesystem representation in either form. Bare machine absolute paths are legacy
and still read for backward compatibility; `migrate` rewrites them.

Usage:
  # Rewrite legacy absolute keys to the portable form, merging collisions.
  python3 scripts/manifest.py migrate <vault_path> [--dry-run]
  # Alias kept for older instructions; behaves like migrate.
  python3 scripts/manifest.py normalize <vault_path> [--dry-run]

  # List new/modified sources under a glob that aren't in the manifest yet.
  # Honors $WIKI_SKIP_PROJECTS (comma-separated substrings) plus --skip.
  python3 scripts/manifest.py delta <vault_path> --scan '<glob>' [--skip a,b]
"""
from __future__ import annotations

import argparse
import glob as globmod
import json
import os
import re
import sys
from pathlib import Path


def canonical(path: str) -> str:
    """Absolute form with `~` and env vars expanded (used for path resolution)."""
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path)))


# Matches "sha256:", "https://", "repo:..." — a scheme prefix, not a file path.
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:[^\\/]")


def _is_file_key(key: str | None) -> bool:
    """True if *key* looks like a filesystem path rather than a URL/pseudo-key."""
    return bool(key) and "://" not in key and not _SCHEME_RE.match(key)


def _expand_key(key: str) -> str:
    """Expand ``~``/env vars, but only when the key actually uses them."""
    if key.startswith("~") or "$" in key:
        return os.path.expandvars(os.path.expanduser(key))
    return key


def resolve_key(key: str | None, vault: str) -> str | None:
    """Normalize a manifest key to an absolute path, or ``None`` for pseudo-keys.

    Order: pseudo-key -> None; ``~``/env -> expand; absolute -> as-is; else
    resolve against the vault root. Mirrors ``obsidian_wiki.cache.resolve_key``.
    """
    if not _is_file_key(key):
        return None
    path = Path(_expand_key(key))
    return str(path if path.is_absolute() else (Path(vault) / path))


def stored_key(path: str, vault: str) -> str | None:
    """Portable key for *path*: vault-relative, ``~``-relative, or ``None``.

    ``None`` means the path has no portable representation and the caller must
    supply an explicit pseudo-key instead of letting an absolute path be stored.
    """
    p = Path(canonical(path))
    vault_root = Path(canonical(vault))
    home_root = Path(os.path.expanduser("~"))
    for root, prefix in ((vault_root, ""), (home_root, "~/")):
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        if rel == Path("."):
            return None
        return prefix + rel.as_posix()
    return None


def manifest_path(vault: str) -> str:
    return os.path.join(canonical(vault), ".manifest.json")


def load_manifest(vault: str) -> dict:
    mp = manifest_path(vault)
    if not os.path.exists(mp):
        return {"version": 1, "sources": {}, "projects": {}, "stats": {}}
    with open(mp) as f:
        return json.load(f)


def _newest(a: dict, b: dict) -> dict:
    """Merge two entries for the same file, preferring the newer ingested_at and
    unioning the pages_created / pages_updated / pages_produced lists."""
    keep = a
    other = b
    if str(b.get("ingested_at", "")) > str(a.get("ingested_at", "")):
        keep, other = b, a
    merged = dict(keep)
    for field in ("pages_created", "pages_updated", "pages_produced"):
        union = list(dict.fromkeys((other.get(field) or []) + (keep.get(field) or [])))
        if union:
            merged[field] = union
    return merged


def cmd_migrate(args: argparse.Namespace) -> int:
    """Rewrite legacy absolute keys to the portable key form.

    In-vault keys become vault-relative and `$HOME` keys become `~`-relative.
    Pseudo-keys and legacy ingest-root-relative keys are preserved untouched.
    An absolute path with no portable form (outside the vault and `$HOME`) is
    kept as-is with a warning rather than dropped, so provenance is never lost.
    """
    m = load_manifest(args.vault)
    sources = m.get("sources", {})
    new_sources: dict = {}
    collisions = 0
    rekeyed = 0
    non_portable = 0
    for key, entry in sources.items():
        portable = False
        if not _is_file_key(key):
            ckey = key  # pseudo-key — opaque identity, keep verbatim
        elif os.path.isabs(key):
            ckey = stored_key(key, args.vault)
            if ckey is None:
                ckey = canonical(key)
                non_portable += 1
                print(f"  WARN   no portable form (kept absolute): {key}")
            else:
                portable = True
        elif key.startswith("~") or "$" in key:
            ckey = stored_key(os.path.expanduser(os.path.expandvars(key)), args.vault) or key
            portable = ckey != key
        else:
            # Relative keys are already vault-relative under contract v2. Legacy
            # keys relative to an ingest root outside the vault (e.g.
            # "-Users-x-github/abc.jsonl" under ~/.claude/projects/) also land
            # here; they are indistinguishable without the file and re-resolving
            # them against the vault/CWD would corrupt them, so preserve as-is.
            ckey = key

        if portable and ckey != key:
            rekeyed += 1
        if ckey in new_sources:
            new_sources[ckey] = _newest(new_sources[ckey], entry)
            collisions += 1
            print(f"  MERGE  {ckey}")
        else:
            new_sources[ckey] = entry

    print(
        f"sources: {len(sources)} -> {len(new_sources)} "
        f"({rekeyed} re-keyed, {collisions} collisions merged, "
        f"{non_portable} kept non-portable)"
    )
    if args.dry_run:
        print("(dry-run — no changes written)")
        return 0
    if collisions == 0 and rekeyed == 0:
        print("already portable — nothing to write")
        return 0
    m["sources"] = new_sources
    mp = manifest_path(args.vault)
    with open(mp, "w") as f:
        json.dump(m, f, indent=2)
        f.write("\n")
    print(f"wrote {mp}")
    return 0


def _skip_patterns(cli_skip: str | None) -> list[str]:
    pats: list[str] = []
    env = os.environ.get("WIKI_SKIP_PROJECTS", "")
    for raw in (env, cli_skip or ""):
        pats.extend(p.strip() for p in raw.split(",") if p.strip())
    return pats


def _normalize_for_match(path: str) -> str:
    """Normalize either slash style for host-independent path comparison."""
    portable = path.replace("\\", os.sep).replace("/", os.sep)
    return os.path.normcase(os.path.normpath(portable))


def _relative_key_index(sources: dict) -> dict[str, list[tuple[str, dict]]]:
    """Index relative source keys by basename for suffix matching.

    Real vaults store many keys relative to the ingest root (e.g.
    "-Users-x-github/abc.jsonl" under ~/.claude/projects/). canonical()
    resolves those against the CWD, so they never equal a scanned absolute
    path. Keying by basename lets cmd_delta fall back to an O(1) suffix check
    instead of scanning every key per file.
    """
    index: dict[str, list[tuple[str, dict]]] = {}
    for k, v in sources.items():
        if not os.path.isabs(k):
            basename = os.path.basename(_normalize_for_match(k))
            index.setdefault(basename, []).append((k, v))
    return index


def _match_relative(path: str, index: dict[str, list[tuple[str, dict]]]) -> dict | None:
    """Return the manifest entry whose relative key is a suffix of `path`."""
    normalized_path = _normalize_for_match(path)
    basename = os.path.basename(normalized_path)
    for relkey, entry in index.get(basename, ()):
        normalized_relkey = _normalize_for_match(relkey)
        if normalized_path == normalized_relkey or normalized_path.endswith(
            os.sep + normalized_relkey
        ):
            return entry
    return None


def cmd_delta(args: argparse.Namespace) -> int:
    m = load_manifest(args.vault)
    sources = m.get("sources", {})
    # Resolve every stored key to an absolute path so vault-relative and
    # home-relative keys match a scanned absolute path directly. Pseudo-keys
    # have no path form and are matched only on the raw string (they never equal
    # a scanned path, but keeping them preserves the "known" count).
    known: dict[str, dict] = {}
    for k, v in sources.items():
        resolved = resolve_key(k, canonical(args.vault))
        known[resolved if resolved is not None else k] = v
    rel_index = _relative_key_index(sources)
    skips = _skip_patterns(args.skip)

    matched = sorted(globmod.glob(os.path.expanduser(args.scan), recursive=True))
    new, modified, skipped = [], [], 0
    for path in matched:
        if not os.path.isfile(path):
            continue
        if any(s in path for s in skips):
            skipped += 1
            continue
        ckey = canonical(path)
        entry = known.get(ckey)
        if entry is None:
            entry = _match_relative(path, rel_index)
        if entry is None:
            new.append(ckey)
        else:
            mtime = os.path.getmtime(path)
            ingested = str(entry.get("ingested_at", ""))
            # modified if file changed after it was last ingested
            from datetime import datetime, timezone

            try:
                ing_ts = datetime.fromisoformat(ingested.replace("Z", "+00:00")).timestamp()
            except ValueError:
                ing_ts = 0
            if mtime > ing_ts:
                modified.append(ckey)

    if skips:
        print(f"# skip patterns: {', '.join(skips)} ({skipped} files skipped)")
    print(f"# {len(new)} new, {len(modified)} modified, {len(known)} known")
    for p in new:
        print(f"NEW\t{p}")
    for p in modified:
        print(f"MOD\t{p}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    mg = sub.add_parser("migrate", help="rewrite legacy absolute keys to the portable form")
    mg.add_argument("vault", help="path to the Obsidian vault (contains .manifest.json)")
    mg.add_argument("--dry-run", action="store_true", help="preview without writing")
    mg.set_defaults(func=cmd_migrate)

    # `normalize` predates the portable-key contract; keep it as an alias so
    # older skill instructions keep working.
    n = sub.add_parser("normalize", help="alias for migrate")
    n.add_argument("vault", help="path to the Obsidian vault (contains .manifest.json)")
    n.add_argument("--dry-run", action="store_true", help="preview without writing")
    n.set_defaults(func=cmd_migrate)

    d = sub.add_parser("delta", help="list new/modified sources vs the manifest")
    d.add_argument("vault", help="path to the Obsidian vault")
    d.add_argument("--scan", required=True, help="glob of source files (use ** with recursive)")
    d.add_argument("--skip", default=None, help="comma-separated substrings to exclude")
    d.set_defaults(func=cmd_delta)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
