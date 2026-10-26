#!/usr/bin/env python3
"""Acknowledged terminal session for the ERPBridge Container Apps replica.

Provenance: this is the transport that performed the live alpha.5 upgrade on
2026-10-15, promoted from the ignored evidence directory so the cutover does not
depend on unreviewed code. The wire behaviour is preserved: the same resize
packet, the same `\\x00\\x00` command framing, the same `\\n<octal marker>:%s\\n`
exit acknowledgement, the same 3900-byte terminal input bound, and the same
frame handling.

A Container Apps replica has no file transport. The only channel is a terminal,
so every byte the migration moves is text, and every command is acknowledged
with its exit status before the caller continues.

The Azure CLI supplies the WebSocket client. Run this file with the interpreter
the CLI ships, not with a plain python3, or the import below will fail:

    "$(dirname "$(readlink -f "$(command -v az)")")/../bin/python" aca-session.py ...

Nothing here writes to the registry. The caller decides which commands to send.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
import re
import shlex
import sys
import time
import uuid
from pathlib import Path

# Terminal input is bounded; the proven limit is 3900 bytes per command packet.
MAX_PACKET_BYTES = 3900
DEFAULT_CHUNK = 3000
RESIZE_PACKET = b'\x00\x04' + b'{"Width":120,"Height":50}'


class SessionError(Exception):
    """Failure that is safe to print."""


def octal_escape(text: str) -> str:
    return "".join("\\%03o" % byte for byte in text.encode())


def acknowledged_packet(command: str, marker: str) -> bytes:
    """Wrap a command so its exit status comes back with an unguessable marker."""
    packet = (command + '; backup_rc=$?; printf "\\n' + octal_escape(marker)
              + ':%s\\n" "$backup_rc"\n').encode()
    if len(packet) >= MAX_PACKET_BYTES:
        raise SessionError("A command packet exceeds the terminal input bound.")
    return packet


def chunked(text: str, size: int = DEFAULT_CHUNK) -> list[str]:
    if size < 64:
        raise SessionError("Chunk size is too small to be safe.")
    return [text[offset:offset + size] for offset in range(0, len(text), size)] or [""]


def extract_payload(output: bytes, delimiter: bytes) -> bytes:
    """Return everything after the delimiter, or fail closed."""
    if delimiter not in output:
        raise SessionError("The export delimiter was not found in the terminal output.")
    return output.split(delimiter, 1)[1]


def verify_transfer(data: bytes, remote_hash_line: str, expected: str | None = None) -> str:
    """Compare a local digest with the remote one, and with an expected digest."""
    digest = hashlib.sha256(data).hexdigest()
    if digest not in remote_hash_line:
        raise SessionError("The transferred bytes do not match the remote checksum.")
    if expected is not None and digest != expected:
        raise SessionError("The transferred bytes do not match the expected checksum.")
    return digest


def encode_helper(path: Path) -> tuple[str, str]:
    """Return the transport text and the digest that must be verified remotely."""
    raw = path.read_bytes()
    return base64.b64encode(gzip.compress(raw, compresslevel=9)).decode(), \
        hashlib.sha256(raw).hexdigest()


class Session:
    """Acknowledged command channel to one replica.

    The connection is injected so the acknowledgement logic can be tested
    without Azure. Use `connect` to build the real one.
    """

    def __init__(self, connection, prefix: str | None = None, timeout: float = 45.0):
        self.connection = connection
        self.prefix = prefix or uuid.uuid4().hex
        self.timeout = timeout
        self.sequence = 0
        self.buffer = bytearray()
        self.connection.send(RESIZE_PACKET)
        time.sleep(1)

    def run(self, command: str, timeout: float | None = None) -> bytes:
        self.sequence += 1
        marker = f"ACK_{self.prefix}_{self.sequence}".encode()
        self.connection.send(b"\x00\x00" + acknowledged_packet(command, marker.decode()))
        deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
        while time.monotonic() < deadline:
            if marker + b":" in self.buffer:
                end = self.buffer.index(marker + b":")
                tail = bytes(self.buffer[end + len(marker) + 1:])
                if b"\n" in tail:
                    code = tail.splitlines()[0].strip()
                    output = bytes(self.buffer[:end])
                    self.buffer = bytearray(tail[tail.index(b"\n") + 1:])
                    if code != b"0":
                        raise SessionError("An acknowledged remote command failed.")
                    return output
            try:
                frame = self.connection.recv()
            except Exception as error:  # noqa: BLE001 - transport-specific timeouts
                if type(error).__name__ in ("WebSocketTimeoutException", "TimeoutError"):
                    continue
                raise SessionError("The terminal connection failed.") from None
            if not frame:
                raise SessionError("The remote terminal closed unexpectedly.")
            if isinstance(frame, str):
                frame = frame.encode()
            if frame[0] == 0 and frame[1] in (1, 2):
                self.buffer.extend(frame[2:])
            elif frame[0] == 2:
                raise SessionError("The remote proxy reported an error.")
        raise SessionError("An acknowledged command exceeded its deadline.")

    def close(self) -> None:
        try:
            self.connection.send(b"\x00\x00exit\n")
        except Exception:  # noqa: BLE001 - best-effort shutdown
            pass
        try:
            self.connection.disconnect()
        except Exception:  # noqa: BLE001 - best-effort shutdown
            pass


def connect(resource_group: str, app: str, revision: str, replica: str, container: str,
            startup_command: str = "sh") -> Session:
    """Build the proven Azure CLI WebSocket session."""
    try:
        # These live only inside the Azure CLI's bundled interpreter, which is why
        # this file is imported lazily and documented as such.
        from azure.cli.command_modules.containerapp.custom import WebSocketConnection  # type: ignore[import-not-found]
        from azure.cli.core import get_default_cli  # type: ignore[import-not-found]
    except ImportError:
        raise SessionError("Run this file with the interpreter the Azure CLI ships.") from None
    import types

    connection = WebSocketConnection(
        cmd=types.SimpleNamespace(cli_ctx=get_default_cli()),
        resource_group_name=resource_group, name=app, revision=revision, replica=replica,
        container=container, startup_command=startup_command,
    )
    connection._socket.settimeout(5)
    return Session(connection)


def push_helper(session: Session, binary: Path, remote_dir: str, progress=None) -> None:
    """Upload the helper in bounded chunks and refuse to run it unverified."""
    encoded, expected = encode_helper(binary)
    session.run("stty -echo 2>/dev/null || true")
    session.run(f"umask 077; mkdir -p -m 700 {shlex.quote(remote_dir)}; "
                f": > {remote_dir}/helper.b64")
    for index, part in enumerate(chunked(encoded), start=1):
        session.run(f"printf %s {shlex.quote(part)} >> {remote_dir}/helper.b64")
        if progress is not None:
            progress("upload", index * DEFAULT_CHUNK, len(encoded))
    output = session.run(
        f"base64 -d {remote_dir}/helper.b64 | gzip -d > {remote_dir}/helper; "
        f"test ${{PIPESTATUS:-0}} = 0; chmod 700 {remote_dir}/helper; sha256sum {remote_dir}/helper")
    if expected.encode() not in output:
        raise SessionError("The helper checksum did not match; refusing to execute it.")


def export_file(session: Session, remote_path: str, destination: Path,
                delimiter: bytes | None = None) -> dict:
    """Stream a remote file out as base64 and verify it against the remote hash.

    The delimiter is injectable so the parsing can be tested without a live
    terminal. Callers leave it unset and get a fresh random marker.
    """
    delimiter = delimiter or ("SNAPSHOT_" + uuid.uuid4().hex).encode()
    output = session.run(f'printf "{octal_escape(delimiter.decode())}\\n"; '
                         f"base64 {remote_path}", timeout=120)
    data = base64.b64decode(extract_payload(output, delimiter), validate=False)
    remote_hash = session.run(f"sha256sum {remote_path}").decode()
    digest = verify_transfer(data, remote_hash)
    with destination.open("xb") as stream:
        stream.write(data)
    os.chmod(destination, 0o600)
    return {"bytes": len(data), "sha256": digest}


def main() -> int:
    parser = argparse.ArgumentParser(description="Acknowledged Container Apps session.")
    parser.add_argument("--resource-group", default="erpbridge-hq")
    parser.add_argument("--app", default="erpbridge-server")
    parser.add_argument("--revision", required=False)
    parser.add_argument("--replica", required=False)
    parser.add_argument("--container", default="erpbridge")
    parser.add_argument("--probe-only", action="store_true",
                        help="Connect and acknowledge one command, then exit.")
    parser.add_argument("--helper", help="Helper binary to push and run.")
    parser.add_argument("--snapshot-out", help="Local destination for the snapshot.")
    parser.add_argument("--remote-dir",
                        help="Fixed remote working directory, so a helper can be reused.")
    parser.add_argument("--keep-remote", action="store_true",
                        help="Leave the remote helper in place for reuse.")
    parser.add_argument("--reuse-helper", action="store_true",
                        help="Skip the upload when the remote helper already matches.")
    args = parser.parse_args()

    session = None
    if args.remote_dir:
        if not re.fullmatch(r"/tmp/[A-Za-z0-9._-]{1,60}", args.remote_dir):
            print("The remote directory must be a simple path under /tmp.", file=sys.stderr)
            return 2
        remote_dir = args.remote_dir
    else:
        remote_dir = "/tmp/erpbridge-ack-" + uuid.uuid4().hex
    try:
        session = connect(args.resource_group, args.app, args.revision, args.replica,
                          args.container)
        if args.probe_only:
            session.run("stty -echo 2>/dev/null || true")
            print("Acknowledged terminal command succeeded.")
            return 0
        if not args.helper or not args.snapshot_out:
            raise SessionError("--helper and --snapshot-out are both required.")
        reusable = False
        if args.reuse_helper:
            # A pre-pushed helper removes minutes from the cutover window. Its
            # checksum is still verified before anything runs.
            _, expected = encode_helper(Path(args.helper))
            observed = session.run(f"sha256sum {remote_dir}/helper 2>/dev/null || true")
            reusable = expected.encode() in observed
            print("Remote helper reused." if reusable else "Remote helper missing; uploading.")
        if not reusable:
            push_helper(session, Path(args.helper), remote_dir)
        result = session.run(f"{remote_dir}/helper /tmp/erpbridge.db "
                             f"{remote_dir}/erpbridge.db", timeout=90)
        if b'"snapshot_created":true' not in result:
            raise SessionError("The helper did not report a created snapshot.")
        record = export_file(session, f"{remote_dir}/erpbridge.db", Path(args.snapshot_out))
        if args.keep_remote:
            session.run(f"rm -f -- {remote_dir}/erpbridge.db")
            record["remote_helper_retained"] = True
        else:
            session.run(f"rm -rf -- {shlex.quote(remote_dir)}")
            record["remote_temporary_files_removed"] = True
        record["phase"] = "snapshot_exported"
        record["helper_checksum_verified"] = True
        print(json.dumps(record, indent=2))
        return 0
    except SessionError as error:
        print(f"Snapshot session failed: {error}", file=sys.stderr)
        return 1
    finally:
        if session is not None:
            session.close()


if __name__ == "__main__":
    sys.exit(main())
