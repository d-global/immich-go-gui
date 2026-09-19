"""Server-side completion verification and targeted repair for Archive Migration.

immich-go's process exit code and event counters are useful diagnostics, but they
are not authoritative proof that assets actually became members of the target
album. Archive Migration therefore verifies the destination album through the
Immich API before a queue item can become DONE.

If verification finds an under-filled album after a successful immich-go run,
a repair pass can resolve the source files to existing Immich asset IDs through
the official bulk-upload-check endpoint and retry only album membership. This
fallback never changes asset visibility, trash state, or metadata.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event

import requests

from .network import normalize_server_url


@dataclass(frozen=True)
class AlbumVerificationResult:
    """Result of checking one target album after an immich-go run."""

    success: bool
    album_name: str
    expected_assets: int
    actual_assets: int | None = None
    album_id: str | None = None
    message: str = ""


@dataclass(frozen=True)
class AlbumRepairResult:
    """Result of a best-effort album-membership repair pass."""

    attempted: bool
    success: bool
    scanned_files: int = 0
    resolved_assets: int = 0
    added_assets: int = 0
    already_present: int = 0
    blocked_assets: int = 0
    trashed_assets: int = 0
    message: str = ""
    details: tuple[str, ...] = ()


RepairLog = Callable[[str], None]


def verify_archive_album(
    server_url: str,
    api_key: str,
    album_name: str,
    expected_assets: int,
    *,
    skip_ssl: bool = False,
    timeout: float = 15.0,
) -> AlbumVerificationResult:
    """Verify that the exact target album contains exactly the expected assets."""

    clean_url = normalize_server_url(server_url)
    expected = max(0, int(expected_assets))
    base = AlbumVerificationResult(
        success=False,
        album_name=album_name,
        expected_assets=expected,
    )

    if not clean_url:
        return _with_message(base, "Album verification failed: server URL is empty")
    if not api_key:
        return _with_message(base, "Album verification failed: API key is empty")
    if not album_name:
        return _with_message(base, "Album verification failed: album name is empty")

    try:
        response = requests.get(
            f"{clean_url}/api/albums",
            headers=_headers(api_key),
            verify=not skip_ssl,
            timeout=timeout,
        )
    except requests.exceptions.SSLError:
        return _with_message(
            base,
            "Album verification failed: SSL certificate verification failed",
        )
    except requests.exceptions.Timeout:
        return _with_message(
            base,
            f"Album verification failed: request timed out after {timeout:g} seconds",
        )
    except requests.exceptions.ConnectionError:
        return _with_message(
            base,
            "Album verification failed: could not connect to Immich",
        )
    except Exception as exc:
        return _with_message(base, f"Album verification failed: {exc}")

    if response.status_code in (401, 403):
        return _with_message(
            base,
            (
                f"Album verification failed: HTTP {response.status_code}. "
                "The API key cannot read albums, so completion cannot be confirmed."
            ),
        )
    if response.status_code != 200:
        return _with_message(
            base,
            f"Album verification failed: Immich returned HTTP {response.status_code}",
        )

    try:
        payload = response.json()
    except Exception:
        return _with_message(
            base,
            "Album verification failed: Immich returned invalid JSON",
        )

    if not isinstance(payload, list):
        return _with_message(
            base,
            "Album verification failed: unexpected albums response",
        )

    matches = [
        album
        for album in payload
        if isinstance(album, dict) and album.get("albumName") == album_name
    ]
    if not matches:
        return _with_message(
            base,
            f"Album verification failed: album '{album_name}' was not found",
        )
    if len(matches) > 1:
        return _with_message(
            base,
            (
                f"Album verification failed: {len(matches)} albums named "
                f"'{album_name}' were found; target is ambiguous"
            ),
        )

    album = matches[0]
    album_id = str(album.get("id") or "") or None
    raw_count = album.get("assetCount")
    try:
        actual = int(raw_count)
    except (TypeError, ValueError):
        return AlbumVerificationResult(
            success=False,
            album_name=album_name,
            expected_assets=expected,
            actual_assets=None,
            album_id=album_id,
            message=(
                "Album verification failed: Immich response has no valid assetCount"
            ),
        )

    if actual != expected:
        return AlbumVerificationResult(
            success=False,
            album_name=album_name,
            expected_assets=expected,
            actual_assets=actual,
            album_id=album_id,
            message=(
                f"Album verification failed: expected {expected} assets, found {actual}"
            ),
        )

    return AlbumVerificationResult(
        success=True,
        album_name=album_name,
        expected_assets=expected,
        actual_assets=actual,
        album_id=album_id,
        message=f"Album verified: {actual} assets",
    )


def repair_archive_album_membership(
    server_url: str,
    api_key: str,
    album_id: str,
    source_folder: str,
    expected_assets: int,
    *,
    skip_ssl: bool = False,
    timeout: float = 30.0,
    batch_size: int = 200,
    cancel_event: Event | None = None,
    on_log: RepairLog | None = None,
) -> AlbumRepairResult:
    """Resolve source files by SHA1 and retry only their album membership.

    The Immich bulk-upload-check endpoint is checksum-based and returns the
    existing asset ID for duplicates owned by the authenticated user. We use
    that official lookup only after normal completion verification failed.

    Assets reported as trashed are not modified. Per-asset `no_permission`
    responses are surfaced explicitly instead of being treated as success.
    """

    clean_url = normalize_server_url(server_url)
    folder = Path(source_folder)
    expected = max(0, int(expected_assets))
    if not clean_url:
        return AlbumRepairResult(False, False, message="Repair skipped: server URL is empty")
    if not api_key:
        return AlbumRepairResult(False, False, message="Repair skipped: API key is empty")
    if not album_id:
        return AlbumRepairResult(False, False, message="Repair skipped: album ID is empty")
    if not folder.is_dir():
        return AlbumRepairResult(
            False,
            False,
            message=f"Repair skipped: source folder does not exist: {source_folder}",
        )

    _emit(on_log, "Album repair: resolving source files by SHA1 through Immich")

    resolved: dict[str, str] = {}
    trashed: dict[str, str] = {}
    details: list[str] = []
    pending: list[dict[str, str]] = []
    scanned_files = 0

    try:
        for path in sorted(folder.rglob("*")):
            if cancel_event is not None and cancel_event.is_set():
                return AlbumRepairResult(
                    True,
                    False,
                    scanned_files=scanned_files,
                    resolved_assets=len(resolved),
                    trashed_assets=len(trashed),
                    message="Album repair cancelled",
                    details=tuple(details),
                )
            if path.is_symlink() or not path.is_file():
                continue

            scanned_files += 1
            relative = path.relative_to(folder).as_posix()
            try:
                checksum = _sha1_hex(path)
            except OSError as exc:
                details.append(f"{relative}: SHA1 read failed: {exc}")
                continue

            pending.append({"id": relative, "checksum": checksum})
            if len(pending) >= max(1, batch_size):
                _resolve_checksum_batch(
                    clean_url,
                    api_key,
                    pending,
                    resolved,
                    trashed,
                    skip_ssl=skip_ssl,
                    timeout=timeout,
                )
                pending.clear()
                _emit(
                    on_log,
                    (
                        f"Album repair: scanned {scanned_files} files, "
                        f"resolved {len(resolved)} assets"
                    ),
                )
                if expected > 0 and len(resolved) + len(trashed) >= expected:
                    break

        if pending and (expected <= 0 or len(resolved) + len(trashed) < expected):
            _resolve_checksum_batch(
                clean_url,
                api_key,
                pending,
                resolved,
                trashed,
                skip_ssl=skip_ssl,
                timeout=timeout,
            )
    except requests.RequestException as exc:
        return AlbumRepairResult(
            True,
            False,
            scanned_files=scanned_files,
            resolved_assets=len(resolved),
            trashed_assets=len(trashed),
            message=f"Album repair lookup failed: {exc}",
            details=tuple(details),
        )

    for asset_id, relative in trashed.items():
        details.append(f"{relative}: existing Immich asset is in trash; repair skipped")

    ids_to_add = list(resolved)
    if not ids_to_add:
        return AlbumRepairResult(
            True,
            False,
            scanned_files=scanned_files,
            resolved_assets=0,
            trashed_assets=len(trashed),
            message=(
                "Album repair could not resolve any active Immich assets "
                "from the source checksums"
            ),
            details=tuple(details),
        )

    try:
        response = requests.put(
            f"{clean_url}/api/albums/{album_id}/assets",
            headers=_headers(api_key),
            json={"ids": ids_to_add},
            verify=not skip_ssl,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return AlbumRepairResult(
            True,
            False,
            scanned_files=scanned_files,
            resolved_assets=len(resolved),
            trashed_assets=len(trashed),
            message=f"Album repair update failed: {exc}",
            details=tuple(details),
        )

    if response.status_code in (401, 403):
        return AlbumRepairResult(
            True,
            False,
            scanned_files=scanned_files,
            resolved_assets=len(resolved),
            trashed_assets=len(trashed),
            message=(
                f"Album repair failed: HTTP {response.status_code}. "
                "The API key cannot add assets to albums."
            ),
            details=tuple(details),
        )
    if response.status_code != 200:
        return AlbumRepairResult(
            True,
            False,
            scanned_files=scanned_files,
            resolved_assets=len(resolved),
            trashed_assets=len(trashed),
            message=f"Album repair failed: Immich returned HTTP {response.status_code}",
            details=tuple(details),
        )

    try:
        payload = response.json()
    except Exception:
        return AlbumRepairResult(
            True,
            False,
            scanned_files=scanned_files,
            resolved_assets=len(resolved),
            trashed_assets=len(trashed),
            message="Album repair failed: Immich returned invalid JSON",
            details=tuple(details),
        )

    if not isinstance(payload, list):
        return AlbumRepairResult(
            True,
            False,
            scanned_files=scanned_files,
            resolved_assets=len(resolved),
            trashed_assets=len(trashed),
            message="Album repair failed: unexpected add-assets response",
            details=tuple(details),
        )

    added = 0
    already = 0
    blocked = 0
    for item in payload:
        if not isinstance(item, dict):
            continue
        asset_id = str(item.get("id") or "")
        relative = resolved.get(asset_id, asset_id or "<unknown>")
        if item.get("success") is True:
            added += 1
            continue

        error = str(item.get("error") or "unknown")
        if error == "duplicate":
            already += 1
        elif error == "no_permission":
            blocked += 1
            details.append(
                (
                    f"{relative}: Immich returned no_permission for AssetShare. "
                    "For a same-user checksum duplicate on Immich v3 this is "
                    "consistent with LOCKED visibility."
                )
            )
        else:
            details.append(f"{relative}: Immich rejected album membership: {error}")

    success = (
        blocked == 0
        and len(trashed) == 0
        and (expected <= 0 or len(resolved) >= expected)
        and added + already >= min(len(resolved), expected or len(resolved))
    )
    message = (
        f"Album repair: resolved {len(resolved)} assets; "
        f"added {added}; already present {already}; "
        f"blocked {blocked}; trashed {len(trashed)}"
    )
    return AlbumRepairResult(
        attempted=True,
        success=success,
        scanned_files=scanned_files,
        resolved_assets=len(resolved),
        added_assets=added,
        already_present=already,
        blocked_assets=blocked,
        trashed_assets=len(trashed),
        message=message,
        details=tuple(details),
    )


def _resolve_checksum_batch(
    clean_url: str,
    api_key: str,
    batch: list[dict[str, str]],
    resolved: dict[str, str],
    trashed: dict[str, str],
    *,
    skip_ssl: bool,
    timeout: float,
) -> None:
    response = requests.post(
        f"{clean_url}/api/assets/bulk-upload-check",
        headers=_headers(api_key),
        json={"assets": batch},
        verify=not skip_ssl,
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        raise requests.RequestException("unexpected bulk-upload-check response")

    for item in results:
        if not isinstance(item, dict):
            continue
        if item.get("action") != "reject" or item.get("reason") != "duplicate":
            continue
        asset_id = str(item.get("assetId") or "")
        relative = str(item.get("id") or "")
        if not asset_id:
            continue
        if item.get("isTrashed") is True:
            trashed.setdefault(asset_id, relative)
        else:
            resolved.setdefault(asset_id, relative)


def _sha1_hex(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _headers(api_key: str) -> dict[str, str]:
    return {
        "x-api-key": api_key,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _emit(callback: RepairLog | None, message: str) -> None:
    if callback is not None:
        callback(message)


def _with_message(
    result: AlbumVerificationResult, message: str
) -> AlbumVerificationResult:
    return AlbumVerificationResult(
        success=result.success,
        album_name=result.album_name,
        expected_assets=result.expected_assets,
        actual_assets=result.actual_assets,
        album_id=result.album_id,
        message=message,
    )
