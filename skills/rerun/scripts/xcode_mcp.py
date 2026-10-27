#!/usr/bin/env python3
"""Persistent JSON-lines client for the native MCP server in a running Xcode."""

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid


def find_xcode_pid(selected):
    result = subprocess.run(
        ["pgrep", "-x", "Xcode"], capture_output=True, text=True, check=False
    )
    pids = result.stdout.split()
    if selected:
        if str(selected) not in pids:
            raise RuntimeError(f"PID {selected} is not a running Xcode process")
        return str(selected)
    if len(pids) != 1:
        raise RuntimeError(
            "Expected one running Xcode instance; found "
            + str(len(pids))
            + ". Open Xcode or pass --pid with an observed Xcode PID"
        )
    return pids[0]


class Bridge:
    def __init__(self, pid, timeout):
        env = os.environ.copy()
        env["MCP_XCODE_PID"] = pid
        env["MCP_XCODE_SESSION_ID"] = str(uuid.uuid4())
        self.timeout = timeout
        self.sequence = 0
        self.messages = queue.Queue()
        self.process = subprocess.Popen(
            ["xcrun", "mcpbridge"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        threading.Thread(target=self.read_messages, daemon=True).start()

    def read_messages(self):
        try:
            for line in self.process.stdout:
                self.messages.put(json.loads(line))
        except Exception as error:
            self.messages.put(error)
        finally:
            self.messages.put(EOFError("Xcode MCP bridge closed its output"))

    def send(self, payload):
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()

    def request(self, method, params):
        self.sequence += 1
        request_id = self.sequence
        self.send({
            "jsonrpc": "2.0", "id": request_id, "method": method, "params": params
        })
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Native MCP request timed out; outcome is unknown")
            try:
                message = self.messages.get(timeout=remaining)
            except queue.Empty as error:
                raise TimeoutError(
                    "Native MCP request timed out; outcome is unknown"
                ) from error
            if isinstance(message, Exception):
                raise message
            if message.get("id") == request_id and "method" not in message:
                return message
            if "method" in message and "id" in message:
                if message["method"] == "ping":
                    self.send({"jsonrpc": "2.0", "id": message["id"], "result": {}})
                else:
                    self.send({
                        "jsonrpc": "2.0", "id": message["id"],
                        "error": {"code": -32601, "message": "Client method unsupported"}
                    })

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


def emit(value):
    print(json.dumps(value), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, help="PID of the running Xcode application")
    parser.add_argument("--timeout", type=float, default=900,
                        help="Maximum seconds per MCP request (default: 900)")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    bridge = None
    try:
        pid = find_xcode_pid(args.pid)
        bridge = Bridge(pid, args.timeout)
        initialized = bridge.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "rerun-skill", "version": "1.0"},
        })
        if "error" in initialized:
            raise RuntimeError(json.dumps(initialized["error"]))
        bridge.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        emit({"ready": True, "xcodePID": pid,
              "server": initialized.get("result", {}).get("serverInfo")})
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                command = json.loads(line)
                if not isinstance(command, dict):
                    raise ValueError("Expected a JSON object")
                method = command.get("method", "tools/call")
                if method == "tools/list":
                    params = command.get("params", {})
                elif method == "tools/call":
                    name = command.get("name")
                    arguments = command.get("arguments", {})
                    if not isinstance(name, str) or not isinstance(arguments, dict):
                        raise ValueError("Supply a tool name and arguments object")
                    params = {"name": name, "arguments": arguments}
                else:
                    raise ValueError("Only tools/list and tools/call are supported")
            except (ValueError, TypeError) as error:
                emit({"inputError": str(error)})
                continue
            response = bridge.request(method, params)
            emit(response)
        return 0
    except (RuntimeError, OSError, ValueError, TimeoutError, EOFError) as error:
        emit({"clientError": str(error)})
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        if bridge:
            bridge.close()


if __name__ == "__main__":
    sys.exit(main())
