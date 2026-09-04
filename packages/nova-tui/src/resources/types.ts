/**
 * 呈现资源层的类型词汇（resources/ 子系统）。
 *
 * 定位（设计定案）：本层是**管线**（发现 → 过滤 → 加载 → 注册），
 * 注册完即放手——资源状态的家在 store（mirror）与 slots（注册表），
 * 不做全量持有的"资源仓库"（pi DefaultResourceLoader 的模式明确不拿）。
 */

/** 资源作用域（与后端 PackageView.scope 对齐——只有 user/project 两档；
 * 覆盖优先级 user < project。"builtin" 不是包的作用域——宿主自带贡献
 * （块适配器/包面板等）的 builtin 是 slot 注册的 source 词汇，与此无涉）。 */
export type ResourceScope = 'user' | 'project';

/** 一个包贡献的 ui/ 资产清单（发现阶段的产物）。 */
export interface PackageUIAssets {
  /**
   * 来源标签（slot 注册的 source，覆盖诊断用）。已安装包为包名；
   * 散养根（``frontend/<host>/`` 直挂资产，无包身份）即 scope 值
   * （``user`` / ``project``）。
   */
  packageName: string;
  scope: ResourceScope;
  installPath: string;
  /** 渲染器：工具名 → 文件绝对路径（``ui/renderers/<tool>.ts``）。 */
  renderers: Map<string, string>;
  /**
   * 文件约定对话框：对话框名 → 文件绝对路径（``dialogs/<name>.ts``，
   * 默认导出 DialogFactory）。散养根专属通道——包的对话框走
   * ``index.ts`` 编程式注册（registerDialog），不经文件约定。
   */
  dialogs?: Map<string, string>;
  /** 全量扩展入口绝对路径（``ui/index.ts``，不存在则 undefined）。 */
  extensionEntry?: string;
  /** 主题资产：主题名 → 文件绝对路径（``ui/themes/*.json``——纯数据资产）。 */
  themes: Map<string, string>;
  /** 包根有 package.json 但 node_modules 缺失（需 npm 自愈）。 */
  needsNpmInstall: boolean;
}

/** 覆盖碰撞：同名键的胜者/败者与来源在案。 */
export interface ResourceCollision {
  kind: string;
  name: string;
  /** 胜者（后注册者）来源。 */
  winner: string;
  /** 败者（被覆盖者）来源。 */
  loser: string;
}

/** 资源诊断：加载失败 / 覆盖碰撞 / trust 剔除——全部结构化（非日志）。 */
export interface ResourceDiagnostic {
  type: 'warning' | 'error' | 'collision' | 'trust-skipped';
  message: string;
  path?: string;
  collision?: ResourceCollision;
}

import type { ThemeJson } from '../presentation/theme-json.js';

/** 资源加载结果（管线的统一产出）。 */
export interface ResourceLoadResult {
  /** 成功加载（含耗时毫秒——包多后排查启动性能的观测点）。 */
  loaded: { name: string; source: string; durationMs: number }[];
  diagnostics: ResourceDiagnostic[];
  /** 包内主题资产（ui/themes/*.json——校验通过的主题，宿主注册用）。 */
  themes: Map<string, ThemeJson>;
}
