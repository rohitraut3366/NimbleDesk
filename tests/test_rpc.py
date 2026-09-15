from __future__ import annotations

import asyncio

import pytest

from nimbledesk.backends import SimulatorBackend
from nimbledesk.client import DaemonClient, DaemonClientError
from nimbledesk.daemon.approvals import ApprovalManager
from nimbledesk.daemon.audit import AuditLog
from nimbledesk.daemon.policy import ActionPolicy
from nimbledesk.daemon.runtime import DesktopRuntime
from nimbledesk.daemon.sessions import SessionManager
from nimbledesk.daemon.transport import DaemonTransport
from nimbledesk.protocol.rpc import ConnectionInfo, RequestAuthenticator, create_request


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
        capture = await client.call(
            "screen_capture",
            {
                "session_id": session["session_id"],
                "observation_id": observation["observation_id"],
            },
        )

    assert health == {"status": "ok"}
    assert observation["platform"] == "simulator"
    assert capture["mime_type"] == "image/png"


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
