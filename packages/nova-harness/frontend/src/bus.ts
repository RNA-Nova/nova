/**
 * NovaBus：本层的事件脊柱（观察式 pub/sub）。
 *
 * 货源两个：
 * - **原始事件**：后端 Bus 2 事件经 wire 注入（``publish``）；
 * - **派生事件**：mirror/presentation 在状态迁移确定后发布的便利词汇
 *   （``publishDerived``，如 ``session:synced``）。
 *
 * 纪律（设计 v3 §5）：
 * - 观察式：handler 一律 ``void``、fire-and-forget、异常隔离（同步抛错与
 *   async 观察者的 rejection 同样兜住）——拦截语义归 Python Bus 3，
 *   本层永不做结果合并；
 * - **mirror 是特权订阅者**：按序、每条恰好一次、先于一切观察者
 *   （状态正确性攸关——mirror 的异常不吞，响亮冒泡）；
 * - 前端输入事件（键位/命令）不进 bus。
 */

import type { NovaEventEnvelope } from './protocol/nova-wire.gen.js';

/** 原始事件 handler（观察式或 mirror 特权）。 */
export type RawEventHandler = (event: NovaEventEnvelope) => void;

/** 派生事件负载表（便利词汇的清单与载荷类型）。 */
export interface DerivedEventMap {
  /** mirror 全量同步完成（连接/恢复/新会话后）。 */
  'session:synced': undefined;
  /** 一轮 agent 工作开始（agent_start）。 */
  'turn:started': undefined;
  /** 一轮 agent 工作结束（agent_end）。 */
  'turn:ended': undefined;
  /** 扩展设置键变更（前端域 settings.json 写入后）。 */
  'settings:changed': { key: string; value: unknown };
}

export type DerivedEventName = keyof DerivedEventMap;

type DerivedHandler<T> = (payload: T) => void;

const WILDCARD = '*';

export class NovaBus {
  private readonly mirrorHandlers: RawEventHandler[] = [];
  private readonly rawObservers = new Map<string, Set<RawEventHandler>>();
  private readonly derivedObservers = new Map<string, Set<DerivedHandler<unknown>>>();

  /**
   * mirror 特权订阅：先于一切观察者、按发布顺序逐条调用。
   * handler 异常**不隔离**（mirror 状态损坏继续跑比崩溃更糟）——响亮冒泡。
   */
  subscribeMirror(handler: RawEventHandler): () => void {
    this.mirrorHandlers.push(handler);
    return () => {
      const index = this.mirrorHandlers.indexOf(handler);
      if (index >= 0) this.mirrorHandlers.splice(index, 1);
    };
  }

  /** 观察式订阅原始事件（``type`` 为事件类型或 ``*`` 通配）。 */
  on(type: NovaEventEnvelope['type'] | '*', handler: RawEventHandler): () => void {
    let set = this.rawObservers.get(type);
    if (!set) {
      set = new Set();
      this.rawObservers.set(type, set);
    }
    set.add(handler);
    return () => set.delete(handler);
  }

  /** 观察式订阅派生事件。 */
  onDerived<K extends DerivedEventName>(
    name: K,
    handler: DerivedHandler<DerivedEventMap[K]>,
  ): () => void {
    let set = this.derivedObservers.get(name);
    if (!set) {
      set = new Set();
      this.derivedObservers.set(name, set);
    }
    const stored = handler as DerivedHandler<unknown>;
    set.add(stored);
    return () => set.delete(stored);
  }

  /** wire 注入点：原始事件上线（mirror 先行，观察者随后、异常隔离）。 */
  publish(event: NovaEventEnvelope): void {
    for (const handler of [...this.mirrorHandlers]) {
      handler(event);
    }
    this.notifyRaw(event.type, event);
    this.notifyRaw(WILDCARD, event);
  }

  /** 派生事件发布（mirror/presentation 的便利词汇）。 */
  publishDerived<K extends DerivedEventName>(name: K, payload: DerivedEventMap[K]): void {
    const set = this.derivedObservers.get(name);
    if (!set) return;
    for (const handler of [...set]) {
      this.isolate(`派生事件 ${name}`, () => handler(payload));
    }
  }

  private notifyRaw(type: string, event: NovaEventEnvelope): void {
    const set = this.rawObservers.get(type);
    if (!set) return;
    for (const handler of [...set]) {
      this.isolate(`事件 ${type}`, () => handler(event));
    }
  }

  /**
   * 观察者异常隔离：同步抛错捕获；返回 Promise 的（async 观察者）
   * 挂 .catch 兜 rejection——"观察者弄不垮主干"对异步同样成立。
   * mirror 档刻意不走这里（异常响亮冒泡）。
   */
  private isolate(name: string, run: () => unknown): void {
    try {
      const result = run();
      if (result instanceof Promise) {
        result.catch((error: unknown) => {
          console.error(`[nova-bus] ${name} 的观察者异步异常：`, error);
        });
      }
    } catch (error) {
      console.error(`[nova-bus] ${name} 的观察者异常：`, error);
    }
  }
}
