from pathlib import Path

from gtnh_rag.config import Config, load_config


def test_defaults_when_file_missing(tmp_path):
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg.chat_url == "https://openrouter.ai/api/v1/chat/completions"
    assert cfg.chat_model == "google/gemma-4-31b-it"
    assert cfg.chat_provider == ["parasail/fp8"]
    assert cfg.embed_model == "bge-m3"
    assert cfg.data_dir == Path("data")
    assert cfg.vault_dir is None
    assert cfg.vps_questbook == ""
    assert cfg.nerd_handlers == []


def test_reads_file_and_paths(tmp_path):
    p = tmp_path / "gtnh_rag.toml"
    p.write_text(
        'chat_model = "x"\nembed_url = "http://gpu:11434"\ndata_dir = "/tmp/d"\nnerd_handlers = ["a", "b"]\n'
    )
    cfg = load_config(p)
    assert isinstance(cfg, Config)
    assert cfg.chat_model == "x"
    assert cfg.embed_url == "http://gpu:11434"
    assert cfg.data_dir == Path("/tmp/d")
    assert cfg.nerd_handlers == ["a", "b"]
    assert cfg.chat_url == "https://openrouter.ai/api/v1/chat/completions"  # untouched default


def test_chat_provider_overridable(tmp_path):
    p = tmp_path / "gtnh_rag.toml"
    p.write_text('chat_provider = ["other/provider"]\n')
    assert load_config(p).chat_provider == ["other/provider"]


def test_vault_dir_unset_by_empty_string(tmp_path):
    p = tmp_path / "gtnh_rag.toml"
    p.write_text('vault_dir = ""\n')
    assert load_config(p).vault_dir is None


def test_vault_dir_set_from_toml(tmp_path):
    p = tmp_path / "gtnh_rag.toml"
    notes = tmp_path / "notes"
    p.write_text(f'vault_dir = "{notes}"\n')
    assert load_config(p).vault_dir == notes


def test_env_var_overrides_data_dir(tmp_path, monkeypatch):
    p = tmp_path / "gtnh_rag.toml"
    p.write_text('data_dir = "/tmp/from-toml"\n')
    monkeypatch.setenv("GTNH_RAG_DATA_DIR", str(tmp_path / "from-env"))
    cfg = load_config(p)
    assert cfg.data_dir == tmp_path / "from-env"


def test_malformed_toml_raises_runtime_error(tmp_path):
    p = tmp_path / "gtnh_rag.toml"
    p.write_text("this is not = valid [toml")
    try:
        load_config(p)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert str(p) in str(exc)
