# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/lang/zh-CN/).

## [Unreleased]

### Added
- 初始化仓库结构：nova_ai、nova_agent、nova_harness、nova_team 四个子包。
- 完善各子包的 `pyproject.toml`、`.gitignore` 与 `README.md`。

### Changed
- 暂无

### Fixed
- 暂无

## [0.1.0] - 2026-04-14

### Added
- nova_ai：LLM 统一抽象层、流式接口、模型注册表、内置厂商支持（OpenAI、Anthropic、Google、Volcengine）。
- nova_agent（nova_agent）：Agent 核心、事件系统、工具校验、异步循环。
- nova_harness：AgentSession、会话树管理、上下文压缩、资源加载、Computex 远程工具。
