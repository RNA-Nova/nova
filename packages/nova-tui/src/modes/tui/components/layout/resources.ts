/**
 * ResourcesView：已加载资源区。
 *
 * compact：单行计数（`2 skills · 3 prompts · 16 commands · 2 packages`）；
 * expanded（ctrl+o 跟随全局展开态）：分组清单（名称逐行列出）。
 * 数据经 RPC 现拉（listSkills/listPromptTemplates/getCommands/pkgList），
 * 装配根在 runtime.start 后调 refresh()；quiet_startup 时随 header 区移除。
 */

import { Container, Text } from '@earendil-works/pi-tui';
import type { NovaUIRuntime } from 'nova-tui';

import { colors } from '../../themes/index.js';
import type { ExpansionState } from '../transcript/expansion.js';

interface ResourceGroups {
  skills: string[];
  prompts: string[];
  commands: string[];
  packages: string[];
}

export class ResourcesView extends Container {
  private groups: ResourceGroups | undefined;

  constructor(
    private readonly runtime: NovaUIRuntime,
    private readonly expansion: ExpansionState,
  ) {
    super();
  }

  /** 拉取资源清单并重建（启动后调用一次；/reload 后可再调）。 */
  async refresh(): Promise<void> {
    const [skills, prompts, commands, packages] = await Promise.all([
      this.runtime.invoke('listSkills').catch(() => undefined),
      this.runtime.invoke('listPromptTemplates').catch(() => undefined),
      this.runtime.invoke('getCommands').catch(() => undefined),
      this.runtime.invoke('pkgList').catch(() => undefined),
    ]);
    this.groups = {
      skills: this.namesOf(skills, 'skills'),
      prompts: this.namesOf(prompts, 'prompts'),
      commands: this.namesOf(commands, 'commands'),
      packages: this.packageNamesOf(packages),
    };
    this.rebuild();
  }

  private namesOf(result: unknown, key: string): string[] {
    if (typeof result !== 'object' || result === null) return [];
    const list = (result as Record<string, unknown>)[key];
    if (!Array.isArray(list)) return [];
    return list
      .map((item) => {
        if (typeof item === 'string') return item;
        if (typeof item === 'object' && item !== null) {
          const name = (item as Record<string, unknown>).name;
          if (typeof name === 'string') return name;
        }
        return undefined;
      })
      .filter((name): name is string => Boolean(name));
  }

  /** pkgList 线上形态是身份键控映射 {&lt;identity&gt;: {name, ...}}，不是数组包装。 */
  private packageNamesOf(result: unknown): string[] {
    if (typeof result !== 'object' || result === null) return [];
    return Object.values(result as Record<string, unknown>)
      .map((view) =>
        typeof view === 'object' && view !== null
          ? (view as Record<string, unknown>).name
          : undefined,
      )
      .filter((name): name is string => typeof name === 'string');
  }

  /** 展开态切换后重建（不重拉数据）。 */
  rebuild(): void {
    this.clear();
    if (!this.groups) return;
    const { skills, prompts, commands, packages } = this.groups;
    if (!this.expansion.expanded) {
      const counts = [
        `${skills.length} skills`,
        `${prompts.length} prompts`,
        `${commands.length} commands`,
        `${packages.length} packages`,
      ].join(colors.dim(' · '));
      this.addChild(new Text(` ${colors.dim(counts)}`, 1, 0));
      return;
    }
    const sections: Array<[string, string[]]> = [
      ['Skills', skills],
      ['Prompts', prompts],
      ['Commands', commands],
      ['Packages', packages],
    ];
    for (const [title, names] of sections) {
      this.addChild(new Text(` ${colors.muted(title)} ${colors.dim(`(${names.length})`)}`, 1, 0));
      for (const name of names) {
        this.addChild(new Text(`   ${colors.text(name)}`, 1, 0));
      }
    }
  }
}
