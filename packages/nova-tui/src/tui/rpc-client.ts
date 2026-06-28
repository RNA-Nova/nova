/**
 * JSON-RPC client over stdio for communicating with the Python nova_harness server.
 */

import { spawn, type ChildProcess } from 'node:child_process';
import { createInterface } from 'node:readline';

export interface AgentEvent {
  type: string;
  [key: string]: unknown;
}

export interface RpcRequest {
  jsonrpc: '2.0';
  id: number | string;
  method: string;
  params?: Record<string, unknown>;
}

export interface RpcResponse {
  jsonrpc: '2.0';
  id: number | string;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
}

export interface RpcNotification {
  jsonrpc: '2.0';
  method: string;
  params?: unknown;
}

export class NovaRpcClient {
  private child: ChildProcess | undefined;
  private id = 0;
  private pending = new Map<number | string, { resolve: (v: unknown) => void; reject: (e: Error) => void }>();
  private onEventHandler: ((event: AgentEvent) => void) | undefined;
  private onCloseHandler: (() => void) | undefined;

  constructor(
    private readonly pythonPath: string,
    private readonly serverModule: string,
  ) {}

  async start(readyDelayMs = 300): Promise<void> {
    return new Promise((resolve, reject) => {
      this.child = spawn(this.pythonPath, ['-m', this.serverModule], {
        stdio: ['pipe', 'pipe', 'pipe'],
        env: { ...process.env, PYTHONUNBUFFERED: '1' },
        detached: true,
      });

      this.child.on('error', reject);
      this.child.on('exit', (code) => {
        this.onCloseHandler?.();
        if (code !== 0 && code !== null) {
          // eslint-disable-next-line no-console
          console.error(`Python server exited with code ${String(code)}`);
        }
      });

      if (!this.child.stdout || !this.child.stdin) {
        reject(new Error('Failed to spawn Python child process'));
        return;
      }

      const rl = createInterface({ input: this.child.stdout });
      rl.on('line', (line) => this._handleLine(line));

      // 将 stderr 转发到 Node stderr，便于调试
      this.child.stderr?.on('data', (chunk: Buffer) => {
        process.stderr.write(chunk);
      });

      // 等待一小段时间让服务器就绪
      setTimeout(resolve, readyDelayMs);
    });
  }

  async stop(timeoutMs = 500): Promise<void> {
    if (!this.child) return;
    const child = this.child;

    // 1. 尝试优雅 shutdown：发送 shutdown 请求后关闭 stdin，
    //    让 Python 端的 readline() 返回 None 从而退出主循环
    try {
      child.stdin?.write(JSON.stringify({ jsonrpc: '2.0', method: 'shutdown', id: 'shutdown' }) + '\n');
      child.stdin?.end();
    } catch {
      // ignore
    }

    // 2. 等待子进程退出或超时
    await new Promise<void>((resolve) => {
      const onExit = () => {
        resolve();
      };
      child.once('exit', onExit);
      child.once('error', onExit);

      const timer = setTimeout(() => {
        child.removeListener('exit', onExit);
        child.removeListener('error', onExit);
        resolve();
      }, timeoutMs);

      // 如果子进程已经退出，立即 resolve
      if (child.exitCode != null || child.signalCode != null) {
        clearTimeout(timer);
        child.removeListener('exit', onExit);
        child.removeListener('error', onExit);
        resolve();
      }
    });

    // 3. 如果还在运行，强制 SIGKILL
    if (!child.killed && child.exitCode == null && child.signalCode == null) {
      try {
        child.kill('SIGKILL');
      } catch {
        // ignore
      }
    }

    // 4. 等一小会儿让 SIGKILL 真正生效（子进程变成僵尸并被收割）
    await new Promise<void>((resolve) => {
      if (child.exitCode != null || child.signalCode != null) {
        resolve();
        return;
      }
      child.once('exit', () => resolve());
      setTimeout(() => resolve(), 200);
    });

    this.child = undefined;
  }

  onEvent(handler: (event: AgentEvent) => void): void {
    this.onEventHandler = handler;
  }

  onClose(handler: () => void): void {
    this.onCloseHandler = handler;
  }

  async call(method: string, params?: Record<string, unknown>): Promise<unknown> {
    if (!this.child?.stdin) {
      throw new Error('RPC client not connected');
    }
    const id = ++this.id;
    const req: RpcRequest = { jsonrpc: '2.0', id, method, params };
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      try {
        this.child!.stdin!.write(JSON.stringify(req) + '\n');
      } catch (e) {
        this.pending.delete(id);
        reject(e as Error);
      }
    });
  }

  private _handleLine(line: string): void {
    let msg: RpcResponse | RpcNotification;
    try {
      msg = JSON.parse(line) as RpcResponse | RpcNotification;
    } catch {
      return;
    }

    if ('id' in msg && msg.id !== undefined) {
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

    if ('method' in msg && msg.method === 'agent/event') {
      this.onEventHandler?.(msg.params as AgentEvent);
    }
  }
}
