"""Run the migration worker against a local SSH server with synthetic data."""

import importlib.util
from pathlib import Path
import socket
import tempfile
import threading
import time

import paramiko


def check(scratch):
    path = scratch / "transfer"
    path.mkdir(mode=0o700)
    archive = path / "transfer.mdmove"
    archive.write_bytes(b"synthetic data")
    remote_path = "/root/mdnext-transfer-Lab"
    spec = importlib.util.spec_from_file_location("migration_ssh", Path("scripts/migration-ssh.py"))
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    key = paramiko.RSAKey.generate(2048)
    authentications = []
    commands = []
    errors = []

    class Endpoint(paramiko.ServerInterface):
        def __init__(self):
            self.command_ready = threading.Event()

        def check_auth_password(self, username, password):
            assert (username, password) == ("root", "synthetic-loopback")
            authentications.append(username)
            return paramiko.AUTH_SUCCESSFUL

        def check_channel_request(self, kind, chanid):
            return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

        def check_channel_exec_request(self, channel, command):
            self.command = command.decode()
            self.command_ready.set()
            return True

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(2)
        listener.settimeout(12)

        def serve():
            try:
                for _ in range(2):
                    connection, _ = listener.accept()
                    endpoint = Endpoint()
                    with paramiko.Transport(connection) as transport:
                        transport.add_server_key(key)
                        transport.start_server(server=endpoint)
                        while transport.is_active():
                            channel = transport.accept(1)
                            if channel is None:
                                continue
                            with channel:
                                assert endpoint.command_ready.wait(4)
                                command = endpoint.command
                                endpoint.command_ready.clear()
                                commands.append(command)
                                if command == "cat /etc/machine-id":
                                    channel.sendall(b"test-machine\n")
                                else:
                                    assert command == "rm -rf -- " + remote_path
                                    archive.unlink()
                                    path.rmdir()
                                channel.send_exit_status(0)
                                time.sleep(0.2)
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        request = {
            "host": "127.0.0.1",
            "port": listener.getsockname()[1],
            "fingerprint": worker.fingerprint(key),
            "ssh_password": "synthetic-loopback",
        }
        sock, broken = worker.connect_target(request, timeout=5)
        broken.close()
        sock.close()
        worker.cleanup_transfer(request, broken, remote_path, "test-machine")
        thread.join(6)
        assert not thread.is_alive()
        assert not errors, errors
        assert len(authentications) == 2 and not path.exists()
        assert commands == ["cat /etc/machine-id", "rm -rf -- " + remote_path]
        print("SSH_DISCONNECT_RECONNECT_CLEANUP_OK")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="mdnext-ssh-lab-") as directory:
        check(Path(directory))
