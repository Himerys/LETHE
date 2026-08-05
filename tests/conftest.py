import socket
import threading

import pytest


class SmtpSink(threading.Thread):
    """Minimal SMTP server that accepts everything and records the messages."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.messages: list[dict] = []
        self._shutdown = threading.Event()

    def run(self):
        self.sock.settimeout(0.2)
        while not self._shutdown.is_set():
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            try:
                self._handle(conn)
            except OSError:
                pass
            finally:
                conn.close()

    def _handle(self, conn):
        f = conn.makefile("rb")
        conn.sendall(b"220 sink ESMTP\r\n")
        rcpt, sender, data_lines, in_data = [], "", [], False
        while True:
            line = f.readline()
            if not line:
                return
            if in_data:
                if line.rstrip(b"\r\n") == b".":
                    self.messages.append(
                        {
                            "from": sender,
                            "to": list(rcpt),
                            "data": b"".join(data_lines).decode("utf-8", "replace"),
                        }
                    )
                    rcpt, sender, data_lines, in_data = [], "", [], False
                    conn.sendall(b"250 OK\r\n")
                else:
                    data_lines.append(line)
                continue
            cmd = line.strip()
            up = cmd.upper()
            if up.startswith(b"EHLO") or up.startswith(b"HELO"):
                conn.sendall(b"250 sink\r\n")
            elif up.startswith(b"MAIL FROM:"):
                sender = cmd[10:].strip().strip(b"<>").decode()
                conn.sendall(b"250 OK\r\n")
            elif up.startswith(b"RCPT TO:"):
                rcpt.append(cmd[8:].strip().strip(b"<>").decode())
                conn.sendall(b"250 OK\r\n")
            elif up.startswith(b"DATA"):
                in_data = True
                conn.sendall(b"354 end with .\r\n")
            elif up.startswith(b"QUIT"):
                conn.sendall(b"221 bye\r\n")
                return
            else:
                conn.sendall(b"250 OK\r\n")

    def stop(self):
        self._shutdown.set()
        self.join(timeout=2)
        self.sock.close()


@pytest.fixture
def smtp_sink():
    sink = SmtpSink()
    sink.start()
    yield sink
    sink.stop()
