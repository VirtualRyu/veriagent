# Desktop guide

The Windows companion provides policy editing, request previews, revision history, and an audit view. Codex connects to the local MCP server for document-sharing requests.

## Start an empty owner window

Run from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
.\.venv\Scripts\python.exe -m veriagent.desktop --home .demo-state
```

A fresh directory starts with empty trusted records. You can author and review policies immediately. Sharing also requires catalog entries and approvals in the selected owner state. To use existing state, relaunch with `--home` pointing to that directory. To select a persistent directory through Connection / State, first reopen without `--home` or `VERIAGENT_HOME` overrides.

`--home` selects an explicit directory. Resolution otherwise uses `VERIAGENT_HOME`, then saved per-user selection, then the application default. Development launchers select `runtime/` by default.

## Samples and checks

The historical seed-policy file is not included. `init-demo`, sample initialization without an active policy, and `python -m examples.demo` require that file.

Tests that require absent fixtures report explicit skips. The empty-state check uses an actual MCP process to verify that a request with missing metadata cannot execute. [Test commands](../README.md#tests)

## Connect Codex and apply rules

Review the registration command in Connection / State before updating the named `veriagent` MCP entry. GUI and server must use the same explicit owner directory. Reopen Codex after initial registration if needed. Each subsequent request reads the active policy revision.

Rules edits persist as a draft. Review/Apply shows the whole bundle and eight fact assignments before publication. Apply creates a new immutable revision. History restoration creates a new draft. Activity shows persisted facts and traces when recorded. HUMAN_REVIEW remains paused without approval/resume.

Run from source using the commands above. A compiled Windows release has not been validated on a clean machine. [Dependency notices](third-party-notices.md) · [Project overview](../README.md)
