"""Server-side completion verification for Archive Migration.

immich-go's process exit code and event counters are useful diagnostics, but they
are not authoritative proof that assets actually became members of the target
album. Archive Migration therefore verifies the destination album through the
Immich API before a queue item can become DONE.
"""

from __future__ import annotations

from dataclasses import dataclass

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


def verify_archive_album(
    server_url: str,
    api_key: str,
    album_name: str,
    expected_assets: int,
    *,
    skip_ssl: bool = False,
    timeout: float = 15.0,
) -> AlbumVerificationResult:
    """Verify that the exact target album exists and contains exactly the expected number of assets.

    The list-albums endpoint is intentionally used without version-specific
    query parameters so the check stays compatible across Immich V2/V3. An
    exact-name match is required. Multiple exact matches are treated as
    ambiguous instead of guessing which album immich-go targeted.
    """

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
            headers={"x-api-key": api_key, "Accept": "application/json"},
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
                "Album verification failed: expected "
                f"{expected} assets, found {actual}"
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
