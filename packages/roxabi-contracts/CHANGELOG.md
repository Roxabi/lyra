# Changelog

## [0.3.0](https://github.com/Roxabi/lyra/compare/roxabi-contracts/v0.2.0...roxabi-contracts/v0.3.0) (2026-05-18)


### Features

* **contracts:** add optional `agent_name` and `agent_email` fields to `CliCmdPayload` for per-session git committer attribution ([#1150](https://github.com/Roxabi/lyra/issues/1150)). Additive, non-security-bearing — older consumers ignore the fields per the package's forward-compat policy (`extra='ignore'`).


## [0.2.0](https://github.com/Roxabi/lyra/compare/roxabi-contracts/v0.1.0...roxabi-contracts/v0.2.0) (2026-04-17)


### Features

* **contracts:** port voice domain — models + subjects + fixtures ([#763](https://github.com/Roxabi/lyra/issues/763)) ([#777](https://github.com/Roxabi/lyra/issues/777)) ([c632d07](https://github.com/Roxabi/lyra/commit/c632d07b1d3aad1f7a51f51790d6d171d2e75796))
* **contracts:** scaffold packages/roxabi-contracts skeleton + envelope.py ([#771](https://github.com/Roxabi/lyra/issues/771)) ([cffec07](https://github.com/Roxabi/lyra/commit/cffec072a3315daa6cc3753f68785d3205747a01))


### Bug Fixes

* **contracts:** add reportMissingImports directive for voice fixtures ([67160a0](https://github.com/Roxabi/lyra/commit/67160a0e44273b074f6b05c8f4c66e1184f54727))
