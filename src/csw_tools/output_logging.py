"""Shared stdout/stderr transcript logging for CLI invocations."""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import TextIO

import click
from click import termui


class OutputLogError(RuntimeError):
    """Raised when an enabled CLI transcript cannot be written safely."""


def cli_output_log_path(
    directory: Path,
    command_name: str,
    *,
    now: datetime | None = None,
) -> Path:
    """Return the standardized transcript path for one command invocation."""

    current = now if now is not None else datetime.now(UTC)
    current = current.astimezone(UTC)
    milliseconds = current.microsecond // 1_000
    timestamp = f"{current:%Y%m%dT%H%M%S}{milliseconds:03d}Z"
    return directory.expanduser().resolve() / (
        f"csw-tools-{command_name}-{timestamp}.log"
    )


class _TranscriptWriter:
    """Serialize plain-text writes from stdout and stderr into one file."""

    def __init__(self, file: TextIO, path: Path) -> None:
        self.file = file
        self.path = path
        self.lock = Lock()
        self._pause_count = 0

    def write(self, value: str) -> None:
        try:
            with self.lock:
                if self._pause_count:
                    return
                self.file.write(click.unstyle(value))
                self.file.flush()
        except OSError as exc:
            raise OutputLogError(
                f"Could not write CLI output log: {self.path}"
            ) from exc

    @contextmanager
    def paused(self) -> Iterator[None]:
        """Temporarily omit terminal-echoed user input from the transcript."""

        with self.lock:
            self._pause_count += 1
        try:
            yield
        finally:
            with self.lock:
                self._pause_count -= 1

    def flush(self) -> None:
        try:
            with self.lock:
                self.file.flush()
        except OSError as exc:
            raise OutputLogError(
                f"Could not write CLI output log: {self.path}"
            ) from exc


class _TeeTextIO:
    """Mirror text to a transcript without changing the original stream."""

    def __init__(self, original: TextIO, transcript: _TranscriptWriter) -> None:
        self.original = original
        self.transcript = transcript

    def write(self, value: str) -> int:
        written = self.original.write(value)
        self.transcript.write(value)
        return written

    def flush(self) -> None:
        self.original.flush()
        self.transcript.flush()

    def isatty(self) -> bool:
        return self.original.isatty()

    def writable(self) -> bool:
        return True

    def fileno(self) -> int:
        return self.original.fileno()

    @property
    def encoding(self) -> str | None:
        return self.original.encoding

    @property
    def errors(self) -> str | None:
        return self.original.errors

    def __getattr__(self, name: str) -> object:
        return getattr(self.original, name)


class _InputTextIO:
    """Pause transcript writes while terminal input may be echoed."""

    def __init__(self, original: TextIO, transcript: _TranscriptWriter) -> None:
        self.original = original
        self.transcript = transcript

    def read(self, size: int = -1) -> str:
        with self.transcript.paused():
            return self.original.read(size)

    def readline(self, size: int = -1) -> str:
        with self.transcript.paused():
            return self.original.readline(size)

    def readlines(self, hint: int = -1) -> list[str]:
        with self.transcript.paused():
            return self.original.readlines(hint)

    def __iter__(self) -> Iterator[str]:
        while True:
            line = self.readline()
            if not line:
                return
            yield line

    def __getattr__(self, name: str) -> object:
        return getattr(self.original, name)


class OutputLogSession:
    """Own the transcript file and process stream redirection for one run."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: TextIO | None = None
        self._stdin: TextIO | None = None
        self._stdout: TextIO | None = None
        self._stderr: TextIO | None = None
        self._stdout_tee: _TeeTextIO | None = None
        self._stderr_tee: _TeeTextIO | None = None
        self._stdin_proxy: _InputTextIO | None = None
        self._visible_prompt_func: Callable[[str | None], str] | None = None
        self._hidden_prompt_func: Callable[[str | None], str] | None = None

    def start(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._file = self.path.open("w", encoding="utf-8")
        except OSError as exc:
            raise OutputLogError(
                f"Could not create CLI output log: {self.path}"
            ) from exc

        transcript = _TranscriptWriter(self._file, self.path)
        self._stdin = sys.stdin
        self._stdout = sys.stdout
        self._stderr = sys.stderr
        self._stdin_proxy = _InputTextIO(self._stdin, transcript)
        self._stdout_tee = _TeeTextIO(self._stdout, transcript)
        self._stderr_tee = _TeeTextIO(self._stderr, transcript)
        self._visible_prompt_func = termui.visible_prompt_func
        self._hidden_prompt_func = termui.hidden_prompt_func

        def prompt_without_value(
            original: Callable[[str | None], str], prompt: str | None
        ) -> str:
            transcript.write(prompt or "")
            try:
                with transcript.paused():
                    return original(prompt)
            finally:
                transcript.write("\n")

        def visible_prompt(prompt: str | None = None) -> str:
            assert self._visible_prompt_func is not None
            return prompt_without_value(self._visible_prompt_func, prompt)

        def hidden_prompt(prompt: str | None = None) -> str:
            assert self._hidden_prompt_func is not None
            return prompt_without_value(self._hidden_prompt_func, prompt)

        sys.stdin = self._stdin_proxy  # type: ignore[assignment]
        sys.stdout = self._stdout_tee  # type: ignore[assignment]
        sys.stderr = self._stderr_tee  # type: ignore[assignment]
        termui.visible_prompt_func = visible_prompt
        termui.hidden_prompt_func = hidden_prompt

    def close(self) -> None:
        if self._visible_prompt_func is not None:
            termui.visible_prompt_func = self._visible_prompt_func
            self._visible_prompt_func = None
        if self._hidden_prompt_func is not None:
            termui.hidden_prompt_func = self._hidden_prompt_func
            self._hidden_prompt_func = None
        if self._stdin_proxy is not None and sys.stdin is self._stdin_proxy:
            assert self._stdin is not None
            sys.stdin = self._stdin
        if self._stdout_tee is not None and sys.stdout is self._stdout_tee:
            assert self._stdout is not None
            sys.stdout = self._stdout
        if self._stderr_tee is not None and sys.stderr is self._stderr_tee:
            assert self._stderr is not None
            sys.stderr = self._stderr

        if self._file is not None:
            try:
                self._file.flush()
                self._file.close()
            except OSError:
                # Writes are flushed eagerly and report failures while the CLI
                # can still render a useful Click error.
                pass
            finally:
                self._file = None


_ACTIVE_OUTPUT_LOG: ContextVar[OutputLogSession | None] = ContextVar(
    "csw_tools_active_output_log",
    default=None,
)


def prepare_output_logging() -> Token[OutputLogSession | None]:
    """Start an isolated output-log lifecycle for a root Click invocation."""

    return _ACTIVE_OUTPUT_LOG.set(None)


def start_output_logging(directory: Path, command_name: str) -> OutputLogSession:
    """Activate transcript logging for the current root Click invocation."""

    session = OutputLogSession(cli_output_log_path(directory, command_name))
    _ACTIVE_OUTPUT_LOG.set(session)
    session.start()
    return session


def stop_output_logging(token: Token[OutputLogSession | None]) -> None:
    """Close the current transcript and restore the prior logging context."""

    session = _ACTIVE_OUTPUT_LOG.get()
    if session is not None:
        session.close()
    _ACTIVE_OUTPUT_LOG.reset(token)


def disable_failed_output_logging() -> None:
    """Restore terminal streams after a transcript write failure."""

    session = _ACTIVE_OUTPUT_LOG.get()
    if session is not None:
        session.close()
