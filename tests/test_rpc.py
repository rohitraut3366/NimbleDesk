from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path

import pytest

from nimbledesk.backends import SimulatorBackend
from nimbledesk.client import DaemonClient, DaemonClientError
from nimbledesk.daemon.approvals import ApprovalManager
from nimbledesk.daemon.audit import AuditLog
from nimbledesk.daemon.policy import ActionPolicy
from nimbledesk.daemon.runtime import DesktopRuntime
from nimbledesk.daemon.sessions import SessionManager
from nimbledesk.daemon.transport import DaemonTransport, _strict_json_object
from nimbledesk.jobs.service import JobService
from nimbledesk.protocol.rpc import (
    ConnectionInfo,
    RequestAuthenticator,
    RpcResponse,
    create_request,
)


def test_authentication_rejects_replay() -> None:
    request = create_request("health", {}, "correct-secret")
    authenticator = RequestAuthenticator("correct-secret")

    assert authenticator.verify(request)[0]
    assert authenticator.verify(request) == (False, "request nonce has already been used")


def test_authentication_rejects_wrong_secret() -> None:
    request = create_request("health", {}, "wrong-secret")

    assert RequestAuthenticator("correct-secret").verify(request) == (
        False,
        "invalid request signature",
    )


def test_rpc_parser_rejects_duplicate_keys_and_non_finite_numbers() -> None:
    with pytest.raises(ValueError, match="duplicate key: method"):
        _strict_json_object(b'{"method":"health","method":"session_start"}')
    with pytest.raises(ValueError, match="non-finite number"):
        _strict_json_object(b'{"value":NaN}')


def test_request_signature_rejects_non_canonical_numeric_values() -> None:
    with pytest.raises(ValueError, match="Out of range float values"):
        create_request("health", {"value": float("inf")}, "correct-secret")


def test_rpc_parser_fuzz_corpus_returns_bounded_errors() -> None:
    runtime = DesktopRuntime(
        SimulatorBackend(), SessionManager(), ActionPolicy(), ApprovalManager(), AuditLog()
    )
    transport = DaemonTransport(runtime, "test-secret-with-at-least-forty-three-characters")
    generator = random.Random(20260916)
    corpus = [
        b"",
        b"null",
        b"[]",
        b"{}",
        b"\xff\xfe",
        json.dumps({"params": [None] * 10_000}).encode(),
        ("[" * 2_000 + "]" * 2_000).encode(),
    ]
    corpus.extend(generator.randbytes(generator.randrange(0, 512)) for _ in range(500))

    for payload in corpus:
        response = transport._process(payload)
        assert isinstance(response, RpcResponse)
        assert not response.ok
        assert response.error_code in {"invalid_request", "authentication_failed"}
        assert response.error_message is not None
        assert len(response.error_message) < 10_000


@pytest.mark.asyncio
async def test_client_reaches_runtime_through_authenticated_transport() -> None:
    runtime = DesktopRuntime(
        backend=SimulatorBackend(),
        sessions=SessionManager(),
        policy=ActionPolicy(),
        approvals=ApprovalManager(),
        audit=AuditLog(),
    )
    transport = DaemonTransport(runtime, "test-secret-with-at-least-forty-three-characters")
    server = await transport.start()
    port = int(server.sockets[0].getsockname()[1])
    client = DaemonClient(
        ConnectionInfo(port=port, secret="test-secret-with-at-least-forty-three-characters")
    )

    async with server:
        health = await client.call("health")
        session = await client.call(
            "session_start",
            {"reason": "transport test", "config": {"input_enabled": False}},
        )
        observation = await client.call("desktop_observe", {"session_id": session["session_id"]})
        changed = await client.call(
            "desktop_observe",
            {
                "session_id": session["session_id"],
                "previous_observation_id": observation["observation_id"],
            },
        )
        retrieved = await client.call(
            "desktop_observation_get",
            {
                "session_id": session["session_id"],
                "observation_id": changed["observation_id"],
            },
        )
        status = await client.call("session_status", {"session_id": session["session_id"]})
        adapters = await client.call("adapters_list")
        audit = await client.call(
            "audit_query", {"session_id": session["session_id"], "limit": 10}
        )
        capture = await client.call(
            "screen_capture",
            {
                "session_id": session["session_id"],
                "observation_id": changed["observation_id"],
            },
        )

    assert health["status"] == "ok"
    assert health["backend"] == "simulator"
    assert "screen_capture" in health["capabilities"]
    assert observation["platform"] == "simulator"
    assert changed["change_summary"]["unchanged"] is True
    assert retrieved["observation_id"] == changed["observation_id"]
    assert len(changed["windows_sha256"]) == 64
    assert len(changed["ui_tree_sha256"]) == 64
    assert status["session_id"] == session["session_id"]
    assert status["state"] == "active"
    assert adapters == {"adapters": []}
    assert audit["entries"] == []
    assert audit["integrity"] == {
        "valid": True,
        "entries": 0,
        "head_hash": "0" * 64,
    }
    assert capture["mime_type"] == "image/jpeg"


@pytest.mark.asyncio
async def test_client_reports_authentication_failure() -> None:
    runtime = DesktopRuntime(
        SimulatorBackend(), SessionManager(), ActionPolicy(), ApprovalManager(), AuditLog()
    )
    transport = DaemonTransport(runtime, "correct-secret-with-at-least-forty-three-chars")
    server = await transport.start()
    port = int(server.sockets[0].getsockname()[1])
    client = DaemonClient(
        ConnectionInfo(port=port, secret="incorrect-secret-with-at-least-forty-three-chars")
    )

    async with server:
        with pytest.raises(DaemonClientError, match="authentication_failed"):
            await client.call("health")

    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_creative_jobs_reconnect_through_daemon_owned_rpc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture")
    jobs = JobService(tmp_path / "jobs")
    monkeypatch.setattr(jobs._executor, "submit", lambda *_args, **_kwargs: None)
    runtime = DesktopRuntime(
        SimulatorBackend(),
        SessionManager(),
        ActionPolicy(),
        ApprovalManager(),
        AuditLog(),
        jobs=jobs,
    )
    secret = "job-secret-with-at-least-forty-three-characters"
    server = await DaemonTransport(runtime, secret).start()
    connection = ConnectionInfo(port=int(server.sockets[0].getsockname()[1]), secret=secret)
    submitter = DaemonClient(connection)
    reconnecting_client = DaemonClient(connection)

    async with server:
        session = await submitter.call(
            "session_start",
            {
                "reason": "creative RPC fixture",
                "config": {"granted_paths": [str(tmp_path)]},
            },
        )
        submitted = await submitter.call(
            "job_submit",
            {
                "session_id": session["session_id"],
                "kind": "create",
                "request": {
                    "source": str(source),
                    "output_directory": str(tmp_path / "output"),
                    "brief": {"title": "RPC fixture"},
                    "ffmpeg_render": False,
                },
            },
        )
        reconnected = await reconnecting_client.call(
            "job_get",
            {"session_id": session["session_id"], "job_id": submitted["job_id"]},
        )
        listed = await reconnecting_client.call("job_list")

    jobs.close()
    assert reconnected["job_id"] == submitted["job_id"]
    assert reconnected["session_id"] == session["session_id"]
    assert listed["jobs"][0]["job_id"] == submitted["job_id"]
    assert (tmp_path / "jobs" / f"{submitted['job_id']}.json").is_file()
