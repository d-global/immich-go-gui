from core.archive_migration_verify import verify_archive_album


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


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
