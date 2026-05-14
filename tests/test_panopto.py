from studylens.ingestion.panopto import (
    extract_session_id,
    find_deep_urls,
    generated_srt_url,
    panopto_session_api_url,
)


def test_extract_session_id_from_panopto_urls() -> None:
    session_id = "12345678-1234-1234-1234-123456789abc"

    assert (
        extract_session_id(f"https://host/Panopto/Pages/Viewer.aspx?id={session_id}")
        == session_id
    )
    assert extract_session_id(f"https://host/foo/{session_id}/bar") == session_id
    assert extract_session_id("https://host/Panopto/Pages/Sessions/List.aspx") is None


def test_find_caption_urls_and_generate_api_urls() -> None:
    session_id = "12345678-1234-1234-1234-123456789abc"
    details = {
        "CaptionDownloadUrl": "/caption.srt",
        "Nested": [{"captionDownloadUrl": "https://host/other.vtt"}],
    }

    assert find_deep_urls(details, ("CaptionDownloadUrl", "captionDownloadUrl")) == [
        "/caption.srt",
        "https://host/other.vtt",
    ]
    assert panopto_session_api_url("https://host/Panopto/Pages/Sessions/List.aspx", session_id) == (
        f"https://host/Panopto/api/v1/sessions/{session_id}"
    )
    assert generated_srt_url("https://host/Panopto/Pages/Sessions/List.aspx", session_id) == (
        f"https://host/Panopto/Pages/Transcription/GenerateSRT.ashx?id={session_id}&language=1"
    )

