/**
 * WireClient：与后端（agent 运行时）的唯一传输接触面。
 *
 * 线上契约（架构 2.0 哑管道，NDJSON over stdio）：
 * - 命令/快照：JSON-RPC request → response（按 id 配对）；
 * - 事件：服务器发 ``agent/event`` notification，params 为 ``{type, data}`` 信封；
 * - 反向通道：服务器主动发的其他 method 帧（``ui/request``、``ui/notify``，
 *   以及 M4 的 ``tool/invoke`` 家族）一律交给反向槽，本模块不解释。
 *
 * 纪律：本模块只懂帧与生命周期——不懂事件语义、不懂呈现；
 * 后端实现语言不透出（``command`` 换成任意语言的二进制即可，契约一致即可）。
 */

import { spawn, type ChildProcess } from 'node:child_process';
import { createInterface } from 'node:readline';
import type {
  NovaEventEnvelope,
  NovaWireMethod,
  NovaWireMethodMap,
} from '../protocol/nova-wire.gen.js';

/** 哑管道事件信封：线上契约的生成类型（Python 事件即事实，构建期快照）。 */
export type RuntimeEvent = NovaEventEnvelope;

/** 方法 params 形状（契约生成，调用方省略带默认值的字段）。 */
export type WireParams<M extends NovaWireMethod> = NovaWireMethodMap[M]['params'];
/** 方法 result 形状（契约生成）。 */
export type WireResult<M extends NovaWireMethod> = NovaWireMethodMap[M]['result'];

/** 反向通道帧（后端 → 前端发起的请求或通知，本层不解释载荷）。 */
export interface ReverseFrame {
  /** 请求 id（``ui/request`` 等需要应答的帧携带；通知类帧为 undefined）。 */
  id?: string;
  method: string;
  params?: Record<string, unknown>;
}

interface RpcResponse {
  jsonrpc: '2.0';
  id: number | string;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
}

interface RpcInbound {
  jsonrpc: '2.0';
  method: string;
  params?: unknown;
}

export interface WireClientOptions {
  /**
   * 后端启动命令（含参数）。默认 Python harness；
   * 其他语言的后端实现（Rust/Go/…）换成其二进制即可——契约相同，客户端无感知。
   */
  command?: string[];
  /** 子进程工作目录（会话 cwd）。 */
  cwd?: string;
  /** 透传给子进程的额外环境变量。 */
  env?: Record<string, string>;
  /** stderr 是否直通到本进程 stderr（默认 true，服务器异常栈不进协议通道）。 */
  forwardStderr?: boolean;
}

const DEFAULT_COMMAND = ['python3', '-m', 'nova_harness.modes.rpc.cli'];

export class WireClient {
  private child: ChildProcess | undefined;
  private nextId = 0;
  private pending = new Map<
    number | string,
    { resolve: (v: unknown) => void; reject: (e: Error) => void }
  >();
  private eventSink: ((event: RuntimeEvent) => void) | undefined;
  private reverseSink: ((frame: ReverseFrame) => void) | undefined;
  private closeSink: (() => void) | undefined;

  constructor(private readonly options: WireClientOptions = {}) {}

  /** 启动后端子进程（任意语言实现，契约一致即可）。 */
  async start(readyDelayMs = 300): Promise<void> {
    const command = this.options.command ?? DEFAULT_COMMAND;
    return new Promise((resolve, reject) => {
      this.child = spawn(command[0]!, command.slice(1), {
        stdio: ['pipe', 'pipe', 'pipe'],
        env: { ...process.env, ...this.options.env, PYTHONUNBUFFERED: '1' },
        cwd: this.options.cwd,
        detached: true,
      });

      this.child.on('error', reject);
      this.child.on('exit', () => {
        this.handleExit();
      });

      if (!this.child.stdout || !this.child.stdin) {
        reject(new Error('后端子进程 stdio 打开失败'));
        return;
      }

      const rl = createInterface({ input: this.child.stdout });
      rl.on('line', (line) => this.handleLine(line));

      if (this.options.forwardStderr !== false) {
        this.child.stderr?.on('data', (chunk: Buffer) => {
          process.stderr.write(chunk);
        });
      }

      setTimeout(resolve, readyDelayMs);
    });
  }

  /** 优雅关停：shutdown 命令 → stdin EOF → 等退出 → 兜底 SIGKILL。 */
  async stop(timeoutMs = 500): Promise<void> {
    if (!this.child) return;
    const child = this.child;

    try {
      child.stdin?.write(
        JSON.stringify({ jsonrpc: '2.0', method: 'shutdown', id: 'shutdown' }) + '\n',
      );
      child.stdin?.end();
    } catch {
      // 关停路上的一切写入失败都忽略——进程可能已死
    }

    await new Promise<void>((resolve) => {
      const onExit = () => resolve();
      child.once('exit', onExit);
      child.once('error', onExit);
      const timer = setTimeout(() => {
        child.removeListener('exit', onExit);
        child.removeListener('error', onExit);
        resolve();
      }, timeoutMs);
      if (child.exitCode != null || child.signalCode != null) {
        clearTimeout(timer);
        child.removeListener('exit', onExit);
        child.removeListener('error', onExit);
        resolve();
      }
    });

    if (!child.killed && child.exitCode == null && child.signalCode == null) {
      try {
        child.kill('SIGKILL');
      } catch {
        // ignore
      }
    }

    this.child = undefined;
  }

  /** 事件槽：``agent/event`` 通知的唯一出口（上 bus 的入口）。 */
  onEvent(sink: (event: RuntimeEvent) => void): void {
    this.eventSink = sink;
  }

  /** 反向槽：后端主动发起的请求/通知帧的唯一出口（bridge 的入口）。 */
  onReverse(sink: (frame: ReverseFrame) => void): void {
    this.reverseSink = sink;
  }

  onClose(sink: () => void): void {
    this.closeSink = sink;
  }

  /** 写一帧（fire-and-forget，供 bridge 应答反向请求/上报能力）。 */
  send(frame: Record<string, unknown>): void {
    if (!this.child?.stdin) {
      throw new Error('后端连接未建立');
    }
    this.child.stdin.write(JSON.stringify(frame) + '\n');
  }

  /**
   * JSON-RPC 请求-响应调用（类型化：params/result 形状来自契约生成表，
   * 方法名拼写与参数形状在编译期校验）。
   */
  async call<M extends NovaWireMethod>(
    method: M,
    params?: WireParams<M>,
  ): Promise<WireResult<M>> {
    if (!this.child?.stdin) {
      throw new Error('后端连接未建立');
    }
    const id = ++this.nextId;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      try {
        this.child!.stdin!.write(
          JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n',
        );
      } catch (e) {
        this.pending.delete(id);
        reject(e as Error);
      }
    }) as Promise<WireResult<M>>;
  }

  /**
   * 可取消调用（LSP $/cancelRequest 对位）：返回 promise 与 cancel 句柄。
   *
   * cancel() 做两件事：本地立即以 AbortError 收尾（Esc 体验零延迟，不等
   * 后端往返）；并发 cancelRequest 帧请后端取消执行（幂等——调用已完成
   * 则 cancelled:false）。后到的应答因 pending 已删被静默丢弃（天然幂等）。
   * 用途：slash 命令等长调用的取消入口（OAuth 登录）；run 的取消请走
   * abort* 域方法（领域清理更完整）。
   */
  callCancellable<M extends NovaWireMethod>(
    method: M,
    params?: WireParams<M>,
  ): { promise: Promise<WireResult<M>>; cancel: () => void } {
    if (!this.child?.stdin) {
      throw new Error('后端连接未建立');
    }
    const id = ++this.nextId;
    const promise = new Promise<WireResult<M>>((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      try {
        this.child!.stdin!.write(
          JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n',
        );
      } catch (e) {
        this.pending.delete(id);
        reject(e as Error);
      }
    });
    const cancel = () => {
      const entry = this.pending.get(id);
      if (!entry) return; // 已 settle 或已取消：幂等空转
      this.pending.delete(id);
      const error = new Error('调用已取消');
      error.name = 'AbortError';
      entry.reject(error);
      try {
        // 字符串 id 避免与自增数字空间碰撞；应答无 pending 条目，静默丢弃
        this.send({
          jsonrpc: '2.0',
          id: `cancel-${id}`,
          method: 'cancelRequest',
          params: { id },
        });
      } catch {
        // 后端已死等写入失败：本地收尾已完成，静默
      }
    };
    return { promise, cancel };
  }

  /**
   * 子进程退出：拒绝全部在飞的调用（后端死亡后 Promise 不再悬挂），
   * 然后通知 closeSink（经 ``onClose`` 订阅）。
   */
  private handleExit(): void {
    const error = new Error('后端进程已退出');
    for (const { reject } of this.pending.values()) {
      reject(error);
    }
    this.pending.clear();
    this.closeSink?.();
  }

  private handleLine(line: string): void {
    let msg: RpcResponse | RpcInbound;
    try {
      msg = JSON.parse(line) as RpcResponse | RpcInbound;
    } catch {
      return;
    }

    // 响应帧：按 id 配对（含反向请求的应答——后端回 ui/response 的收据）
    if ('id' in msg && msg.id !== undefined && !('method' in msg)) {
      const pending = this.pending.get(msg.id);
      if (!pending) return;
      this.pending.delete(msg.id);
      if ('error' in msg && msg.error) {
        pending.reject(new Error(`[${String(msg.error.code)}] ${msg.error.message}`));
      } else {
        pending.resolve(msg.result);
      }
      return;
    }

    if (!('method' in msg) || typeof msg.method !== 'string') return;

    if (msg.method === 'agent/event') {
      this.eventSink?.(msg.params as RuntimeEvent);
      return;
    }

    // 其余服务器主动帧：反向通道（ui/request、ui/notify、tool/invoke…）
    const params = (msg.params ?? {}) as Record<string, unknown>;
    this.reverseSink?.({
      id: typeof params.id === 'string' ? params.id : undefined,
      method: msg.method,
      params,
    });
  }
}
