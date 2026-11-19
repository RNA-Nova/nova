# Nova exec-server Relay 协议与部署

> Relay 为 exec-server 提供远程加密接入，不承载 Agent、模型、云任务或云主机概念。
> 端点行为对齐 codex `39f4277087` 的公开 Noise/Relay 实现；Registry、Rendezvous、
> 鉴权与部署为 Nova 自有实现。

## 组件

- `nova-exec-server`：私有执行节点；经 `--remote` 主动注册并连接 Rendezvous。
- `nova-exec-relay-server`：公网 Registry + Rendezvous；只路由 Noise 密文。
- `nova-exec-relay-client`：harness 端 stdio 桥；把 Noise Relay 转为原有 NDJSON JSON-RPC。
- `nova-dev-ssh-node` / `nova-dev-ssh-client`：独立开发期 OpenSSH 反向隧道，不进入 exec 协议。
- `nova-exec-server-relay-protocol`：Registry DTO、Noise 公钥与 Protobuf 帧正典。
- `nova-exec-server-relay-server`：自有 Registry/Rendezvous crate 与服务二进制。

## 线上层次

```text
exec JSON-RPC v1.13
  → Noise hybrid IK（X25519 + ML-KEM-768、AES-GCM、SHA-256）
  → RelayMessageFrame Protobuf（版本 1）
  → WebSocket / Nova Rendezvous
```

Nova 品牌域为 `nova-exec-server-relay-noise/v1`，Protobuf package 为
`nova.exec_server.relay.v1`。算法族与 `security_profile = noise_hybrid_ik_v1`
保持公开协议语义，不包含 ChatGPT、OpenAI 账号或 Codex 环境变量。

## Registry API

与公开锚点相同的 HTTP JSON 形状：

- `POST /cloud/environment/{id}/register`：节点 bearer token + executor 公钥；
- `POST /cloud/environment/{id}/connect`：harness bearer token + harness 公钥；
- `POST /cloud/environment/{id}/validate`：executor 验证 harness 公钥与一次性授权。

Registry 返回完整 Rendezvous URL。URL 中的短期能力参数只用于物理 WS 准入；
Noise 握手仍绑定 environment、registration、stream 和双方公钥。诊断输出必须
对 URL 与授权字段脱敏。

## Nova 自有安全边界

- enrollment 同时签发 node/harness token，SQLite 只保存 SHA-256 哈希；
- 再 enroll 表示轮换并作废旧注册，revoke 会断开失效代际；
- harness 授权有效期 60 秒，绑定公钥、环境和注册代际；
- 一个环境同时只允许一个执行节点；
- Rendezvous 有物理连接与单流两级有界背压；
- exec Rendezvous 不解析 exec JSON-RPC，也不提供任意 TCP 转发；独立 dev-ssh 面仅允许节点启动时固定的回环 OpenSSH 目标；
- 非公网回环部署必须配置 HTTPS `public_url`，TLS 可在受信反向代理终止。

## 部署

```bash
# 1. 创建或轮换环境；token 仅在命令输出中出现一次。
nova-exec-relay-server enroll \
  --environment-id machine-a --tenant-id default

# 2. 启动公共服务（生产使用 HTTPS public_url 与受信 TLS 入口）。
nova-exec-relay-server serve \
  --listen 127.0.0.1:8787 \
  --public-url http://127.0.0.1:8787

# 3. 私有节点主动出站注册。
nova-exec-server \
  --remote http://127.0.0.1:8787 \
  --environment-id machine-a \
  --registry-token '<node token>'

# 4. Python 调用方无需 Noise/Relay DTO。
nova-exec-relay-client \
  --registry-url http://127.0.0.1:8787 \
  --environment-id machine-a \
  --harness-token '<harness token>'
```

Python 更便捷地使用 `ExecutorClient.from_relay(...)`；token 经子进程环境变量
`NOVA_EXEC_SERVER_HARNESS_TOKEN` 传递，不进入 argv。

## 开发期 OpenSSH 反向隧道（独立数据面）

该面用于真机构建与测试，**不是 Codex 协议能力，也不恢复 bundle 的 `ssh://`
后端**。SSH 自身提供端到端加密；Relay 不终止 SSH、不接触密码或私钥，只转发
二进制帧。dev-ssh node/client token 与 exec token 分表、分权，client 授权 60 秒，
单节点最多 64 条流，节点、客户端和每流队列均有界。

```bash
# 1. 在 Relay 数据库中创建或轮换独立 dev-ssh 环境。
nova-exec-relay-server enroll-dev-ssh \
  --environment-id windows-dev --tenant-id default

# 2. 被控 Windows：OpenSSH Server 已启用，节点只连本机 sshd。
$env:NOVA_DEV_SSH_NODE_TOKEN='<node token>'
nova-dev-ssh-node \
  --registry-url https://relay.example.com \
  --environment-id windows-dev

# 3a. 控制端：本地回环端口方式。
export NOVA_DEV_SSH_CLIENT_TOKEN='<client token>'
nova-dev-ssh-client \
  --registry-url https://relay.example.com \
  --environment-id windows-dev \
  --listen 127.0.0.1:2222
ssh -p 2222 nova-test@127.0.0.1

# 3b. 或 ProxyCommand stdio 方式（token 同样从环境变量读取）。
Host windows-dev-relay
    User nova-test
    IdentityFile ~/.ssh/nova_dev_test
    IdentitiesOnly yes
    ProxyCommand nova-dev-ssh-client --registry-url https://relay.example.com --environment-id windows-dev --stdio

# 4. 吊销环境并失效旧授权；活动节点在授权检查周期内断开。
nova-exec-relay-server revoke-dev-ssh --environment-id windows-dev
```

被控机应使用专用测试账号、公钥-only OpenSSH 和严格 host-key 校验。节点只接受
回环 `target`（默认 `127.0.0.1:22`），客户端也只能监听回环地址；公网不开放任意
TCP CONNECT，客户端不能临时选择内网目标。节点断线后进程退出，常驻部署应由
Windows 服务管理器或进程监督器按策略重启。

## Direct / Host-accepted WebSocket

Direct 端点与 AWS SigV4 provider 按 codex 公开实现提供，使用外部兼容 Registry/acceptor；
Nova 自有 `nova-exec-relay-server` 不额外发明 Direct 数据面。Rust 宿主可通过
`EnvironmentManager::from_accepted_websocket` 接管已鉴权 socket，并用
`replace_accepted_websocket` 在断线后提供替换 socket、恢复原 exec 会话。

## 验证

- Noise/帧/排序/恢复单元测试来自公开锚点并改为 Nova 品牌金标；
- Registry 测试覆盖 enrollment、注册代际、授权和吊销；
- Rendezvous 测试覆盖路由所有权与断开通知；
- 真实端到端测试启动 Rust Registry/Rendezvous、远程 exec 节点和 Noise harness，
  在 Unix/Windows 分别选择 echo 命令并验证输出；
- dev-ssh 真实 E2E 启动 Relay、节点和两个客户端，验证双向字节流、独立 token、
  重复节点拒绝及吊销断连。
