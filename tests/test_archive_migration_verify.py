from core.archive_migration_verify import (
    AlbumRepairResult,
    AlbumVerificationResult,
    finalize_archive_album_verification,
    repair_archive_album_membership,
    verify_archive_album,
)


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_verify_archive_album_success(monkeypatch):
    monkeypatch.setattr(
        "core.archive_migration_verify.requests.get",
        lambda *args, **kwargs: _Response(
            200,
            [{"id": "album-1", "albumName": "TEST_Anapa", "assetCount": 4}],
        ),
    )

    result = verify_archive_album(
        "http://immich.test:2283",
        "secret",
        "TEST_Anapa",
        4,
    )

    assert result.success is True
    assert result.actual_assets == 4
    assert result.album_id == "album-1"


def test_verify_archive_album_empty_is_failure(monkeypatch):
    monkeypatch.setattr(
        "core.archive_migration_verify.requests.get",
        lambda *args, **kwargs: _Response(
            200,
            [{"id": "album-1", "albumName": "TEST_Anapa", "assetCount": 0}],
        ),
    )

    result = verify_archive_album(
        "http://immich.test:2283",
        "secret",
        "TEST_Anapa",
        4,
    )

    assert result.success is False
    assert result.actual_assets == 0
    assert result.message == "Album verification failed: expected 4 assets, found 0"


def test_verify_archive_album_rejects_ambiguous_name(monkeypatch):
    monkeypatch.setattr(
        "core.archive_migration_verify.requests.get",
        lambda *args, **kwargs: _Response(
            200,
            [
                {"id": "album-1", "albumName": "Same", "assetCount": 4},
                {"id": "album-2", "albumName": "Same", "assetCount": 4},
            ],
        ),
    )

    result = verify_archive_album(
        "http://immich.test:2283",
        "secret",
        "Same",
        4,
    )

    assert result.success is False
    assert "target is ambiguous" in result.message


def test_verify_archive_album_permission_failure(monkeypatch):
    monkeypatch.setattr(
        "core.archive_migration_verify.requests.get",
        lambda *args, **kwargs: _Response(403, {}),
    )

    result = verify_archive_album(
        "http://immich.test:2283",
        "secret",
        "TEST_Anapa",
        4,
    )

    assert result.success is False
    assert "HTTP 403" in result.message


def test_verify_archive_album_extra_assets_is_failure(monkeypatch):
    monkeypatch.setattr(
        "core.archive_migration_verify.requests.get",
        lambda *args, **kwargs: _Response(
            200,
            [{"id": "album-1", "albumName": "TEST_Anapa", "assetCount": 5}],
        ),
    )

    result = verify_archive_album(
        "http://immich.test:2283",
        "secret",
        "TEST_Anapa",
        4,
    )

    assert result.success is False
    assert result.actual_assets == 5
    assert result.message == "Album verification failed: expected 4 assets, found 5"


def test_repair_resolves_checksums_and_adds_missing_assets(tmp_path, monkeypatch):
    (tmp_path / "old.jpg").write_bytes(b"old")
    (tmp_path / "new.jpg").write_bytes(b"new")
    (tmp_path / "note.xmp").write_bytes(b"sidecar")

    def fake_post(_url, **kwargs):
        results = []
        for item in kwargs["json"]["assets"]:
            if item["id"].endswith(".jpg"):
                asset_id = "asset-old" if item["id"] == "old.jpg" else "asset-new"
                results.append(
                    {
                        "id": item["id"],
                        "action": "reject",
                        "reason": "duplicate",
                        "assetId": asset_id,
                        "isTrashed": False,
                    }
                )
            else:
                results.append({"id": item["id"], "action": "accept"})
        return _Response(200, {"results": results})

    def fake_put(_url, **kwargs):
        assert set(kwargs["json"]["ids"]) == {"asset-old", "asset-new"}
        return _Response(
            200,
            [
                {"id": "asset-old", "success": True},
                {"id": "asset-new", "success": False, "error": "duplicate"},
            ],
        )

    monkeypatch.setattr("core.archive_migration_verify.requests.post", fake_post)
    monkeypatch.setattr("core.archive_migration_verify.requests.put", fake_put)

    result = repair_archive_album_membership(
        "http://immich.test:2283",
        "secret",
        "album-1",
        str(tmp_path),
        2,
    )

    assert result.attempted is True
    assert result.success is True
    assert result.resolved_assets == 2
    assert result.added_assets == 1
    assert result.already_present == 1
    assert result.blocked_assets == 0


def test_repair_surfaces_no_permission_per_file(tmp_path, monkeypatch):
    (tmp_path / "locked.jpg").write_bytes(b"locked")

    monkeypatch.setattr(
        "core.archive_migration_verify.requests.post",
        lambda *_args, **_kwargs: _Response(
            200,
            {
                "results": [
                    {
                        "id": "locked.jpg",
                        "action": "reject",
                        "reason": "duplicate",
                        "assetId": "asset-locked",
                        "isTrashed": False,
                    }
                ]
            },
        ),
    )
    monkeypatch.setattr(
        "core.archive_migration_verify.requests.put",
        lambda *_args, **_kwargs: _Response(
            200,
            [
                {
                    "id": "asset-locked",
                    "success": False,
                    "error": "no_permission",
                }
            ],
        ),
    )

    result = repair_archive_album_membership(
        "http://immich.test:2283",
        "secret",
        "album-1",
        str(tmp_path),
        1,
    )

    assert result.success is False
    assert result.blocked_assets == 1
    assert "locked.jpg" in result.details[0]
    assert "LOCKED" in result.details[0]


def test_repair_does_not_add_trashed_assets(tmp_path, monkeypatch):
    (tmp_path / "trashed.jpg").write_bytes(b"trashed")
    put_called = False

    monkeypatch.setattr(
        "core.archive_migration_verify.requests.post",
        lambda *_args, **_kwargs: _Response(
            200,
            {
                "results": [
                    {
                        "id": "trashed.jpg",
                        "action": "reject",
                        "reason": "duplicate",
                        "assetId": "asset-trash",
                        "isTrashed": True,
                    }
                ]
            },
        ),
    )

    def fake_put(*_args, **_kwargs):
        nonlocal put_called
        put_called = True
        return _Response(200, [])

    monkeypatch.setattr("core.archive_migration_verify.requests.put", fake_put)

    result = repair_archive_album_membership(
        "http://immich.test:2283",
        "secret",
        "album-1",
        str(tmp_path),
        1,
    )

    assert result.success is False
    assert result.trashed_assets == 1
    assert put_called is False
    assert "trash" in result.details[0].lower()


def test_repair_can_restore_only_checksum_matched_trashed_assets(tmp_path, monkeypatch):
    (tmp_path / "trashed.jpg").write_bytes(b"trashed")
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs["json"]))
        if url.endswith("/api/assets/bulk-upload-check"):
            return _Response(
                200,
                {
                    "results": [
                        {
                            "id": "trashed.jpg",
                            "action": "reject",
                            "reason": "duplicate",
                            "assetId": "asset-trash",
                            "isTrashed": True,
                        }
                    ]
                },
            )
        if url.endswith("/api/trash/restore/assets"):
            assert kwargs["json"] == {"ids": ["asset-trash"]}
            return _Response(200, {"count": 1})
        raise AssertionError(url)

    def fake_put(_url, **kwargs):
        assert kwargs["json"] == {"ids": ["asset-trash"]}
        return _Response(200, [{"id": "asset-trash", "success": True}])

    monkeypatch.setattr("core.archive_migration_verify.requests.post", fake_post)
    monkeypatch.setattr("core.archive_migration_verify.requests.put", fake_put)

    result = repair_archive_album_membership(
        "http://immich.test:2283",
        "secret",
        "album-1",
        str(tmp_path),
        1,
        restore_trashed=True,
    )

    assert result.success is True
    assert result.trashed_assets == 0
    assert result.restored_assets == 1
    assert result.added_assets == 1
    assert any("restored from Immich trash" in detail for detail in result.details)
    assert len(calls) == 2


def test_repair_reports_missing_delete_permission_for_targeted_restore(
    tmp_path, monkeypatch
):
    (tmp_path / "trashed.jpg").write_bytes(b"trashed")

    def fake_post(url, **_kwargs):
        if url.endswith("/api/assets/bulk-upload-check"):
            return _Response(
                200,
                {
                    "results": [
                        {
                            "id": "trashed.jpg",
                            "action": "reject",
                            "reason": "duplicate",
                            "assetId": "asset-trash",
                            "isTrashed": True,
                        }
                    ]
                },
            )
        return _Response(403, {})

    monkeypatch.setattr("core.archive_migration_verify.requests.post", fake_post)

    result = repair_archive_album_membership(
        "http://immich.test:2283",
        "secret",
        "album-1",
        str(tmp_path),
        1,
        restore_trashed=True,
    )

    assert result.success is False
    assert result.trashed_assets == 1
    assert "asset.delete permission" in result.message


def test_finalize_accepts_verified_membership_with_extra_album_assets():
    initial = AlbumVerificationResult(
        success=False,
        album_name="Абрау",
        expected_assets=20,
        actual_assets=40,
        album_id="album-1",
        message="Album verification failed: expected 20 assets, found 40",
    )
    repair = AlbumRepairResult(
        attempted=True,
        success=True,
        resolved_assets=20,
        already_present=20,
    )

    result = finalize_archive_album_verification(initial, repair)

    assert result.success is True
    assert result.actual_assets == 40
    assert "20/20 source assets" in result.message
    assert "20 extra assets" in result.message


def test_finalize_keeps_failure_when_membership_repair_did_not_prove_source():
    initial = AlbumVerificationResult(
        success=False,
        album_name="Абрау",
        expected_assets=20,
        actual_assets=40,
        album_id="album-1",
        message="Album verification failed: expected 20 assets, found 40",
    )
    repair = AlbumRepairResult(
        attempted=True,
        success=False,
        resolved_assets=10,
        already_present=10,
    )

    assert finalize_archive_album_verification(initial, repair) == initial


def test_finalize_repair_failure_overrides_matching_count():
    final = AlbumVerificationResult(
        success=True,
        album_name="Existing",
        expected_assets=20,
        actual_assets=20,
        album_id="album-1",
        message="Album verified: 20 assets",
    )
    repair = AlbumRepairResult(
        attempted=True,
        success=False,
        resolved_assets=10,
        trashed_assets=10,
        message="Album repair: 10 source assets are still in trash",
    )

    result = finalize_archive_album_verification(final, repair)

    assert result.success is False
    assert "still in trash" in result.message
