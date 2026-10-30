---
name: rerun
description: Build or rerun an Xcode project through Apple's native MCP server, preserving the running scheme and device or the currently selected destination when idle. Use for requests such as "rerun", "run again", "rebuild", and Xcode build verification after code changes
---

# Rerun

Use Xcode's native MCP server for every build and rerun in this workflow

## Connect to the existing Xcode workspace

- Prefer native Xcode MCP tools already available in the session
- Discover the live tool schemas rather than assuming signatures from another Xcode version
- Target the running Xcode application, not an independent background workspace
- When native tools are not exposed, use the bundled [stdio bridge client](scripts/xcode_mcp.py) to call the same native MCP tools through `xcrun mcpbridge`
- Set `MCP_XCODE_PID` to the actual running Xcode PID and keep one `MCP_XCODE_SESSION_ID` for the connection
- An unqualified bridge can connect to the background XcodeService instead, which may have different scheme/device selections and workspace state
- Do not silently fall back to computer use, AppleScript, direct `xcodebuild`, `devicectl`, or `simctl` for builds or reruns
- If access is denied, report the exact native approval requirement and let the user approve it — do not grant permissions using sudo or change security settings

## Resolve the target before acting

1. Discover open windows/workspaces using the native tool catalog
2. Match the intended project by its full path and carry its returned identifier into every subsequent call
3. Use native scheme/destination tools to read the current state
4. For a rerun, preserve the scheme and destination of the running app when native session information or verified current task context identifies them; otherwise use the active scheme and selected destination
5. If a known running session differs from the selection, restore that running session's scheme and device using native switch tools before rerunning
6. If multiple projects or running sessions are plausible and the intended target cannot be determined, ask one focused clarification
7. Verify both scheme and destination immediately before building/running — switching schemes can automatically change the destination

Never hard-code a project, scheme, PID, tab identifier, or device from a previous conversation
Do not substitute a generic destination or another available device when the selected device is unavailable
Distinguish a physical device from a simulator with the same display name using `isActive`, `isSimulator`, and `group`
If the switch tool cannot disambiguate identical names, stop before launching the wrong target
Do not boot a simulator unless the user requested it or explicitly chose it for this run

## Build or rerun

- Build only: call native `BuildProject` with the discovered workspace/tab identifier
- Rerun: call native `RunProject` with that identifier, equivalent to Xcode Run
- Preserve the active scheme’s Run action “Debug executable” setting unless the user explicitly requests a debugger override
- Read that setting through native scheme inspection when available; otherwise inspect the matching `.xcscheme` file read-only, resolving the active scheme’s container and user/shared scheme before using its `LaunchAction` debugger configuration
- Set `attachDebugger` to the verified setting: `true` when enabled, `false` when disabled — do not infer it from Debug/Release names or a previous agent launch
- Omit `attachDebugger` only if the discovered native tool explicitly documents that omission preserves the scheme setting; a fixed default such as `false` does not preserve it
- If the setting cannot be established and the native tool cannot inherit it, ask one focused question before launching instead of choosing a debugger mode
- Do not edit the scheme to match the tool’s default
- Let Xcode use its existing configuration, Derived Data, package checkouts, and compilation cache
- Do not clean, delete caches, create a fresh Derived Data directory, change signing, or update packages merely to rerun
- Do not call `StopProject` after a successful launch — the requested result is an app left running
- Keep tool calls sequential when they change the scheme, destination, or running app
- Wait for the native result, sharing a brief update if the build takes more than a minute
- Success requires a successful build/launch result, not merely a submitted request
- On failure, inspect native build logs or console output and report the concrete blocker
- Do not repeatedly rebuild an unchanged failure or retry a timed-out run until its actual outcome is established
- End with the scheme, device, and outcome

## Stdio client fallback

This is a transport for Apple's MCP server, not an alternative build implementation
It discovers a single running Xcode PID automatically and refuses to guess when several are running

Start a persistent terminal session (PTY) so stdin stays open:

```sh
python3 -u <skill-directory>/scripts/xcode_mcp.py
```

Use `--pid <observed-Xcode-PID>` when multiple Xcode instances exist
Send one JSON object per line, inspect each response before dependent actions:

```json
{"method":"tools/list"}
{"name":"XcodeListWindows","arguments":{}}
```

Read the returned tool schemas before sending the remaining requests
For versions exposing `tabIdentifier`, first verify the scheme and destination:

```json
{"name":"XcodeListRunDestinations","arguments":{"tabIdentifier":"<discovered-tab>"}}
```

After resolving “Debug executable” as described above, call `RunProject` with the discovered tab identifier and the matching boolean `attachDebugger` value
Use `BuildProject` instead of `RunProject` for build-only requests
The script prints one JSON response per request and retains the bridge session across calls
Poll the terminal session in bounded intervals while an action is pending; do not submit the same action again
After completion, close stdin to release the bridge without stopping the app
A timeout means the outcome is unknown, not that Xcode cancelled the operation
