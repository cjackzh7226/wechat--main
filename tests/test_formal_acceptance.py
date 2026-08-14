from __future__ import annotations

import os
from pathlib import Path
import json
import sys
import tempfile
from types import SimpleNamespace

import pytest

import app_paths
from chat_identity import ChatIdentity, ChatObservation
from managed_reply import ReplyDecision
from managed_runtime import (
    ManagedPhase,
    ManagedReplyCoordinator,
    RuntimeAdapters,
    RuntimeOutcome,
    SendAttempt,
)
from release_manifest import (
    run_package_check,
    run_runtime_check,
    scan_private_files,
    write_release_manifest,
)
from send_ledger import SendLedger
from tools.knowledge_audit import audit_memory
from build_release import install_release_assets, prepare_release_dir
from version import RUNTIME_DEPENDENCIES, __version__


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COLLECTION_DATA_DIR = app_paths.data_dir()
COLLECTION_TEMP = Path(os.environ.get("TEMP", tempfile.gettempdir())).resolve()
COLLECTION_TMP = Path(os.environ.get("TMP", tempfile.gettempdir())).resolve()
COLLECTION_PYCACHE = Path(sys.pycache_prefix).resolve() if sys.pycache_prefix else None


def test_test_environment_is_isolated_before_test_module_collection(
    isolated_data_root: Path,
) -> None:
    formal_root = (Path(os.environ["APPDATA"]) / app_paths.APP_NAME).resolve()
    session_root = isolated_data_root.parent.resolve()

    assert COLLECTION_DATA_DIR.resolve() == isolated_data_root.resolve()
    assert COLLECTION_DATA_DIR.resolve() != formal_root
    assert COLLECTION_TEMP == (session_root / "temp").resolve()
    assert COLLECTION_TMP == (session_root / "temp").resolve()
    assert COLLECTION_PYCACHE == (session_root / "pycache").resolve()


def test_no_runtime_files_appear_in_repository_root() -> None:
    forbidden = {
        "credentials.dat",
        "managed_send.log",
        "memory.json",
        "runtime-log.salt",
        "send_ledger.json",
        "settings.json",
        "vector_memory.json",
    }

    assert forbidden.isdisjoint({item.name for item in PROJECT_ROOT.iterdir()})


def _formal_source(tmp_path: Path) -> Path:
    source = (
        PROJECT_ROOT
        / "dist"
        / "聊小智_开发测试_20260803_v4"
        / "聊小智"
        / "user_data"
        / "memory.json"
    )
    if source.is_file():
        return source
    archived = json.loads(
        (
            PROJECT_ROOT
            / "artifacts"
            / "formal-hardening"
            / "2026-08-13"
            / "knowledge-migration-dry-run.json"
        ).read_text(encoding="utf-8")
    )
    reconstructed = tmp_path / "reconstructed-formal-source.json"
    reconstructed.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "style_summary": "",
                "examples": [item["original"] for item in archived["archive_records"]],
                "common_phrases": {},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return reconstructed


def test_knowledge_audit_covers_all_449_source_rows_and_integrity(
    tmp_path: Path,
) -> None:
    report = audit_memory(_formal_source(tmp_path), expected_source_count=449)

    assert report["source_count"] == 449
    assert report["output_count"] == 435
    assert report["exact_duplicate_count"] == 14
    assert report["conflict_group_count"] == 19
    assert report["conflict_record_count"] == 40
    assert report["archive_count"] == 449
    assert report["missing_fields"] == {}
    assert report["hash_integrity_errors"] == []
    assert report["recall"]["known_match"]["answer"] == (
        "进入福建省网上办事大厅用户登录页面，找到“忘记密码”按操作找回。"
    )
    assert report["recall"]["no_match"]["candidate_count"] == 0


def _formal_runtime_fault(tmp_path: Path, fault: str):
    identity = ChatIdentity(501, "-", "personal")
    observation = ChatObservation(
        identity,
        "[对方] 如果账号密码忘记怎么办？",
        ("如果账号密码忘记怎么办？",),
        captured_at=1.0,
        foreground=True,
    )
    decision = ReplyDecision(
        kind="answer",
        text="进入福建省网上办事大厅用户登录页面，找到“忘记密码”按操作找回。",
        record_id="formal-record",
        answer_hash="a" * 64,
    )
    sender_calls: list[str] = []

    def decide(_question: str, _context: str):
        if fault in {"memory_corrupt", "api_timeout"}:
            raise RuntimeError(fault)
        if fault == "api_invalid_json":
            return "invalid-json"
        return decision

    def fresh(_expected: ChatObservation):
        if fault == "ocr_empty":
            return None
        if fault == "ocr_exception":
            raise RuntimeError("ocr")
        if fault == "identity_changed":
            return ChatObservation(
                ChatIdentity(502, "另一个聊天", "personal"),
                observation.context,
                observation.customer_turn,
                captured_at=2.0,
                foreground=True,
            )
        if fault == "window_background":
            return ChatObservation(
                observation.identity,
                observation.context,
                observation.customer_turn,
                captured_at=2.0,
                foreground=False,
            )
        return observation

    def validate(_decision: ReplyDecision) -> bool:
        if fault == "settings_corrupt":
            raise ValueError("settings")
        return fault != "vector_mismatch"

    def send(_identity: ChatIdentity, text: str) -> SendAttempt:
        sender_calls.append(text)
        if fault == "send_uncertain":
            return SendAttempt(True, "enter_keypress")
        raise AssertionError("fault matrix must block before sender")

    adapters = RuntimeAdapters(
        decide=decide,
        validate_for_send=validate,
        prepare_send_window=lambda _expected: "ready",
        fresh_observation=fresh,
        send=send,
        verify=lambda _fresh, _text: "uncertain",
    )
    coordinator = ManagedReplyCoordinator(
        SendLedger(tmp_path / f"{fault}-ledger.json").load()
    )
    outcome = coordinator.process(observation, adapters)
    return SimpleNamespace(outcome=outcome, sender_calls=sender_calls, ledger=coordinator.ledger)


@pytest.mark.parametrize(
    "fault",
    [
        "ocr_empty",
        "ocr_exception",
        "identity_changed",
        "window_background",
        "settings_corrupt",
        "memory_corrupt",
        "vector_mismatch",
        "api_timeout",
        "api_invalid_json",
        "send_uncertain",
    ],
)
def test_formal_fault_matrix_never_records_unverified_send(
    tmp_path: Path,
    fault: str,
) -> None:
    result = _formal_runtime_fault(tmp_path, fault)

    assert result.outcome.sent is False
    assert result.sender_calls == ([] if fault != "send_uncertain" else [
        "进入福建省网上办事大厅用户登录页面，找到“忘记密码”按操作找回。"
    ])
    assert result.ledger.status(result.outcome.turn_key) != "verified"


def test_package_check_requires_calibration_and_application_entry(tmp_path: Path) -> None:
    report = run_package_check(tmp_path)

    assert report.ok is False
    assert "wecom_5_calibration.json" in report.missing
    assert "聊小智.exe|main.py" in report.missing


def test_private_file_scan_blocks_every_runtime_data_family(tmp_path: Path) -> None:
    private = {
        "user_data/settings.json",
        "nested/credentials.dat",
        "nested/managed_send.log",
        "send_ledger.json.bak.1",
    }
    for relative in private:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private", encoding="utf-8")

    assert set(scan_private_files(tmp_path)) == private


def test_release_manifest_hashes_files_and_excludes_itself(tmp_path: Path) -> None:
    (tmp_path / "聊小智.exe").write_bytes(b"exe")
    (tmp_path / "wecom_5_calibration.json").write_text("{}", encoding="utf-8")

    manifest_path = write_release_manifest(tmp_path)
    document = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert document["version"] == __version__
    assert [item["path"] for item in document["files"]] == [
        "wecom_5_calibration.json",
        "聊小智.exe",
    ]
    assert "release-manifest.json" not in {item["path"] for item in document["files"]}


def test_package_check_rejects_files_not_covered_by_release_manifest(tmp_path: Path) -> None:
    (tmp_path / "聊小智.exe").write_bytes(b"exe")
    (tmp_path / "wecom_5_calibration.json").write_text(
        json.dumps(
            {
                "version": "test",
                "conversation_list_area": {},
                "top_title_area": {},
                "avatar_area": {},
                "timing": {},
            }
        ),
        encoding="utf-8",
    )
    write_release_manifest(tmp_path)
    (tmp_path / "unlisted.bin").write_bytes(b"not covered")

    report = run_package_check(tmp_path)

    assert "manifest_unlisted:unlisted.bin" in report.errors


def test_runtime_check_rejects_legacy_model_memory_mode(tmp_path: Path) -> None:
    from memory import StyleMemory
    from settings import AppSettings

    settings_path = tmp_path / "settings.json"
    AppSettings(config_path=settings_path).save()
    settings_document = json.loads(settings_path.read_text(encoding="utf-8"))
    settings_document["reply_source_mode"] = "model_memory"
    settings_path.write_text(json.dumps(settings_document), encoding="utf-8")
    StyleMemory(path=tmp_path / "memory.json").save()

    report = run_runtime_check(tmp_path, require_wecom=False)

    assert report.ok is False
    assert "reply_source_mode_not_knowledge_only" in report.errors


def test_build_refuses_to_overwrite_existing_version_directory(tmp_path: Path) -> None:
    release = tmp_path / f"聊小智_{__version__}"
    release.mkdir()

    with pytest.raises(FileExistsError):
        prepare_release_dir(release)


def test_spec_and_dependency_locks_are_release_exact() -> None:
    spec = (PROJECT_ROOT / "聊小智.spec").read_text(encoding="utf-8")
    runtime_lock = (PROJECT_ROOT / "requirements-runtime.lock.txt").read_text(
        encoding="utf-8"
    )
    dev_lock = (PROJECT_ROOT / "requirements-dev.lock.txt").read_text(encoding="utf-8")

    assert "wecom_5_calibration.json" in spec
    assert "user_data" not in spec
    assert "copy_metadata" in spec
    assert "requests" in spec
    assert "pywin32" in spec
    assert __version__ == "2026.08.14.5"
    for distribution, expected in RUNTIME_DEPENDENCIES.items():
        assert f"{distribution}=={expected}" in runtime_lock
    assert "pytest==9.1.1" in dev_lock
    assert "pyinstaller==6.21.0" in dev_lock


def test_release_assets_place_calibration_at_package_root(tmp_path: Path) -> None:
    install_release_assets(tmp_path)

    assert (tmp_path / "wecom_5_calibration.json").read_bytes() == (
        PROJECT_ROOT / "wecom_5_calibration.json"
    ).read_bytes()


def test_desktop_shortcut_script_requires_exact_versioned_executable() -> None:
    script = (PROJECT_ROOT / "tools" / "create_desktop_shortcut.ps1").read_text(
        encoding="utf-8"
    )

    assert "Resolve-Path -LiteralPath" in script
    assert "0x804A, 0x5C0F, 0x667A" in script
    assert "$exe.Name -ne $expectedExeName" in script
    assert "_\\d{4}\\.\\d{2}\\.\\d{2}\\.\\d+$" in script
    assert "$saved.TargetPath -ne $resolvedExe" in script


def test_acceptance_event_is_explicit_machine_evidence_without_chat_text(
    tmp_path: Path,
) -> None:
    import main

    decision = ReplyDecision(
        kind="answer",
        text="PRIVATE ANSWER TEXT",
        record_id="b" * 64,
        answer_hash="a" * 64,
        score=91,
        org_scope="social",
    )
    outcome = RuntimeOutcome(
        phase=ManagedPhase.RECORDED,
        sent=True,
        reason="verified",
        turn_key="PRIVATE TURN KEY",
        decision=decision,
        phase_trace=("waiting", "recorded"),
        attempt_count=1,
        verification="verified",
    )
    report = tmp_path / "acceptance.jsonl"

    main._append_acceptance_event(report, "controlled", outcome)

    line = json.loads(report.read_text(encoding="utf-8"))
    assert set(line) == {
        "timestamp",
        "mode",
        "phase",
        "reason",
        "sent",
        "verification",
        "decision_kind",
        "record_id",
        "answer_hash",
        "score",
        "org_scope",
        "attempt_count",
    }
    assert line["record_id"] == "b" * 64
    assert line["answer_hash"] == "a" * 64
    raw = report.read_text(encoding="utf-8")
    assert "PRIVATE ANSWER TEXT" not in raw
    assert "PRIVATE TURN KEY" not in raw


def test_runtime_check_cli_uses_explicit_data_root_without_starting_ui(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main
    import self_check

    captured: dict[str, object] = {}

    def fake_run_self_check(report_path, *, runtime_data_dir, require_wecom):
        captured.update(
            report_path=report_path,
            runtime_data_dir=runtime_data_dir,
            require_wecom=require_wecom,
        )
        return SimpleNamespace(ok=True)

    def forbidden_app_factory():
        raise AssertionError("runtime check must not construct the UI application")

    report = tmp_path / "runtime report.json"
    monkeypatch.setattr(self_check, "run_self_check", fake_run_self_check)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "main.py",
            "--runtime-check",
            "--data-dir",
            str(tmp_path),
            "--report",
            str(report),
            "--no-wecom",
        ],
    )

    main._run_main(forbidden_app_factory)

    assert captured == {
        "report_path": report,
        "runtime_data_dir": tmp_path,
        "require_wecom": False,
    }


def test_unknown_cli_option_fails_closed_without_starting_ui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main

    monkeypatch.setattr(sys, "argv", ["main.py", "--unknown-diagnostic-gate"])

    with pytest.raises(SystemExit) as error:
        main._run_main(
            lambda: (_ for _ in ()).throw(
                AssertionError("unknown CLI options must not construct the UI")
            )
        )

    assert error.value.code == 2


def test_diagnostic_cli_runs_before_single_instance_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main
    import self_check

    calls: list[Path] = []

    def fake_run_self_check(report_path, *, runtime_data_dir, require_wecom):
        calls.append(Path(runtime_data_dir))
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(self_check, "run_self_check", fake_run_self_check)
    monkeypatch.setattr(
        sys,
        "argv",
        ["main.py", "--runtime-check", "--data-dir", str(tmp_path)],
    )

    main.main(
        instance_factory=lambda: (_ for _ in ()).throw(
            AssertionError("diagnostic commands must bypass the singleton gate")
        ),
        app_factory=lambda: (_ for _ in ()).throw(
            AssertionError("diagnostic commands must not construct the UI")
        ),
    )

    assert calls == [tmp_path]


def test_acceptance_lifecycle_status_is_allowlisted_deduplicated_and_text_free(
    tmp_path: Path,
) -> None:
    import main

    report = tmp_path / "acceptance.jsonl"
    app = SimpleNamespace(
        acceptance_report_path=report,
        settings=SimpleNamespace(managed_mode="observe"),
    )

    assert main._record_acceptance_status_once(app, "runtime_ready") is True
    assert main._record_acceptance_status_once(app, "runtime_ready") is False
    with pytest.raises(ValueError):
        main._record_acceptance_status_once(app, "PRIVATE CHAT TEXT")

    lines = report.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["reason"] == "runtime_ready"
    assert event["mode"] == "observe"
    assert event["decision_kind"] == ""
    assert event["record_id"] == ""
    assert event["answer_hash"] == ""
    assert "PRIVATE CHAT TEXT" not in lines[0]


def test_managed_cli_synchronizes_ui_and_records_configuration_before_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main

    class Var:
        def __init__(self, value):
            self.value = value

        def set(self, value):
            self.value = value

        def get(self):
            return self.value

    class FakeApp:
        def __init__(self):
            self.settings = SimpleNamespace(managed_mode="off", auto_recognize=False)
            self.ui = SimpleNamespace(managed_var=Var("off"), auto_var=Var(False))
            self.started = False

        def start(self):
            self.started = True

    app = FakeApp()
    report = tmp_path / "acceptance.jsonl"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "main.py",
            "--managed-mode",
            "observe",
            "--acceptance-report",
            str(report),
        ],
    )

    main._run_main(lambda: app)

    assert app.started is True
    assert app.settings.managed_mode == "observe"
    assert app.settings.auto_recognize is True
    assert app.ui.managed_var.get() == "observe"
    assert app.ui.auto_var.get() is True
    event = json.loads(report.read_text(encoding="utf-8"))
    assert event["reason"] == "runtime_configured"


def test_successful_managed_start_records_runtime_ready_before_monitoring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main

    events: list[str] = []

    class Worker:
        def start(self):
            events.append("worker_started")

        def shutdown(self, *, timeout):
            assert timeout == 10.0
            events.append("worker_stopped")

    class Root:
        def after(self, _delay, _callback):
            events.append("ui_scheduled")

    class UI:
        root = Root()

        def set_status(self, _text):
            pass

        def mainloop(self):
            events.append("mainloop")

    app = main.WeChatStyleReplyAssistant.__new__(main.WeChatStyleReplyAssistant)
    app.startup_blocking_error = ""
    app.settings = SimpleNamespace(
        managed_mode="observe",
        api_key="",
        wecom_account_name="configured",
    )
    app.acceptance_report_path = tmp_path / "acceptance.jsonl"
    app.managed_worker = Worker()
    app.ui = UI()
    app.running = True
    app._establish_startup_baseline = lambda: True
    app._monitor_loop = lambda: None
    app._ui_loop = lambda: None
    monkeypatch.setattr(main.threading, "Thread", lambda **_kwargs: SimpleNamespace(start=lambda: events.append("monitor_started")))

    app.start()

    lines = (tmp_path / "acceptance.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["reason"] for line in lines] == ["runtime_ready"]
    assert events.index("worker_started") < events.index("monitor_started")
