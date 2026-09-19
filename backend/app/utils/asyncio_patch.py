"""
Windows asyncio ProactorEventLoop WinError 10054 / 10053 monkey patch and exception handler.
"""
import sys
import socket
import logging

logger = logging.getLogger("asyncio_patch")

def apply_proactor_patch():
    if sys.platform != "win32":
        return

    try:
        from asyncio.proactor_events import _ProactorBasePipeTransport

        if getattr(_ProactorBasePipeTransport, "_win_10054_patched", False):
            return

        _orig_call_connection_lost = _ProactorBasePipeTransport._call_connection_lost

        def _patched_call_connection_lost(self, exc):
            try:
                self._protocol.connection_lost(exc)
            finally:
                if hasattr(self, "_sock") and self._sock is not None:
                    if hasattr(self._sock, "shutdown"):
                        try:
                            self._sock.shutdown(socket.SHUT_RDWR)
                        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, OSError):
                            pass
                    try:
                        self._sock.close()
                    except Exception:
                        pass
                    self._sock = None
                server = getattr(self, "_server", None)
                if server is not None:
                    try:
                        server._detach()
                    except Exception:
                        pass
                    self._server = None

        _ProactorBasePipeTransport._call_connection_lost = _patched_call_connection_lost
        _ProactorBasePipeTransport._win_10054_patched = True
    except Exception as e:
        logger.warning(f"Failed to apply Windows asyncio patch: {e}")

def silence_winerror_10054(loop, context):
    exc = context.get("exception")
    if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
        return
    if getattr(exc, "winerror", None) in (10054, 10053, 10038):
        return
    loop.default_exception_handler(context)

apply_proactor_patch()
