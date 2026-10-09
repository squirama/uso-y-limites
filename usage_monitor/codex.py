"""Read-only Codex RPC over a private stdio pipe; authentication stays in Codex."""

import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time

from .models import UsageError, parse_codex


MAX_RPC_LINE = 2 * 1024 * 1024
REQUEST_TIMEOUT = 25


def find_codex():
    native = shutil.which("codex.exe" if os.name == "nt" else "codex")
    if native:
        return native
    shim = shutil.which("codex.cmd") or shutil.which("codex.ps1")
    if shim:
        scope = Path(shim).parent / "node_modules" / "@openai"
        # Use the installed native binary so closing the app cannot orphan a Node child.
        for package in sorted(scope.glob("codex*")):
            matches = sorted(package.glob("**/codex.exe"))
            if matches:
                return str(matches[0])
    raise UsageError("No se encuentra Codex CLI. Instálalo e inicia sesión con tu cuenta Enterprise.")


def safe_rpc_error(error):
    message = str(error.get("message", "")).lower() if isinstance(error, dict) else ""
    if any(term in message for term in ("unauthorized", "not authenticated", "authentication", "401", "login")):
        return "Codex necesita una sesión válida. Ejecuta codex login con tu cuenta Enterprise."
    if any(term in message for term in ("forbidden", "403", "policy", "permission")):
        return "La cuenta o la política de Enterprise no permite esta consulta."
    if any(term in message for term in ("429", "too many requests")):
        return "Codex ha limitado las consultas. Se reintentará más tarde."
    return "No se pudo consultar Codex. Revisa la conexión y la sesión de Codex CLI."


class CodexClient:
    def __init__(self, command=None, timeout=REQUEST_TIMEOUT):
        self.command = command
        self.timeout = timeout
        self.process = None
        self.lock = threading.Lock()
        self.closed = threading.Event()

    def close(self):
        self.closed.set()
        with self.lock:
            process = self.process
        if process is not None:
            self._stop(process)

    @staticmethod
    def _stop(process):
        if process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                return
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def fetch(self):
        if self.closed.is_set():
            raise UsageError("La consulta se ha cancelado.")
        command = self.command or [find_codex(), "app-server", "--listen", "stdio://"]
        try:
            with self.lock:
                if self.closed.is_set():
                    raise UsageError("La consulta se ha cancelado.")
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self.process = process
        except OSError:
            raise UsageError("No se ha podido iniciar Codex CLI.") from None
        responses = queue.Queue(maxsize=64)
        finished = threading.Event()
        protocol_error = threading.Event()

        def read_output():
            try:
                while not finished.is_set():
                    line = process.stdout.readline(MAX_RPC_LINE + 1)
                    if not line:
                        break
                    if len(line) > MAX_RPC_LINE:
                        protocol_error.set()
                        break
                    try:
                        message = json.loads(line)
                    except (ValueError, UnicodeError):
                        protocol_error.set()
                        break
                    if not isinstance(message, dict):
                        protocol_error.set()
                        break
                    if "id" in message:
                        try:
                            responses.put(message, timeout=0.1)
                        except queue.Full:
                            protocol_error.set()
                            break
            except (OSError, ValueError):
                protocol_error.set()
            finally:
                finished.set()

        def drain_errors():
            # Provider stderr may contain private context. Drain it without displaying or saving it.
            try:
                while process.stderr.read(4096):
                    if finished.is_set():
                        break
            except (OSError, ValueError):
                return

        readers = [threading.Thread(target=read_output, daemon=True),
                   threading.Thread(target=drain_errors, daemon=True)]
        for reader in readers:
            reader.start()

        def send(message):
            try:
                process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                raise UsageError("Codex ha cerrado la conexión.") from None

        def request(request_id, method, params=None):
            message = {"id": request_id, "method": method}
            if params is not None:
                message["params"] = params
            send(message)
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                if self.closed.is_set():
                    raise UsageError("La consulta se ha cancelado.")
                if protocol_error.is_set():
                    raise UsageError("Codex ha devuelto una respuesta incompatible.")
                try:
                    response = responses.get(timeout=0.1)
                except queue.Empty:
                    if finished.is_set():
                        raise UsageError("Codex terminó antes de entregar el consumo.")
                    continue
                if "method" in response:
                    send({"id": response["id"], "error": {"code": -32601,
                         "message": "This client only reads usage data"}})
                    continue
                if response.get("id") != request_id:
                    continue
                if "error" in response:
                    raise UsageError(safe_rpc_error(response["error"]))
                if "result" not in response:
                    raise UsageError("Codex ha devuelto una respuesta incompleta.")
                return response["result"]
            raise UsageError("Codex no ha respondido a tiempo. Se reintentará automáticamente.")

        try:
            request(1, "initialize", {"clientInfo": {
                "name": "local_usage_monitor", "title": "Uso y límites", "version": "0.1.0"}})
            send({"method": "initialized"})
            return parse_codex(request(2, "account/rateLimits/read"))
        finally:
            self._stop(process)
            finished.set()
            for reader in readers:
                reader.join(timeout=1)
            for pipe in (process.stdin, process.stdout, process.stderr):
                pipe.close()
            with self.lock:
                if self.process is process:
                    self.process = None
