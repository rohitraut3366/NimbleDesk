from __future__ import annotations

from types import SimpleNamespace

from pytest import MonkeyPatch

import nimbledesk.adapters.limits as limits
import nimbledesk.adapters.runner as runner


class FakeProcess:
    pid = 8192
    returncode = 0
    stdin = None

    def __init__(self) -> None:
        self.terminated = False
        self.killed = False

    def poll(self) -> int:
        return 0

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        return 0


def test_posix_worker_irreversibly_lowers_memory_process_and_cpu_limits(
    monkeypatch: MonkeyPatch,
) -> None:
    applied: dict[int, tuple[int, int]] = {}
    resource = SimpleNamespace(
        RLIMIT_AS=1,
        RLIMIT_CPU=2,
        RLIMIT_FSIZE=3,
        RLIMIT_NOFILE=4,
        RLIMIT_NPROC=5,
        RLIM_INFINITY=-1,
        getrlimit=lambda _kind: (-1, -1),
        setrlimit=lambda kind, value: applied.__setitem__(kind, value),
    )
    monkeypatch.setattr(limits.os, "name", "posix")
    monkeypatch.setattr(limits.sys, "platform", "linux")
    monkeypatch.setattr(limits, "import_module", lambda _name: resource)

    limits.apply_worker_limits(30.2)

    assert applied == {
        resource.RLIMIT_AS: (
            limits.MAXIMUM_WORKER_MEMORY_BYTES,
            limits.MAXIMUM_WORKER_MEMORY_BYTES,
        ),
        resource.RLIMIT_CPU: (33, 33),
        resource.RLIMIT_FSIZE: (
            limits.MAXIMUM_WORKER_FILE_BYTES,
            limits.MAXIMUM_WORKER_FILE_BYTES,
        ),
        resource.RLIMIT_NOFILE: (
            limits.MAXIMUM_WORKER_OPEN_FILES,
            limits.MAXIMUM_WORKER_OPEN_FILES,
        ),
        resource.RLIMIT_NPROC: (
            limits.MAXIMUM_WORKER_PROCESSES,
            limits.MAXIMUM_WORKER_PROCESSES,
        ),
    }


def test_posix_runner_terminates_the_entire_worker_process_group(
    monkeypatch: MonkeyPatch,
) -> None:
    signals: list[tuple[int, int]] = []
    process = FakeProcess()
    monkeypatch.setattr(runner.os, "name", "posix")
    monkeypatch.setattr(
        runner.os, "killpg", lambda process_id, signal: signals.append((process_id, signal))
    )

    runner._terminate_process(process)
    runner._kill_process(process)
    runner._kill_process_group(process)

    assert signals == [
        (process.pid, runner.signal.SIGTERM),
        (process.pid, runner.signal.SIGKILL),
        (process.pid, runner.signal.SIGKILL),
    ]
    assert not process.terminated
    assert not process.killed


def test_posix_runner_falls_back_when_process_group_signals_are_denied(
    monkeypatch: MonkeyPatch,
) -> None:
    process = FakeProcess()
    monkeypatch.setattr(runner.os, "name", "posix")

    def denied_signal(_process_id: int, _signal: int) -> None:
        raise PermissionError

    monkeypatch.setattr(runner.os, "killpg", denied_signal)

    runner._terminate_process(process)
    runner._kill_process(process)
    runner._kill_process_group(process)

    assert process.terminated
    assert process.killed


def test_runner_measures_the_complete_worker_process_tree(
    monkeypatch: MonkeyPatch,
) -> None:
    class MeasuredProcess:
        def __init__(
            self,
            process_id: int,
            memory: int,
            children: tuple[MeasuredProcess, ...],
        ) -> None:
            self.process_id = process_id
            self.memory = memory
            self._children = children

        def children(self, *, recursive: bool) -> tuple[MeasuredProcess, ...]:
            assert recursive
            return self._children

        def memory_info(self) -> SimpleNamespace:
            return SimpleNamespace(rss=self.memory)

    child = MeasuredProcess(2, 200, ())
    root = MeasuredProcess(1, 300, (child,))
    monkeypatch.setattr(runner.psutil, "Process", lambda process_id: root)

    assert runner._process_tree_memory_bytes(FakeProcess()) == 500
