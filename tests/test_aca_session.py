#!/usr/bin/env python3
"""Acknowledged-session tests with a fake terminal, not Azure.

The transport logic is the part that must not fail at cutover, so its wire
behaviour is pinned here: packet framing, the 3900-byte bound, the exit-status
acknowledgement, frame handling, the export delimiter, and transfer checksums.
The Azure CLI connection itself is injected, so none of this needs az.
"""
import gzip
import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "demo" / "erpbridge" / "aca-session.py"


def load_module():
    spec = importlib.util.spec_from_file_location("aca_session", MODULE)
    if spec is None or spec.loader is None:
        raise RuntimeError("the session module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


aca = load_module()


class FakeConnection:
    """Feeds scripted frames and records what the session sent."""

    def __init__(self, replies=None, timeout_after=None):
        self.sent = []
        self.replies = list(replies or [])
        self.timeout_after = timeout_after
        self.recv_count = 0
        self.closed = False

    def send(self, payload):
        self.sent.append(payload)

    def recv(self):
        self.recv_count += 1
        if self.timeout_after is not None and self.recv_count > self.timeout_after:
            raise TimeoutError()
        if self.replies:
            return self.replies.pop(0)
        raise TimeoutError()

    def disconnect(self):
        self.closed = True


def frame(channel, payload):
    return bytes([0, channel]) + payload


class PacketTests(unittest.TestCase):
    def test_packet_carries_the_command_and_an_exit_acknowledgement(self):
        packet = aca.acknowledged_packet("echo hello", "ACK_marker_1")
        self.assertIn(b"echo hello", packet)
        self.assertIn(b"backup_rc=$?", packet)
        self.assertIn(aca.octal_escape("ACK_marker_1").encode(), packet)
        self.assertTrue(packet.endswith(b"\n"))

    def test_oversized_packet_is_refused_before_sending(self):
        with self.assertRaises(aca.SessionError):
            aca.acknowledged_packet("x" * aca.MAX_PACKET_BYTES, "ACK_marker_1")

    def test_chunking_is_bounded_and_lossless(self):
        payload = "abcdefghij" * 1000
        parts = aca.chunked(payload, 3000)
        self.assertTrue(all(len(part) <= 3000 for part in parts))
        self.assertEqual("".join(parts), payload)
        self.assertEqual(aca.chunked("", 3000), [""])
        with self.assertRaises(aca.SessionError):
            aca.chunked(payload, 8)


class SessionTests(unittest.TestCase):
    def make(self, replies, **kwargs):
        connection = FakeConnection(replies, **kwargs)
        session = aca.Session(connection, prefix="fixt", timeout=0.2)
        return session, connection

    def test_successful_command_returns_output_after_the_marker(self):
        session, connection = self.make([frame(1, b"remote output\n"),
                                         frame(1, b"\nACK_fixt_1:0\n")])
        # The acknowledgement prints a leading newline before the marker, so the
        # captured output ends with it. Callers match on content, not exact length.
        output = session.run("echo")
        self.assertTrue(output.startswith(b"remote output\n"))
        self.assertEqual(output.rstrip(b"\n"), b"remote output")
        self.assertEqual(connection.sent[0], aca.RESIZE_PACKET)
        self.assertTrue(connection.sent[1].startswith(b"\x00\x00"))
        self.assertIn(b"echo", connection.sent[1])

    def test_nonzero_exit_status_is_an_error(self):
        session, _ = self.make([frame(1, b"boom\n"), frame(1, b"\nACK_fixt_1:1\n")])
        with self.assertRaises(aca.SessionError):
            session.run("false")

    def test_output_arriving_before_the_marker_is_kept(self):
        session, _ = self.make([frame(1, b"first\n"),
                                frame(2, b"second\n"),
                                frame(1, b"\nACK_fixt_1:0\n")])
        # Channel 1 and channel 2 are both captured, in arrival order.
        self.assertEqual(session.run("cmd").rstrip(b"\n"), b"first\nsecond")

    def test_proxy_error_frame_is_fatal(self):
        session, _ = self.make([frame(2, b"proxy failure")])
        with self.assertRaises(aca.SessionError):
            session.run("cmd")

    def test_closed_terminal_is_fatal(self):
        session, _ = self.make([b""])
        with self.assertRaises(aca.SessionError):
            session.run("cmd")

    def test_missing_acknowledgement_hits_the_deadline(self):
        session, _ = self.make([], timeout_after=0)
        with self.assertRaises(aca.SessionError):
            session.run("cmd")

    def test_close_exits_and_disconnects(self):
        session, connection = self.make([])
        session.close()
        self.assertEqual(connection.sent[-1], b"\x00\x00exit\n")
        self.assertTrue(connection.closed)

    def test_sequence_markers_are_unique_per_command(self):
        session, connection = self.make([frame(1, b"\nACK_fixt_1:0\n"),
                                         frame(1, b"\nACK_fixt_2:0\n")])
        session.run("one")
        session.run("two")
        self.assertIn(aca.octal_escape("ACK_fixt_1").encode(), connection.sent[1])
        self.assertIn(aca.octal_escape("ACK_fixt_2").encode(), connection.sent[2])


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def test_helper_encoding_round_trips_and_reports_the_right_digest(self):
        binary = self.base / "helper"
        binary.write_bytes(b"\x7fELF not really, but bytes are bytes")
        encoded, digest = aca.encode_helper(binary)
        import base64
        self.assertEqual(gzip.decompress(base64.b64decode(encoded)), binary.read_bytes())
        self.assertEqual(digest, hashlib.sha256(binary.read_bytes()).hexdigest())

    def test_export_payload_requires_the_delimiter(self):
        with self.assertRaises(aca.SessionError):
            aca.extract_payload(b"nothing useful", b"SNAPSHOT_abc")
        self.assertEqual(aca.extract_payload(b"noise\nSNAPSHOT_abcPAYLOAD", b"SNAPSHOT_abc"),
                         b"PAYLOAD")

    def test_transfer_checksum_mismatch_is_refused(self):
        data = b"snapshot bytes"
        good = hashlib.sha256(data).hexdigest()
        self.assertEqual(aca.verify_transfer(data, f"{good}  file"), good)
        with self.assertRaises(aca.SessionError):
            aca.verify_transfer(data, "0" * 64 + "  file")
        with self.assertRaises(aca.SessionError):
            aca.verify_transfer(data, f"{good}  file", expected="1" * 64)

    def test_push_helper_verifies_the_checksum_before_running(self):
        binary = self.base / "helper"
        binary.write_bytes(b"helper-bytes")
        _, digest = aca.encode_helper(binary)
        replies = [frame(1, b"\nACK_fixt_1:0\n")]                       # stty
        replies += [frame(1, b"\nACK_fixt_2:0\n")]                      # mkdir
        replies += [frame(1, b"\nACK_fixt_3:0\n")]                      # one chunk
        replies += [frame(1, f"{digest}  helper\n".encode() + b"\nACK_fixt_4:0\n")]
        session = aca.Session(FakeConnection(replies), prefix="fixt", timeout=0.2)
        aca.push_helper(session, binary, "/tmp/remote")
        sent = b"".join(session.connection.sent)
        self.assertIn(b"chmod 700", sent)
        self.assertIn(b"sha256sum", sent)

    def test_push_helper_refuses_a_checksum_mismatch(self):
        binary = self.base / "helper"
        binary.write_bytes(b"helper-bytes")
        replies = [frame(1, b"\nACK_fixt_1:0\n"),
                   frame(1, b"\nACK_fixt_2:0\n"),
                   frame(1, b"\nACK_fixt_3:0\n"),
                   frame(1, b"0000000000000000000000000000000000000000000000000000000000000000  helper\n"
                            b"\nACK_fixt_4:0\n")]
        session = aca.Session(FakeConnection(replies), prefix="fixt", timeout=0.2)
        with self.assertRaises(aca.SessionError):
            aca.push_helper(session, binary, "/tmp/remote")
        self.assertNotIn(b"/tmp/remote/helper /tmp/erpbridge.db", b"".join(session.connection.sent))

    def stub_export_session(self, data):
        import base64
        delimiter = b"SNAPSHOT_fixture"
        session = aca.Session(FakeConnection([]), prefix="fixt", timeout=0.2)
        calls = []

        def run(command, timeout=None):
            calls.append(command)
            if "base64" in command:
                return delimiter + b"\n" + base64.b64encode(data)
            return (hashlib.sha256(data).hexdigest() + "  file").encode()

        session.run = run
        return session, delimiter, calls

    def test_export_writes_a_private_file_and_verifies_the_remote_hash(self):
        data = b"SQLite format 3\x00pretend database"
        session, delimiter, calls = self.stub_export_session(data)
        destination = self.base / "out.db"
        record = aca.export_file(session, "/tmp/remote/erpbridge.db", destination, delimiter)
        self.assertEqual(record["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(destination.read_bytes(), data)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
        self.assertIn(aca.octal_escape(delimiter.decode()), calls[0])

    def test_export_refuses_an_existing_destination(self):
        data = b"SQLite format 3\x00body"
        session, delimiter, _ = self.stub_export_session(data)
        destination = self.base / "existing.db"
        destination.write_bytes(b"keep me")
        with self.assertRaises(FileExistsError):
            aca.export_file(session, "/tmp/remote/db", destination, delimiter)
        self.assertEqual(destination.read_bytes(), b"keep me")

    def test_export_refuses_a_remote_checksum_mismatch(self):
        import base64
        data = b"SQLite format 3\x00body"
        delimiter = b"SNAPSHOT_fixture"
        session = aca.Session(FakeConnection([]), prefix="fixt", timeout=0.2)
        session.run = lambda command, timeout=None: (
            delimiter + b"\n" + base64.b64encode(data) if "base64" in command
            else ("0" * 64 + "  file").encode())
        with self.assertRaises(aca.SessionError):
            aca.export_file(session, "/tmp/remote/db", self.base / "out.db", delimiter)
        self.assertFalse((self.base / "out.db").exists())


if __name__ == "__main__":
    unittest.main()
