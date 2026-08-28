/**
 * NovaKeybindingsManager：三级合并 + 诊断 + reload。
 *
 * extends pi-tui KeybindingsManager（matches/getKeys/conflicts 内建），
 * 加上：user（frontend/tui/ 半区）+ project（.nova/frontend/tui/）两级
 * keybindings.json 的加载合并、加载诊断透出、运行期 reload。
 *
 * **本层不持有任何具体键位表**（机械归运行时，表归宿主——TUI 表在
 * ``modes/tui/keymap/tables.ts``，装配根经 ``create({defaults, reserved})``
 * 注入）。保留键位查询（restrictOverride 判据）同样以注入为准。
 */

import {
  KeybindingsManager,
  type KeybindingDefinitions,
  type KeybindingsConfig,
} from '@earendil-works/pi-tui';

import {
  loadKeybindingsFile,
  mergeKeybindingsConfigs,
  projectKeybindingsPath,
  userKeybindingsPath,
} from './loader.js';

export interface KeybindingsCreateResult {
  manager: NovaKeybindingsManager;
  diagnostics: string[];
}

/** 键位文件路径（测试可注入覆盖，生产走默认约定路径）。 */
export interface KeybindingsPaths {
  user?: string;
  project?: string;
}

/** 装配参数：宿主默认表 + 保留键位清单（+ 可选路径覆盖）。 */
export interface KeybindingsCreateOptions {
  /** 宿主默认键位表（如 TUI 的 NOVA_KEYBINDINGS——pi-tui 内建 + app 级）。 */
  defaults: KeybindingDefinitions;
  /** 保留键位清单（扩展快捷键禁覆盖的动作集——restrictOverride 判据）。 */
  reserved: readonly string[];
  paths?: KeybindingsPaths;
}

export class NovaKeybindingsManager extends KeybindingsManager {
  private constructor(
    private readonly defaults: KeybindingDefinitions,
    private readonly reserved: readonly string[],
    userBindings: KeybindingsConfig,
    private readonly paths: Required<KeybindingsPaths>,
  ) {
    super(defaults, userBindings);
  }

  /** 装配入口：三级合并（builtin < user < project）+ 诊断收集。 */
  static create(
    cwd: string,
    options: KeybindingsCreateOptions,
  ): KeybindingsCreateResult {
    const resolved = {
      user: options.paths?.user ?? userKeybindingsPath(),
      project: options.paths?.project ?? projectKeybindingsPath(cwd),
    };
    const user = loadKeybindingsFile(resolved.user, options.defaults);
    const project = loadKeybindingsFile(resolved.project, options.defaults);
    const merged = mergeKeybindingsConfigs(user.config, project.config);
    return {
      manager: new NovaKeybindingsManager(
        options.defaults,
        options.reserved,
        merged,
        resolved,
      ),
      diagnostics: [...user.diagnostics, ...project.diagnostics],
    };
  }

  /** 重读两级文件（运行期刷新）；返回新诊断。 */
  reload(): string[] {
    const user = loadKeybindingsFile(this.paths.user, this.defaults);
    const project = loadKeybindingsFile(this.paths.project, this.defaults);
    this.setUserBindings(mergeKeybindingsConfigs(user.config, project.config));
    return [...user.diagnostics, ...project.diagnostics];
  }

  /** 保留键位查询（扩展快捷键注册的 restrictOverride 判据——注入清单）。 */
  isReserved(actionId: string): boolean {
    return this.reserved.includes(actionId);
  }
}
