/**
 * settings/state 子系统（design.md §7——扩展配置与数据的一等公民底座）。
 *
 * 边界：本系统是 **Node 层**的存储（前端域 ``~/.nova/agent/frontend/tui/``
 * 下的 `settings.json` 与 `state/<ns>.json`——前后端分治 §9）——后端
 * settings.json 不背（它的 schema 未知键拒绝，且这是前端域；两个文件主权分明）。
 *
 * - ``UISettings``：扩展声明的用户设置键（define 注册 + get/set——按注册表
 *   校验类型、并入默认值；变更经回调发布，宿主接 bus）；
 * - ``UIStateStore``：扩展内部 KV（每扩展命名空间隔离一个文件）——todo 条目、
 *   bookmark 列表这类"扩展自己记来干活"的数据，不进设置面板。
 *
 * 判别一句话：用户会想在设置面板里调的 → settings；扩展自己记的 → state。
 */

import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

import { userFrontendDir } from '../paths.js';

// ---------------------------------------------------------------------------
// 设置键注册表
// ---------------------------------------------------------------------------

export type SettingValueType = 'string' | 'number' | 'boolean';

export interface SettingDef {
  type: SettingValueType;
  default: unknown;
  description?: string;
  /** 声明者（扩展包名——冲突诊断用）。 */
  owner: string;
}

export interface SettingRegistration {
  key: string;
  def: SettingDef;
}

// ---------------------------------------------------------------------------
// UISettings（扩展设置键 + 前端域 settings.json 持久化）
// ---------------------------------------------------------------------------

export class UISettings {
  private readonly registry = new Map<string, SettingDef>();
  private values: Record<string, unknown> = {};
  private readonly changeListeners: Array<(key: string, value: unknown) => void> = [];

  constructor(private readonly filePath: string) {
    this.values = this.readFile();
  }

  /** 默认路径：~/.nova/agent/frontend/tui/settings.json（前端域 §9）。 */
  static defaultPath(): string {
    return join(userFrontendDir(), 'settings.json');
  }

  /**
   * 声明设置键（api.settings.define）。重复声明：同 owner 幂等重载，
   * 异 owner 冲突返回 false（诊断归调用方收集）。
   */
  define(key: string, def: Omit<SettingDef, 'owner'>, owner: string): boolean {
    const existing = this.registry.get(key);
    if (existing !== undefined && existing.owner !== owner) return false;
    this.registry.set(key, { ...def, owner });
    return true;
  }

  /** 注册表清单（设置面板/诊断用）。 */
  registrations(): SettingRegistration[] {
    return [...this.registry.entries()].map(([key, def]) => ({ key, def }));
  }

  /** 读设置（未显式设置时并入注册默认值；未声明的键返回 undefined）。 */
  get<T = unknown>(key: string): T | undefined {
    if (key in this.values) return this.values[key] as T;
    const def = this.registry.get(key);
    return def?.default as T | undefined;
  }

  /** 写设置（已声明键校验类型；未声明键拒绝——返回 false）。 */
  set(key: string, value: unknown): boolean {
    const def = this.registry.get(key);
    if (def === undefined) return false;
    const actualType = typeof value;
    if (actualType !== def.type) return false;
    this.values[key] = value;
    this.persist();
    for (const listener of this.changeListeners) listener(key, value);
    return true;
  }

  /** 变更订阅（宿主接 bus 发布）。 */
  onChange(listener: (key: string, value: unknown) => void): void {
    this.changeListeners.push(listener);
  }

  private readFile(): Record<string, unknown> {
    if (!existsSync(this.filePath)) return {};
    try {
      const parsed = JSON.parse(readFileSync(this.filePath, 'utf-8')) as unknown;
      return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
        ? (parsed as Record<string, unknown>)
        : {};
    } catch {
      return {};
    }
  }

  /** 原子写（tmp + rename——防半写文件）。 */
  private persist(): void {
    mkdirSync(dirname(this.filePath), { recursive: true });
    const tmp = `${this.filePath}.tmp`;
    writeFileSync(tmp, JSON.stringify(this.values, null, 2), 'utf-8');
    renameSync(tmp, this.filePath);
  }
}

// ---------------------------------------------------------------------------
// UIStateStore（扩展内部 KV——frontend/tui/state/<namespace>.json 每扩展一个文件）
// ---------------------------------------------------------------------------

export class UIStateStore {
  private readonly cache = new Map<string, Record<string, unknown>>();

  constructor(private readonly dirPath: string) {}

  /** 默认目录：~/.nova/agent/frontend/tui/state/（前端域 §9）。 */
  static defaultDir(): string {
    return join(userFrontendDir(), 'state');
  }

  get<T = unknown>(namespace: string, key: string): T | undefined {
    return this.readNamespace(namespace)[key] as T | undefined;
  }

  set(namespace: string, key: string, value: unknown): void {
    const data = this.readNamespace(namespace);
    data[key] = value;
    this.persist(namespace, data);
  }

  /** 命名空间全量（扩展自查用）。 */
  all(namespace: string): Record<string, unknown> {
    return { ...this.readNamespace(namespace) };
  }

  private fileOf(namespace: string): string {
    // 命名空间净化（防路径逃逸：包名只留安全字符）
    const safe = namespace.replace(/[^a-zA-Z0-9._-]/g, '_');
    return join(this.dirPath, `${safe}.json`);
  }

  private readNamespace(namespace: string): Record<string, unknown> {
    const cached = this.cache.get(namespace);
    if (cached !== undefined) return cached;
    const file = this.fileOf(namespace);
    let data: Record<string, unknown> = {};
    if (existsSync(file)) {
      try {
        const parsed = JSON.parse(readFileSync(file, 'utf-8')) as unknown;
        if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
          data = parsed as Record<string, unknown>;
        }
      } catch {
        data = {};
      }
    }
    this.cache.set(namespace, data);
    return data;
  }

  private persist(namespace: string, data: Record<string, unknown>): void {
    mkdirSync(this.dirPath, { recursive: true });
    const file = this.fileOf(namespace);
    const tmp = `${file}.tmp`;
    writeFileSync(tmp, JSON.stringify(data, null, 2), 'utf-8');
    renameSync(tmp, file);
  }
}
