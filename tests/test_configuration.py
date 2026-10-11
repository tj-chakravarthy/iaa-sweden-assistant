from types import SimpleNamespace

import pytest

from iaa.core.config import Settings
from iaa.core.llm import client
from iaa.core.policy import llm_personal_data_allowed


def settings(**values):
    return Settings(_env_file=None, register_sheet_id="synthetic", inbox_folder_id="synthetic", **values)


def test_unset_optional_workflow_variables_are_accepted():
    configured = settings(llm_provider="", llm_model="")
    assert configured.llm_provider == "none" and configured.llm_model
    assert client(configured) is None


def test_model_backends_are_explicit(monkeypatch):
    import google

    calls = []
    fake = SimpleNamespace(Client=lambda **kwargs: calls.append(kwargs) or object())
    monkeypatch.setattr(google, "genai", fake, raising=False)
    credentials = object()
    monkeypatch.setattr("iaa.core.auth.credentials", lambda raw, scopes: credentials)
    client(settings(llm_provider="gemini", gemini_api_key="synthetic"))
    assert calls[-1]["vertexai"] is False and calls[-1]["api_key"] == "synthetic"
    client(
        settings(
            llm_provider="vertex",
            vertex_project="synthetic-project",
            vertex_location="europe-west4",
            vertex_sa_json="synthetic",
        )
    )
    assert calls[-1]["vertexai"] is True and calls[-1]["project"] == "synthetic-project"
    assert calls[-1]["credentials"] is credentials and "api_key" not in calls[-1]


def test_incomplete_vertex_configuration_cannot_fall_back_to_personal_key():
    with pytest.raises(ValueError, match="Vertex requires"):
        client(settings(llm_provider="vertex", gemini_api_key="synthetic"))


def test_personal_data_flag_requires_association_backend(monkeypatch):
    monkeypatch.setenv("IAA_LLM_PERSONAL_DATA", "true")
    monkeypatch.setenv("IAA_LLM_PROVIDER", "gemini")
    assert not llm_personal_data_allowed()
    monkeypatch.setenv("IAA_LLM_PROVIDER", "vertex")
    for name in ("IAA_VERTEX_PROJECT", "IAA_VERTEX_LOCATION", "IAA_VERTEX_SA_JSON"):
        monkeypatch.delenv(name, raising=False)
    assert not llm_personal_data_allowed()
    for name in ("IAA_VERTEX_PROJECT", "IAA_VERTEX_LOCATION", "IAA_VERTEX_SA_JSON"):
        monkeypatch.setenv(name, "synthetic")
    assert llm_personal_data_allowed()
