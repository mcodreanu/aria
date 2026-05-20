import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import tools
from sysinfo import files_upload
from sysinfo.conversations import ConversationStore
import sysinfo.prefs as prefs
from memory import Memory
from aria_brain import process
import search
from sysinfo import actions, tasks, typed_memory, workspace_index, plugins
import aria_brain


def test_file_sandbox_blocks_parent_traversal():
    result = tools.read_file_tool("../../secret.txt")
    assert "outside ARIA's allowed workspace" in result or "Absolute paths are disabled" in result


def test_file_sandbox_allows_workspace_file(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "WORKSPACE_ROOT", tmp_path)
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")
    result = tools.read_file_tool("note.txt")
    assert "hello" in result


def test_unknown_app_is_not_launched():
    result = tools.open_app("definitely-not-real")
    assert "I don't know how to open" in result


def test_upload_metadata_hides_path(tmp_path, monkeypatch):
    monkeypatch.setattr(files_upload, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(files_upload, "UPLOAD_META_FILE", tmp_path / ".uploads.json")
    meta = files_upload.save_upload("../secret.txt", b"hello")
    assert "path" not in meta
    stored = files_upload.get_upload_meta(meta["id"])
    assert stored is not None
    assert stored["name"] == "secret.txt"
    assert Path(stored["path"]).parent == tmp_path


def test_prefs_reject_bad_theme():
    try:
        prefs.update({"theme": "neon"})
    except ValueError as exc:
        assert "theme" in str(exc)
    else:
        raise AssertionError("bad theme should raise")


def test_conversation_store_crud_search_and_summary(tmp_path):
    store = ConversationStore(tmp_path / "conversations.db")
    sid = store.new_session()
    store.add(sid, "user", "remember the blue notebook")
    store.add(sid, "aria", "Noted.")
    assert store.search("blue")
    store.upsert_summary(sid, "User mentioned a blue notebook.")
    assert "blue notebook" in store.summary(sid)
    assert len(store.session_messages(sid, limit=1)) == 1


def test_time_intent_does_not_match_plain_word_runtime():
    response = process("Tell me about runtime complexity", Memory())
    assert not response.startswith("It's ")


def test_what_time_is_it_hits_time_handler():
    response = process("What time is it?", Memory())
    assert response.startswith("It's ")


def test_wikipedia_tries_direct_summary_before_search(monkeypatch):
    calls = []

    def fake_summary(title):
        calls.append(title)
        return "direct summary"

    monkeypatch.setattr(search, "wikipedia_summary", fake_summary)
    assert search.wikipedia_search_and_summarize("albert einstein") == "direct summary"
    assert calls == ["Albert einstein"]


def test_typed_memory_crud_and_forget(tmp_path, monkeypatch):
    monkeypatch.setattr(typed_memory, "TYPED_MEMORY_FILE", tmp_path / "typed.json")
    rec = typed_memory.add_record("preference", "editor", "likes compact UI")
    assert rec["type"] == "preference"
    assert typed_memory.list_records("preference")[0]["value"] == "likes compact UI"
    assert typed_memory.forget_by_query("preference", "compact") == 1
    assert typed_memory.list_records("preference") == []


def test_task_crud(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, "TASKS_FILE", tmp_path / "tasks.json")
    task = tasks.add_task("write tests")
    assert task["status"] == "todo"
    updated = tasks.update_task(task["id"], {"status": "done"})
    assert updated["status"] == "done"
    assert tasks.delete_task(task["id"]) is True


def test_risky_file_command_creates_pending_action(tmp_path, monkeypatch):
    monkeypatch.setattr(actions, "ACTIONS_FILE", tmp_path / "actions.json")
    monkeypatch.setattr(actions, "TRUST_FILE", tmp_path / "trust.json")
    response = aria_brain.process("create file note.txt with content hello", Memory())
    assert response.startswith(aria_brain.ACTION_PENDING_PREFIX)
    pending = actions.list_actions("pending")
    assert pending and pending[0]["payload"]["tool"] == "files.write"


def test_approved_action_trusts_matching_future_action(tmp_path, monkeypatch):
    monkeypatch.setattr(actions, "ACTIONS_FILE", tmp_path / "actions.json")
    monkeypatch.setattr(actions, "TRUST_FILE", tmp_path / "trust.json")
    monkeypatch.setattr(tools, "WORKSPACE_ROOT", tmp_path)
    action = actions.create_action(
        "files.write",
        "Create note",
        {"tool": "files.write", "args": {"filename": "note.txt", "content": "hello"}},
        "write-local",
    )
    approved = actions.approve_action(action["id"])
    assert approved["status"] == "approved"
    second = actions.create_action(
        "files.write",
        "Create note again",
        {"tool": "files.write", "args": {"filename": "note.txt", "content": "again"}},
        "write-local",
    )
    assert second["status"] == "approved"
    assert second["auto_approved"] is True


def test_workspace_index_respects_root(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "note.txt").write_text("needle here", encoding="utf-8")
    monkeypatch.setattr(workspace_index, "WORKSPACE_ROOT", root)
    monkeypatch.setattr(workspace_index, "INDEX_FILE", tmp_path / "index.json")
    data = workspace_index.build_index()
    assert data["files"][0]["path"] == "note.txt"
    assert workspace_index.search("needle")[0]["path"] == "note.txt"


def test_plugin_manifest_validation(tmp_path, monkeypatch):
    pdir = tmp_path / "plugins" / "demo"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(
        '{"id":"demo","name":"Demo","permissions":["read_only","bad"],"commands":["demo"],"enabled":true}',
        encoding="utf-8",
    )
    monkeypatch.setattr(plugins, "PLUGIN_DIR", tmp_path / "plugins")
    monkeypatch.setattr(plugins, "PLUGIN_STATE_FILE", tmp_path / "state.json")
    listed = plugins.list_plugins()
    assert listed[0]["permissions"] == ["read_only"]


def test_diagnostics_shape():
    from fastapi.testclient import TestClient
    import main

    client = TestClient(main.app)
    data = client.get("/diagnostics").json()
    assert "python" in data and "stt" in data and "actions" in data
