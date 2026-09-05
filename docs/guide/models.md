# 模型与鉴权

## 概念

- **provider**：模型供应商（内置 Volcengine、Moonshot AI 国际/国内、Kimi Coding 四家；任意 OpenAI 兼容端点可经 `models.json` 或扩展接入）；
- **模型引用**：`provider/model-id`（如 `volcengine/deepseek-v4-flash-260425`）；
- **鉴权与模型分离（credential-blind）**：api_key/Authorization 不写进模型对象，请求时才经鉴权链解析。

## 鉴权链（请求时依序解析）

1. 运行时覆盖（`/login` 设的 runtime key）；
2. 已存 credential（`~/.nova/agent/auth.json`）；
3. `models.json` / 扩展注册的 key；
4. 环境变量（按 provider 名映射，如 `VOLCENGINE_API_KEY`）；
5. OAuth 刷新（OAuth 登录的 provider 自动续期）。

## 配置方式

**`/login`**（会话内，推荐）：弹 provider 选择器 → 该 provider 支持 OAuth 走浏览器授权流，支持 API key 走掩码输入（不回显明文）。

**环境变量**：`export VOLCENGINE_API_KEY=...`（各 provider 变量名见 `nova_ai` 文档）。

**`~/.nova/agent/models.json`**（自定义/覆盖 provider 与模型）：

```json
{
  "providers": {
    "my-corp": {
      "base_url": "https://llm.corp.internal/v1",
      "api_key": "$CORP_LLM_KEY",
      "models": [{ "id": "corp-model-x", "name": "Corp X", "context_window": 262144 }]
    }
  }
}
```

`api_key` 值的解析语义：`$VAR` / `${VAR}` 为环境变量引用（缺失报错并指明变量名）；`!cmd` 前缀执行 shell 命令取输出（接密码管理器）；`$$` / `$!` 转义；其余按字面量。

## 默认模型解析链

会话初始模型按优先级：**CLI `--model` / scoped 池 → agent 组合声明的 `model:` 字段 → settings 的 `default_model` → 任一有鉴权可用的模型**。无鉴权/未知 provider 静默落回下一档。

## scoped 模型池

把若干模型编成"池"，运行中 `ctrl+p` 循环切换启用集与顺序（主模型不可用时按池顺序自动降级重试）。`/scoped-models` 打开池面板管理。

## thinking 级别

支持思考的模型可切 `default_thinking_level`（off/minimal/low/medium/high 等，按模型能力集）。会话内 `/model` 面板或设置面板切换；各级别对应的 token 预算可在 settings 的 `thinking_budgets` 调整。

## 用量与遥测

- footer 实时显示当轮 token 消耗（↑输入 ↓输出）与上下文占用百分比（`42%/262k(auto)` = 已用/窗口/压缩策略）；
- `/session` 看会话累计；
- 遥测本地记录在 `~/.nova/agent/logs/`（不外发）。
