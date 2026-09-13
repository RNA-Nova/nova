/**
 * 一次性 HTTP 监听器（`host:listenOnce` 宿主原语的 TUI 实现）。
 *
 * 用途：OAuth 回调在用户本机收货（远程形态下后端在别的机器上，收货点必须
 * 在用户机器）。监听固定端口+路径，收到一个合法请求即回传并关闭；
 * 超时/出错/被取消都自动关端口，不留监听残留。
 *
 * 本件是哑信使：原样转发 query 参数，不做任何 OAuth 校验（state 校验、
 * PKCE 交换全归后端）。
 */

import { createServer, type Server } from 'node:http';

export interface ListenOnceParams {
  port: number;
  path: string;
  timeoutMs?: number;
  successHtml?: string;
  errorHtml?: string;
}

export type ListenOnceResult =
  | { status: 'received'; code?: string; state?: string }
  | { status: 'timeout' }
  | { status: 'error'; message: string };

export interface ListenOnceHandle {
  /** 收货结果（received/timeout/error 三态）。 */
  result: Promise<ListenOnceResult>;
  /** 撤销监听（取消路径）——关端口，result 以 error/cancelled 落定。 */
  close: () => void;
}

export function listenOnce(params: ListenOnceParams): ListenOnceHandle {
  let server: Server | undefined;
  let timer: NodeJS.Timeout | undefined;
  let settled = false;
  let settleFn!: (r: ListenOnceResult) => void;

  const result = new Promise<ListenOnceResult>((resolve) => {
    settleFn = resolve;
  });

  const settle = (r: ListenOnceResult): void => {
    if (settled) return;
    settled = true;
    if (timer) clearTimeout(timer);
    if (server) {
      server.close();
      server = undefined;
    }
    settleFn(r);
  };

  server = createServer((req, res) => {
    const url = new URL(req.url ?? '/', 'http://127.0.0.1');
    if (url.pathname !== params.path) {
      res.writeHead(404, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(params.errorHtml || 'Not found');
      return;
    }
    const code = url.searchParams.get('code') ?? undefined;
    const state = url.searchParams.get('state') ?? undefined;
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end(params.successHtml || 'OK');
    settle({ status: 'received', code, state });
  });

  server.on('error', (err) => {
    // 端口被占等——error 落定，后端降级到粘贴框兜底
    settle({ status: 'error', message: String(err) });
  });

  server.listen(params.port, '127.0.0.1');

  const timeoutMs = params.timeoutMs && params.timeoutMs > 0 ? params.timeoutMs : 300_000;
  timer = setTimeout(() => settle({ status: 'timeout' }), timeoutMs);
  timer.unref?.();

  return {
    result,
    close: () => settle({ status: 'error', message: 'cancelled' }),
  };
}
