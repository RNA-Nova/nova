# nova-executor-utils-pty

跨平台 PTY 抽象与进程会话管理：ConPTY（Windows）/ 原生 pty（Unix）统一封装、
输出多路接收、作业对象（Job Object）生命周期、Windows TTY 输入规范化。

派生自 OpenAI Codex 的 `codex-utils-pty`（Apache-2.0），按 nova-executor 的
命名与接口约定改造。
