from core.folder_runner import UploadResult, _tally_report_line


def test_tally_parses_archive_verification_diagnostics():
    result = UploadResult(folder="TEST_Anapa", success=False)

    _tally_report_line(
        "Immich read 100%, Assets found: 4, Upload errors: 0, Uploaded 0",
        result,
    )
    _tally_report_line(
        "server has duplicate               :       4  (459.8 KB)", result
    )
    _tally_report_line("added to album                     :       4", result)

    assert result.assets_found == 4
    assert result.files_errored == 0
    assert result.files_uploaded == 0
    assert result.files_skipped == 4
    assert result.album_added == 4
