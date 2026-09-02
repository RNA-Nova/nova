/** nova-client 公共出口。 */

// facade 与宿主接口（RuntimeHost 与进程内实现同居 runtime.ts；
// M3 WS 宿主落地时再立 hosts/ 目录）
export {
  NovaUIRuntime,
  type NovaUIRuntimeOptions,
  type QueueMode,
  type RuntimeHost,
  type ThinkingLevel,
} from './runtime.js';

// wire
export {
  WireClient,
  type ReverseFrame,
  type RuntimeEvent,
  type WireClientOptions,
  type WireParams,
  type WireResult,
} from './wire/client.js';
export {
  ReverseBridge,
  type ReverseChannel,
  type UINotice,
  type UIRequest,
} from './wire/bridge.js';
export {
  CapabilitySet,
  checkContractVersion,
  type HandshakeInfo,
} from './wire/capabilities.js';

// bus
export {
  NovaBus,
  type DerivedEventMap,
  type DerivedEventName,
  type RawEventHandler,
} from './bus.js';

// mirror（会话镜像）
export { MirrorStore, type HistoryEntry } from './mirror/store.js';
export {
  applyRuntimeEvent,
  createTranscriptState,
  type TranscriptState,
} from './mirror/mapping.js';
export type {
  ContentBlock,
  SessionSnapshot,
  SessionStatus,
  StoreChange,
  ToolCallCard,
  TranscriptEntry,
} from './mirror/types.js';

// presentation（声明式块 + slot 注册表 + 扩展 UI API）
export {
  createExtensionUIAPI,
  OVERLAY_WRAPPER,
  unwrapOverlay,
  type CustomBlockAdapter,
  type CustomBlockDef,
  type EditorFactory,
  type EntryRenderableComponent,
  type EntryRenderer,
  type ExtensionUIContext,
  type ExtensionCommandDef,
  type ExtensionSettingsAPI,
  type ExtensionShortcutHandler,
  type ExtensionStateAPI,
  type ExtensionUIAPI,
  type NovaOverlayAnchor,
  type NovaOverlayOptions,
  type OverlayRegistration,
  type RegionComponentFactory,
  type RegionContext,
  type RegionProducer,
} from './presentation/extension-api.js';
export {
  detailsOf,
  extractText,
  isComponentOutput,
  validateBlock,
  type BlockValidation,
  type BlockValidator,
  type DiffHunk,
  type DiffLine,
  type NovaBlock,
  type NovaRenderer,
  type PreviewComputer,
  type RendererEnv,
  type RendererInput,
  type RendererOutput,
  type RendererResultPart,
} from './presentation/blocks.js';
export {
  SlotRegistry,
  blockSlot,
  commandSlot,
  editorSlot,
  entrySlot,
  regionSlot,
  shortcutSlot,
  toolSlot,
  type SlotProducer,
  type SlotRegistration,
} from './presentation/slots.js';

// packages（已安装包索引与 npm 自愈）
export {
  PackageRegistry,
  type InstalledPackageInfo,
} from './packages/registry.js';
export { ensureNpmDependencies } from './packages/npm.js';
export { fetchPackageUpdateNotice } from './packages/updates.js';

// settings（扩展设置/内部 KV 子系统——Node 层存储）
export {
  UISettings,
  UIStateStore,
  type SettingDef,
  type SettingRegistration,
  type SettingValueType,
} from './settings/store.js';

// resources（呈现资源层：发现 → trust 过滤 → 加载管线）
export {
  discoverLooseAssets,
  discoverLooseUIAssets,
  discoverUIAssets,
  type DiscoverLooseAssetsOptions,
} from './resources/discovery.js';
export { partitionByTrust } from './resources/trust.js';
export {
  loadUIAssets,
  type ExtensionUIEntryFactory,
  type ResourceLoaderOptions,
} from './resources/loader.js';
export type {
  PackageUIAssets,
  ResourceCollision,
  ResourceDiagnostic,
  ResourceLoadResult,
  ResourceScope,
} from './resources/types.js';

// paths（前端域路径族——前后端分治 §9 唯一出处）与迁移
export {
  FRONTEND_HOST,
  projectFrontendDir,
  userAgentDir,
  userFrontendDir,
} from './paths.js';
export { migrateFrontendLayout } from './migration.js';
