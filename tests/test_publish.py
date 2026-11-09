"""publish() must not skip its shrink guard because the Hub was unreachable."""

import json

import pytest

from dcprocure import publish as pub


def _raw_dir(tmp_path):
    raw = tmp_path / "raw"
    for name in pub.FILES:
        p = raw / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps([{"id": "a"}]) if name in pub.GROWS_ONLY else "{}")
    return raw


def test_hub_error_on_manifest_stops_publish_instead_of_skipping_the_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "x")
    requested = []

    def fake_download(repo_id, filename, **kw):
        requested.append(filename)
        raise ConnectionError("hub unreachable")

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_download)
    monkeypatch.setattr(pub, "_load_dotenv", lambda: None)
    with pytest.raises(ConnectionError):
        pub.publish(_raw_dir(tmp_path), "owner/repo")
    # It stopped at the manifest; it did not go on to read the card and commit.
    assert requested == ["archive/manifest.json"]


def test_missing_manifest_means_first_publish(tmp_path, monkeypatch):
    from huggingface_hub.errors import EntryNotFoundError

    monkeypatch.setenv("HF_TOKEN", "x")
    requested = []

    def fake_download(repo_id, filename, **kw):
        requested.append(filename)
        if filename == "archive/manifest.json":
            raise EntryNotFoundError("404")
        raise ConnectionError("stop after the guard")

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_download)
    monkeypatch.setattr(pub, "_load_dotenv", lambda: None)
    with pytest.raises(ConnectionError):
        pub.publish(_raw_dir(tmp_path), "owner/repo")
    assert requested == ["archive/manifest.json", "README.md"]
