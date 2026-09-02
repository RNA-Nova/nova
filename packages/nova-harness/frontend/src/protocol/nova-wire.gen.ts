/**
 * GENERATED — 请勿手改。
 *
 * 由 nova_harness 的线上契约导出生成：
 *   python -m nova_harness.core.rpc.protocol.schema_export
 * 类型真理在 Python 运行时（事件即线上事实），本文件是其构建期快照。
 */

export const NOVA_CONTRACT_MAJOR = 1;
export const NOVA_CONTRACT_MINOR = 3;

// ---- 模型定义 ----

export interface AbortResult {
  ok: boolean;
  reason: string | null;
}

export interface AbortUserToolParams {
  name?: string | null;
}

export interface AgentEndEvent {
  type: "agent_end";
  messages: AgentMessage[];
}

export interface AgentEntry {
  name: string;
  description: string;
  scope: string;
  origin: string;
  current: boolean;
}

export interface AgentListItem {
  name: string;
}

export interface AgentSettledEvent {
  type: "agent_settled";
}

export interface AgentStartEvent {
  type: "agent_start";
}

export interface AppendEntryParams {
  customType: string;
  data?: unknown | null;
}

export interface AppendEntryResult {
  ok: boolean;
  entryId: string;
}

export interface AssistantMessage {
  role: "assistant";
  content: (TextContent | ThinkingContent | ToolCall)[];
  api: "openai-completions" | "openai-responses" | "azure-openai-responses" | "openai-codex-responses" | "anthropic-messages" | "bedrock-converse-stream" | "google-generative-ai" | "google-gemini-cli" | "google-vertex" | string;
  provider: "amazon-bedrock" | "anthropic" | "google" | "google-gemini-cli" | "google-antigravity" | "google-vertex" | "openai" | "azure-openai-responses" | "openai-codex" | "github-copilot" | "xai" | "groq" | "cerebras" | "openrouter" | "vercel-ai-gateway" | "zai" | "zai-coding-cn" | "mistral" | "minimax" | "minimax-cn" | "huggingface" | "opencode" | "opencode-go" | "nvidia" | "moonshotai" | "moonshotai-cn" | "kimi-coding" | "volcengine" | string;
  model: string;
  usage: Usage;
  stopReason: "stop" | "length" | "toolUse" | "error" | "aborted";
  errorMessage: string | null;
  responseId: string | null;
  responseModel: string | null;
  diagnostics: Record<string, unknown>[] | null;
  timestamp: number;
}

export interface AutoCompactionEndEvent {
  type: "auto_compaction_end";
  result: CompactionResult | null;
  aborted: boolean;
  willRetry: boolean;
  errorMessage: string | null;
}

export interface AutoCompactionStartEvent {
  type: "auto_compaction_start";
  reason: "threshold" | "overflow";
}

export interface AutoRetryEndEvent {
  type: "auto_retry_end";
  success: boolean;
  attempt: number;
  finalError: string | null;
}

export interface AutoRetryStartEvent {
  type: "auto_retry_start";
  attempt: number;
  maxAttempts: number;
  delayMs: number;
  errorMessage: string;
}

export interface BranchSummaryEntry {
  id: string;
  parentId: string | null;
  timestamp: string;
  type: "branch_summary";
  fromId: string;
  summary: string;
  details: unknown | null;
  fromHook: boolean | null;
}

export interface CacheMissEvent {
  missedTokens: number;
  missedCost: number;
  idleMs: number;
  modelChanged: boolean;
  type: "cache_miss";
}

export interface CancelRequestParams {
  id: number;
}

export interface CancelRequestResult {
  ok: boolean;
  cancelled: boolean;
}

export interface CapabilitiesInfo {
  domains: string[];
  methods: string[];
}

export interface CapabilitySelection {
  resourceType: string;
  name: string;
  status: "ok" | "missing" | "disabled_by_settings" | "disabled_by_sdk";
}

export interface ChangeAgentParams {
  name: string;
}

export interface ChangeAgentResult {
  agentName: string;
  availableTools: ToolInfo[];
}

export interface ClearQueueResult {
  steering: string[];
  followUp: string[];
}

export interface CloneSessionResult {
  ok: boolean;
  cancelled: boolean | null;
  sessionId: string | null;
  sessionFile: string | null;
}

export interface CommandInfo {
  name: string;
  description: string | null;
  source: string;
  sourceInfo: unknown | null;
}

export interface CompactParams {
  customInstructions?: string | null;
}

export interface CompactResult {
  summary: string | null;
  firstKeptEntryId: string | null;
  tokensBefore: number | null;
  estimatedTokensAfter: number | null;
  details: unknown | null;
}

export interface CompactionEndEvent {
  type: "compaction_end";
  reason: "manual" | "threshold" | "overflow";
  result: unknown;
  aborted: boolean;
  willRetry: boolean;
  errorMessage: string | null;
}

export interface CompactionEntry {
  id: string;
  parentId: string | null;
  timestamp: string;
  type: "compaction";
  summary: string;
  firstKeptEntryId: string;
  tokensBefore: number;
  details: unknown | null;
  fromHook: boolean | null;
}

export interface CompactionResult {
  summary: string;
  firstKeptEntryId: string;
  tokensBefore: number;
  estimatedTokensAfter: number | null;
  details: unknown | null;
}

export interface CompactionStartEvent {
  type: "compaction_start";
  reason: "manual" | "threshold" | "overflow";
  customInstructions: string | null;
}

export interface Cost {
  input: number;
  output: number;
  cacheRead: number;
  cacheWrite: number;
  total: number;
}

export interface CreateSessionParams {
  cwd?: string | null;
  model?: string | Record<string, unknown> | null;
  thinkingLevel?: string | null;
  agentName?: string | null;
  sessionFlag?: string | null;
  continueLast?: boolean;
  agentDir?: string | null;
  sessionFile?: string | null;
  noSession?: boolean;
}

export interface CreateSessionResult {
  sessionId: string;
  sessionName: string | null;
  resumed: boolean;
}

export interface CredentialInfo {
  provider: string | null;
  type: string | null;
}

export interface CustomAgentMessage {
  [key: string]: unknown;
}

export interface CustomEntry {
  id: string;
  parentId: string | null;
  timestamp: string;
  type: "custom";
  customType: string;
  data: unknown | null;
}

export interface CustomMessage {
  customType: string;
  content: string | (TextContent | ImageContent)[];
  display: boolean;
  details: unknown | null;
  timestamp: number;
  role: "custom";
}

export interface CustomMessageEntry {
  id: string;
  parentId: string | null;
  timestamp: string;
  type: "custom_message";
  customType: string;
  content: string | (TextContent | ImageContent)[];
  details: unknown | null;
  display: boolean;
}

export interface CycleModelParams {
  direction?: "forward" | "backward";
}

export interface CycleModelResult {
  ok: boolean;
  model: ModelRef | null;
  thinkingLevel: string | null;
  isScoped: boolean | null;
}

export interface CycleThinkingLevelResult {
  ok: boolean;
  thinkingLevel: string | null;
  reason: string | null;
}

export interface DeleteSessionParams {
  path: string;
}

export interface DeleteSessionResult {
  deleted: boolean;
}

export interface DoneEvent {
  type: "done";
  reason: "stop" | "length" | "toolUse" | "error" | "aborted";
  message: AssistantMessage;
}

export interface EmptyParams {
  [key: string]: unknown;
}

export interface EntryAppendedEvent {
  entry: unknown | null;
  type: "entry_appended";
}

export interface ErrorEvent {
  type: "error";
  reason: "aborted" | "error";
  error: AssistantMessage;
}

export interface ExportSessionParams {
  path: string;
}

export interface ExportSessionResult {
  exportedTo: string;
}

export interface ExtensionErrorEvent {
  type: "extension_error";
  extensionPath: string;
  event: string;
  error: string;
  stack: string | null;
}

export interface ExtensionFlagInfo {
  name: string;
  description: string | null;
  type: string | null;
  default: unknown | null;
  value: unknown | null;
  extensionPath: string | null;
}

export interface FollowUpParams {
  text: string;
  images?: ImageContent[] | null;
}

export interface ForkParams {
  entryId: string;
  position?: "before" | "after";
}

export interface GetAgentsResult {
  agents: AgentEntry[];
}

export interface GetAuthStatusResult {
  credentials: CredentialInfo[];
}

export interface GetCommandsResult {
  commands: CommandInfo[];
}

export interface GetContextUsageResult {
  tokens: number | null;
  contextWindow: number | null;
  percent: number | null;
}

export interface GetExtensionFlagsResult {
  flags: ExtensionFlagInfo[];
}

export interface GetPersonasResult {
  personas: PersonaEntry[];
  override: string | null;
}

export interface GetSessionEntriesParams {
  offset?: number;
  limit?: number;
}

export interface GetSessionEntriesResult {
  entries: Record<string, unknown>[];
  total: number;
  offset: number;
}

export interface GetSettingsParams {
  cwd?: string | null;
}

export interface GetSettingsResult {
  settings: Record<string, unknown>;
}

export interface GetShortcutsResult {
  shortcuts: ShortcutInfo[];
}

export interface GetToolsResult {
  tools: ToolInfo[];
}

export interface ImageContent {
  type: "image";
  mimeType: string;
  data: string;
}

export interface ImportSessionParams {
  path: string;
  cwd?: string | null;
}

export interface ImportSessionResult {
  ok: boolean;
  cancelled: boolean | null;
  sessionId: string | null;
  sessionName: string | null;
}

export interface InitializeResult {
  version: string;
  contractVersionMajor: number;
  contractVersionMinor: number;
  capabilities: CapabilitiesInfo;
}

export interface InvokeShortcutParams {
  shortcut: string;
}

export interface InvokeUserToolParams {
  name: string;
  params?: Record<string, unknown> | null;
}

export interface InvokeUserToolResult {
  message: Record<string, unknown>;
}

export interface LabelEntry {
  id: string;
  parentId: string | null;
  timestamp: string;
  type: "label";
  targetId: string;
  label: string | null;
}

export type ListAgentsResult = AgentListItem[];

export interface ListModelsResult {
  models: ModelListItem[];
}

export interface ListPromptTemplatesResult {
  prompts: PromptTemplateInfo[];
}

export interface ListScopedModelsResult {
  models: ScopedModelItem[];
}

export interface ListSessionsParams {
  cwd?: string | null;
  scope?: "current" | "all";
}

export type ListSessionsResult = SessionListItem[];

export interface ListSkillsResult {
  skills: SkillInfo[];
}

export type ListUserToolsResult = Record<string, unknown>[];

export interface LoginParams {
  provider: string;
  authType?: "api_key" | "oauth";
}

export interface LoginResult {
  ok: boolean;
  provider: string;
  type: string | null;
}

export interface MessageEndEvent {
  type: "message_end";
  message: AgentMessage;
}

export interface MessageStartEvent {
  type: "message_start";
  message: AgentMessage;
}

export interface MessageUpdateEvent {
  type: "message_update";
  message: AgentMessage;
  assistantMessageEvent: StartEvent | TextStartEvent | TextDeltaEvent | TextEndEvent | ThinkingStartEvent | ThinkingDeltaEvent | ThinkingEndEvent | ToolCallStartEvent | ToolCallDeltaEvent | ToolCallEndEvent | DoneEvent | ErrorEvent;
}

export interface ModelChangeEntry {
  id: string;
  parentId: string | null;
  timestamp: string;
  type: "model_change";
  provider: string;
  modelId: string;
}

export interface ModelChangedEvent {
  type: "model_changed";
  model: unknown | null;
  previousModel: unknown | null;
  source: string;
}

export interface ModelListItem {
  provider: string;
  id: string;
  name: string;
  available: boolean;
  reasoning: boolean;
}

export interface ModelRef {
  provider: string;
  id: string;
}

export interface NavigateTreeParams {
  targetId: string;
  options?: Record<string, unknown> | null;
}

export interface NewSessionResult {
  sessionId: string;
  sessionName: string | null;
}

export interface OkResult {
  ok: boolean;
}

export interface PackageUpdateItem {
  source: string;
  displayName: string;
  type: string;
  scope: string | null;
}

export interface PersonaEntry {
  name: string;
  path: string;
  scope: string;
  origin: string;
  isOverride: boolean;
}

export interface PkgCheckUpdatesResult {
  updates: PackageUpdateItem[];
}

export interface PkgInstallParams {
  source: string;
  local?: boolean;
}

export interface PkgNameParams {
  nameOrSource: string;
  local?: boolean;
}

export interface PkgParams {
  local?: boolean;
}

export interface PkgUninstallResult {
  ok: boolean;
  messages: string[];
}

export type PkgUpdateResult = Record<string, unknown>[];

export interface PromptParams {
  text: string;
  images?: ImageContent[] | null;
  expandPromptTemplates?: boolean;
  streamingBehavior?: string | null;
}

export interface PromptTemplateInfo {
  name: string;
  description: string;
  argumentHint: string | null;
  source: string | null;
}

export interface ProviderParams {
  provider: string;
}

export interface ProviderResult {
  ok: boolean;
  provider: string;
}

export interface QueueUpdateEvent {
  type: "queue_update";
  steering: string[];
  followUp: string[];
}

export interface RenameSessionParams {
  path: string;
  name: string;
}

export interface RenameSessionResult {
  ok: boolean;
  sessionName: string | null;
}

export interface SaveAgentParams {
  name?: string | null;
}

export interface SaveAgentResult {
  name: string;
  savedTo: string;
  shadowed: boolean;
}

export interface ScopedModelInput {
  provider: string;
  modelId: string | null;
  id: string | null;
  thinkingLevel: string | null;
}

export interface ScopedModelItem {
  provider: string;
  id: string;
  thinkingLevel: string | null;
}

export interface SessionHeader {
  type: "session";
  version: number;
  id: string;
  timestamp: string;
  cwd: string;
  parentSession: string | null;
}

export interface SessionInfoChangedEvent {
  type: "session_info_changed";
  name: string | null;
  agent: string | null;
  personaOverride: string | null;
}

export interface SessionInfoEntry {
  id: string;
  parentId: string | null;
  timestamp: string;
  type: "session_info";
  name: string | null;
}

export interface SessionListItem {
  id: string;
  name: string;
  path: string;
  modified: number;
  messageCount: number;
  firstMessage: string;
  cwd: string;
  parentSessionPath: string | null;
}

export interface SessionMessageEntry {
  id: string;
  parentId: string | null;
  timestamp: string;
  type: "message";
  message: UserMessage | AssistantMessage | ToolResultMessage | CustomMessage | CustomAgentMessage;
}

export interface SessionReloadedEvent {
  type: "session_reloaded";
  reason: string;
}

export interface SessionReplacedEvent {
  type: "session_replaced";
  reason: string;
}

export interface SessionStateResult {
  sessionId: string;
  sessionFile: string | null;
  sessionName: string | null;
  cwd: string;
  model: ModelRef | null;
  thinkingLevel: string;
  supportsThinking: boolean;
  availableThinkingLevels: string[];
  activeTools: string[];
  messageCount: number;
  pendingMessageCount: number;
  steeringMessages: string[];
  followUpMessages: string[];
  isStreaming: boolean;
  isCompacting: boolean;
  isRetrying: boolean;
  autoRetryEnabled: boolean;
  autoCompactionEnabled: boolean;
  steeringMode: string;
  followUpMode: string;
  projectTrusted: boolean;
  leafId: string | null;
  allowedCommands: string[] | null;
  disabledCommands: string[];
  capabilityReport: CapabilitySelection[];
  agentName: string | null;
  personaOverride: string | null;
}

export interface SessionStatsResult {
  sessionId: string;
  sessionFile: string | null;
  userMessages: number;
  assistantMessages: number;
  toolCalls: number;
  toolResults: number;
  totalMessages: number;
  tokens: TokenUsageSummary | null;
  cost: number;
  cacheWaste: unknown | null;
}

export interface SetActiveToolsParams {
  toolNames?: string[] | null;
  tools?: string[] | null;
}

export interface SetActiveToolsResult {
  ok: boolean;
  activeTools: string[];
}

export interface SetApiKeyParams {
  provider: string;
  apiKey?: string | null;
}

export interface SetAutoCompactionEnabledParams {
  enabled: boolean;
}

export interface SetAutoCompactionEnabledResult {
  ok: boolean;
  autoCompactionEnabled: boolean;
}

export interface SetAutoRetryParams {
  enabled: boolean;
}

export interface SetAutoRetryResult {
  ok: boolean;
  autoRetryEnabled: boolean;
}

export interface SetExtensionFlagParams {
  name: string;
  value?: unknown | null;
}

export interface SetExtensionFlagResult {
  ok: boolean;
  name: string;
  value: unknown | null;
}

export interface SetFollowUpModeParams {
  mode: "all" | "one-at-a-time";
}

export interface SetFollowUpModeResult {
  ok: boolean;
  followUpMode: string;
}

export interface SetLabelParams {
  entryId: string;
  label?: string | null;
}

export interface SetModelParams {
  model: string | Record<string, unknown>;
}

export interface SetPersonaOverrideParams {
  name?: string | null;
}

export interface SetPersonaOverrideResult {
  ok: boolean;
  personaOverride: string | null;
}

export interface SetResourceExclusionParams {
  resourceType: string;
  name: string;
  cwd?: string | null;
}

export interface SetResourceExclusionResult {
  ok: boolean;
  patterns: string[];
}

export interface SetScopedModelsParams {
  models: ScopedModelInput[];
}

export interface SetScopedModelsResult {
  ok: boolean;
  count: number;
}

export interface SetSessionNameParams {
  name: string;
}

export interface SetSessionNameResult {
  ok: boolean;
  sessionName: string | null;
}

export interface SetSteeringModeParams {
  mode: "all" | "one-at-a-time";
}

export interface SetSteeringModeResult {
  ok: boolean;
  steeringMode: string;
}

export interface SetThinkingLevelParams {
  level: "off" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max";
}

export interface SetThinkingLevelResult {
  ok: boolean;
  thinkingLevel: string;
}

export interface ShortcutInfo {
  shortcut: string;
  description: string | null;
  extensionPath: string | null;
}

export interface SkillInfo {
  name: string;
  description: string;
  filePath: string | null;
  sourceLabel: string | null;
}

export interface SourceInfo {
  path: string;
  source: string;
  scope: "user" | "project" | "temporary";
  origin: "package" | "top-level" | "local" | "auto";
  baseDir: string | null;
}

export interface StartEvent {
  type: "start";
  partial: AssistantMessage;
}

export interface SteerParams {
  text: string;
  images?: ImageContent[] | null;
}

export interface SwitchSessionParams {
  path?: string | null;
  sessionId?: string | null;
  cwd?: string | null;
}

export interface SwitchSessionResult {
  ok: boolean;
  cancelled: boolean | null;
  sessionId: string | null;
  sessionName: string | null;
}

export interface SyncSessionParams {
  entriesOffset?: number;
  entriesLimit?: number;
}

export interface SyncSessionResult {
  state: Record<string, unknown>;
  entries: Record<string, unknown>[];
  total: number;
  entriesOffset: number;
  eventSeq: number;
}

export interface TextContent {
  type: "text";
  text: string;
  textSignature: string | null;
}

export interface TextDeltaEvent {
  type: "text_delta";
  contentIndex: number;
  delta: string;
  partial: AssistantMessage;
}

export interface TextEndEvent {
  type: "text_end";
  contentIndex: number;
  content: string;
  partial: AssistantMessage;
}

export interface TextStartEvent {
  type: "text_start";
  contentIndex: number;
  partial: AssistantMessage;
}

export interface ThinkingContent {
  type: "thinking";
  thinking: string;
  thinkingSignature: string | null;
  redacted: boolean;
}

export interface ThinkingDeltaEvent {
  type: "thinking_delta";
  contentIndex: number;
  delta: string;
  partial: AssistantMessage;
}

export interface ThinkingEndEvent {
  type: "thinking_end";
  contentIndex: number;
  content: string;
  partial: AssistantMessage;
}

export interface ThinkingLevelChangeEntry {
  id: string;
  parentId: string | null;
  timestamp: string;
  type: "thinking_level_change";
  thinkingLevel: "off" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max" | null;
}

export interface ThinkingLevelChangedEvent {
  type: "thinking_level_changed";
  level: "off" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max" | null;
}

export interface ThinkingStartEvent {
  type: "thinking_start";
  contentIndex: number;
  partial: AssistantMessage;
}

export interface TokenUsageSummary {
  inputTokens: number;
  outputTokens: number;
  cacheRead: number;
  cacheWrite: number;
  total: number;
}

export interface ToolCall {
  type: "toolCall";
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  thoughtSignature: string | null;
  partialArgs: string | null;
  streamIndex: number | null;
}

export interface ToolCallDeltaEvent {
  type: "toolcall_delta";
  contentIndex: number;
  delta: string;
  partial: AssistantMessage;
}

export interface ToolCallEndEvent {
  type: "toolcall_end";
  contentIndex: number;
  toolCall: ToolCall;
  partial: AssistantMessage;
}

export interface ToolCallStartEvent {
  type: "toolcall_start";
  contentIndex: number;
  partial: AssistantMessage;
}

export interface ToolExecutionEndEvent {
  type: "tool_execution_end";
  toolCallId: string;
  toolName: string;
  result: unknown;
  isError: boolean;
}

export interface ToolExecutionStartEvent {
  type: "tool_execution_start";
  toolCallId: string;
  toolName: string;
  args: unknown;
}

export interface ToolExecutionUpdateEvent {
  type: "tool_execution_update";
  toolCallId: string;
  toolName: string;
  args: unknown;
  partialResult: unknown;
}

export interface ToolInfo {
  name: string;
  description: string;
  parameters: Record<string, unknown> | null;
  promptSnippet: string | null;
  promptGuidelines: string[] | null;
  source: string | null;
  sourcePath: string | null;
  sourceInfo: SourceInfo | null;
}

export interface ToolResultMessage {
  role: "toolResult";
  toolCallId: string;
  toolName: string;
  content: (TextContent | ImageContent)[];
  details: Record<string, unknown> | null;
  isError: boolean;
  addedToolNames: string[] | null;
  timestamp: number;
}

export interface TurnEndEvent {
  type: "turn_end";
  message: AgentMessage;
  toolResults: ToolResultMessage[];
}

export interface TurnStartEvent {
  type: "turn_start";
}

export interface UpdateSettingsParams {
  settings: Record<string, unknown>;
  cwd?: string | null;
}

export interface UpdateSettingsResult {
  ok: boolean;
  settings: Record<string, unknown>;
}

export interface Usage {
  input: number;
  output: number;
  cacheRead: number;
  cacheWrite: number;
  cacheWrite1H: number | null;
  reasoning: number | null;
  totalTokens: number;
  cost: Cost;
}

export interface UserMessage {
  role: "user";
  content: string | (TextContent | ImageContent)[];
  timestamp: number;
}

export interface UserToolEvent {
  type: "user_tool";
  tool: string;
  event: string;
  data: unknown | null;
  callId: string | null;
}

// ---- Union 别名 ----

export type AgentMessage = UserMessage | AssistantMessage | ToolResultMessage | CustomAgentMessage;

// ---- 信封与根联合 ----

export type NovaEventEnvelope =
  | { type: "agent_start"; data: AgentStartEvent; seq: number; ts: number; sessionId: string | null }
  | { type: "agent_end"; data: AgentEndEvent; seq: number; ts: number; sessionId: string | null }
  | { type: "turn_start"; data: TurnStartEvent; seq: number; ts: number; sessionId: string | null }
  | { type: "turn_end"; data: TurnEndEvent; seq: number; ts: number; sessionId: string | null }
  | { type: "message_start"; data: MessageStartEvent; seq: number; ts: number; sessionId: string | null }
  | { type: "message_update"; data: MessageUpdateEvent; seq: number; ts: number; sessionId: string | null }
  | { type: "message_end"; data: MessageEndEvent; seq: number; ts: number; sessionId: string | null }
  | { type: "tool_execution_start"; data: ToolExecutionStartEvent; seq: number; ts: number; sessionId: string | null }
  | { type: "tool_execution_update"; data: ToolExecutionUpdateEvent; seq: number; ts: number; sessionId: string | null }
  | { type: "tool_execution_end"; data: ToolExecutionEndEvent; seq: number; ts: number; sessionId: string | null }
  | { type: "agent_settled"; data: AgentSettledEvent; seq: number; ts: number; sessionId: string | null }
  | { type: "auto_compaction_start"; data: AutoCompactionStartEvent; seq: number; ts: number; sessionId: string | null }
  | { type: "auto_compaction_end"; data: AutoCompactionEndEvent; seq: number; ts: number; sessionId: string | null }
  | { type: "auto_retry_start"; data: AutoRetryStartEvent; seq: number; ts: number; sessionId: string | null }
  | { type: "auto_retry_end"; data: AutoRetryEndEvent; seq: number; ts: number; sessionId: string | null }
  | { type: "model_changed"; data: ModelChangedEvent; seq: number; ts: number; sessionId: string | null }
  | { type: "queue_update"; data: QueueUpdateEvent; seq: number; ts: number; sessionId: string | null }
  | { type: "session_info_changed"; data: SessionInfoChangedEvent; seq: number; ts: number; sessionId: string | null }
  | { type: "session_reloaded"; data: SessionReloadedEvent; seq: number; ts: number; sessionId: string | null }
  | { type: "session_replaced"; data: SessionReplacedEvent; seq: number; ts: number; sessionId: string | null }
  | { type: "user_tool"; data: UserToolEvent; seq: number; ts: number; sessionId: string | null }
  | { type: "thinking_level_changed"; data: ThinkingLevelChangedEvent; seq: number; ts: number; sessionId: string | null }
  | { type: "compaction_start"; data: CompactionStartEvent; seq: number; ts: number; sessionId: string | null }
  | { type: "compaction_end"; data: CompactionEndEvent; seq: number; ts: number; sessionId: string | null }
  | { type: "entry_appended"; data: EntryAppendedEvent; seq: number; ts: number; sessionId: string | null }
  | { type: "cache_miss"; data: CacheMissEvent; seq: number; ts: number; sessionId: string | null }
  | { type: "extension_error"; data: ExtensionErrorEvent; seq: number; ts: number; sessionId: string | null };

export type NovaSessionEntry = SessionMessageEntry | ThinkingLevelChangeEntry | ModelChangeEntry | CompactionEntry | BranchSummaryEntry | CustomEntry | CustomMessageEntry | LabelEntry | SessionInfoEntry;

// ---- 方法形状 ----

export interface NovaWireMethodMap {
  "abort": { params: EmptyParams; result: AbortResult };
  "abortBranchSummary": { params: EmptyParams; result: OkResult };
  "abortCompaction": { params: EmptyParams; result: OkResult };
  "abortRetry": { params: EmptyParams; result: OkResult };
  "abortUserTool": { params: AbortUserToolParams; result: AbortResult };
  "appendEntry": { params: AppendEntryParams; result: AppendEntryResult };
  "cancelRequest": { params: CancelRequestParams; result: CancelRequestResult };
  "changeAgent": { params: ChangeAgentParams; result: ChangeAgentResult };
  "clearQueue": { params: EmptyParams; result: ClearQueueResult };
  "cloneSession": { params: EmptyParams; result: CloneSessionResult };
  "compact": { params: CompactParams; result: CompactResult };
  "createSession": { params: CreateSessionParams; result: CreateSessionResult };
  "cycleModel": { params: CycleModelParams; result: CycleModelResult };
  "cycleThinkingLevel": { params: EmptyParams; result: CycleThinkingLevelResult };
  "deleteSession": { params: DeleteSessionParams; result: DeleteSessionResult };
  "dispose": { params: EmptyParams; result: OkResult };
  "excludeResource": { params: SetResourceExclusionParams; result: SetResourceExclusionResult };
  "exportSession": { params: ExportSessionParams; result: ExportSessionResult };
  "followUp": { params: FollowUpParams; result: OkResult };
  "fork": { params: ForkParams; result: unknown };
  "getAuthStatus": { params: EmptyParams; result: GetAuthStatusResult };
  "getCommands": { params: EmptyParams; result: GetCommandsResult };
  "getContextUsage": { params: EmptyParams; result: GetContextUsageResult };
  "getExtensionFlags": { params: EmptyParams; result: GetExtensionFlagsResult };
  "getPersonas": { params: EmptyParams; result: GetPersonasResult };
  "getSessionAgents": { params: EmptyParams; result: GetAgentsResult };
  "getSessionEntries": { params: GetSessionEntriesParams; result: GetSessionEntriesResult };
  "getSessionState": { params: EmptyParams; result: SessionStateResult };
  "getSessionStats": { params: EmptyParams; result: SessionStatsResult };
  "getSettings": { params: GetSettingsParams; result: GetSettingsResult };
  "getShortcuts": { params: EmptyParams; result: GetShortcutsResult };
  "getTools": { params: EmptyParams; result: GetToolsResult };
  "importSession": { params: ImportSessionParams; result: ImportSessionResult };
  "includeResource": { params: SetResourceExclusionParams; result: SetResourceExclusionResult };
  "initialize": { params: EmptyParams; result: InitializeResult };
  "invokeShortcut": { params: InvokeShortcutParams; result: OkResult };
  "invokeUserTool": { params: InvokeUserToolParams; result: InvokeUserToolResult };
  "listAgents": { params: EmptyParams; result: ListAgentsResult };
  "listModels": { params: EmptyParams; result: ListModelsResult };
  "listPromptTemplates": { params: EmptyParams; result: ListPromptTemplatesResult };
  "listScopedModels": { params: EmptyParams; result: ListScopedModelsResult };
  "listSessions": { params: ListSessionsParams; result: ListSessionsResult };
  "listSkills": { params: EmptyParams; result: ListSkillsResult };
  "listUserTools": { params: EmptyParams; result: ListUserToolsResult };
  "login": { params: LoginParams; result: LoginResult };
  "logout": { params: ProviderParams; result: ProviderResult };
  "navigateTree": { params: NavigateTreeParams; result: unknown };
  "newSession": { params: EmptyParams; result: NewSessionResult };
  "pkgCheckUpdates": { params: EmptyParams; result: PkgCheckUpdatesResult };
  "pkgInfo": { params: PkgNameParams; result: unknown };
  "pkgInstall": { params: PkgInstallParams; result: unknown };
  "pkgList": { params: PkgParams; result: unknown };
  "pkgUninstall": { params: PkgNameParams; result: PkgUninstallResult };
  "pkgUpdate": { params: PkgNameParams; result: PkgUpdateResult };
  "prompt": { params: PromptParams; result: OkResult };
  "reload": { params: EmptyParams; result: OkResult };
  "renameSession": { params: RenameSessionParams; result: RenameSessionResult };
  "saveAgent": { params: SaveAgentParams; result: SaveAgentResult };
  "setActiveTools": { params: SetActiveToolsParams; result: SetActiveToolsResult };
  "setApiKey": { params: SetApiKeyParams; result: ProviderResult };
  "setAutoCompactionEnabled": { params: SetAutoCompactionEnabledParams; result: SetAutoCompactionEnabledResult };
  "setAutoRetry": { params: SetAutoRetryParams; result: SetAutoRetryResult };
  "setExtensionFlag": { params: SetExtensionFlagParams; result: SetExtensionFlagResult };
  "setFollowUpMode": { params: SetFollowUpModeParams; result: SetFollowUpModeResult };
  "setLabel": { params: SetLabelParams; result: OkResult };
  "setModel": { params: SetModelParams; result: OkResult };
  "setPersonaOverride": { params: SetPersonaOverrideParams; result: SetPersonaOverrideResult };
  "setScopedModels": { params: SetScopedModelsParams; result: SetScopedModelsResult };
  "setSessionName": { params: SetSessionNameParams; result: SetSessionNameResult };
  "setSteeringMode": { params: SetSteeringModeParams; result: SetSteeringModeResult };
  "setThinkingLevel": { params: SetThinkingLevelParams; result: SetThinkingLevelResult };
  "shutdown": { params: EmptyParams; result: OkResult };
  "steer": { params: SteerParams; result: OkResult };
  "switchSession": { params: SwitchSessionParams; result: SwitchSessionResult };
  "syncSession": { params: SyncSessionParams; result: SyncSessionResult };
  "updateSettings": { params: UpdateSettingsParams; result: UpdateSettingsResult };
}

export type NovaWireMethod = keyof NovaWireMethodMap;
