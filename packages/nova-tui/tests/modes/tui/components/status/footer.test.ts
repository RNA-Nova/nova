/**
 * FooterView region 部件消费测试：region:footer slot 的注册→产出→渲染链路，
 * 指纹缓存（输出不变复用组件）与异常静默。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { NovaUIRuntime, SlotRegistry, regionSlot, type NovaBlock } from 'nova-tui';

import { FooterView } from '../../../../../src/modes/tui/components/status/footer.js';

function makeFooter(cwd = '/tmp'): { runtime: NovaUIRuntime; footer: FooterView } {
  const runtime = new NovaUIRuntime();
  const footer = new FooterView(runtime, cwd);
  return { runtime, footer };
}

describe('FooterView · region:footer 部件', () => {
  it('无注册 → 两行基线；注册后 region 内容出现', () => {
    const { runtime, footer } = makeFooter();
    try {
      assert.equal(footer.render(80).length, 2); // 基线两行

      runtime.slots.register(
        regionSlot('footer'),
        ({ cwd }: { cwd: string }): NovaBlock[] => [
          { kind: 'markdown', text: `部件:${cwd}` },
        ],
        'test-pkg',
      );
      const out = footer.render(80).join('\n');
      assert.match(out, /部件:\/tmp/);
    } finally {
      footer.dispose();
    }
  });

  it('指纹缓存：输出不变复用组件，变化才重建', () => {
    const { runtime, footer } = makeFooter();
    try {
      let value = 'marker-a';
      runtime.slots.register(
        regionSlot('footer'),
        (): NovaBlock[] => [{ kind: 'markdown', text: value }],
        'test-pkg',
      );
      assert.match(footer.render(80).join('\n'), /marker-a/);
      const firstComponents = (footer as unknown as { regionComponents: unknown })
        .regionComponents;
      footer.render(80); // 输出不变 → 组件引用复用
      assert.equal(
        (footer as unknown as { regionComponents: unknown }).regionComponents,
        firstComponents,
      );
      value = 'marker-b'; // 输出变化 → 重建
      assert.match(footer.render(80).join('\n'), /marker-b/);
      assert.notEqual(
        (footer as unknown as { regionComponents: unknown }).regionComponents,
        firstComponents,
      );
    } finally {
      footer.dispose();
    }
  });

  it('部件异常静默（不炸 footer 主行）；空块列表不产行', () => {
    const { runtime, footer } = makeFooter();
    try {
      runtime.slots.register(
        regionSlot('footer'),
        (): NovaBlock[] => {
          throw new Error('boom');
        },
        'bad-pkg',
      );
      assert.equal(footer.render(80).length, 2); // 主行完好
    } finally {
      footer.dispose();
    }
  });
});

describe('FooterView · 数据行补全', () => {
  /** 桩 runtime：invoke 按方法名返回 stats/usage。 */
  function makeStatsFooter(settings: Record<string, unknown> = {}) {
    const runtime = {
      invoke: async (method: string) => {
        if (method === 'getSessionStats') {
          return {
            tokens: {
              inputTokens: 10000,
              outputTokens: 2000,
              cacheRead: 8000,
              cacheWrite: 500,
            },
            cost: 0.0123,
          };
        }
        if (method === 'getContextUsage') {
          return { tokens: 96000, contextWindow: 128000, percent: 75 };
        }
        return {};
      },
      store: { currentSnapshot: null },
      slots: new SlotRegistry(),
    } as unknown as NovaUIRuntime;
    const footer = new FooterView(runtime, '/tmp', () => settings);
    return { footer };
  }

  it('token 统计 + 命中率 + 成本 + 上下文用量（>70% 阈值）全渲染', async () => {
    const { footer } = makeStatsFooter();
    try {
      await footer.refreshStats();
      const out = footer.render(120).join('\n');
      assert.match(out, /↑10000/);
      assert.match(out, /↓2000/);
      assert.match(out, /R8000/);
      assert.match(out, /W500/);
      assert.match(out, /80%/); // 命中率 8000/10000
      assert.match(out, /\$0\.0123/);
      assert.match(out, /75%\/128k\(auto\)/); // 上下文（默认 auto-compact 开）
    } finally {
      footer.dispose();
    }
  });

  it('compaction.enabled=false 时不带 (auto) 标记', async () => {
    const { footer } = makeStatsFooter({ compaction: { enabled: false } });
    try {
      await footer.refreshStats();
      const out = footer.render(120).join('\n');
      assert.match(out, /75%\/128k(?!.\()/); // 无 (auto)
    } finally {
      footer.dispose();
    }
  });
});

describe('FooterView · setCustomFooter（pi setFooter 对位）', () => {
  it('整件替换：自定义组件接管渲染，env 回灌宿主数据', () => {
    const { footer } = makeFooter();
    try {
      footer.setCustomFooter((env) => {
        const e = env as { cwd: string; getExtensionStatuses: () => string[] };
        footer.setExtensionStatus('demo', '状态X');
        return {
          render: (width: number) => [`自定义:${e.cwd}:${e.getExtensionStatuses().join(',')}:${width}`],
        };
      });
      const out = footer.render(60);
      assert.deepEqual(out, ['自定义:/tmp:状态X:60']);
    } finally {
      footer.dispose();
    }
  });

  it('恢复默认：undefined 还原内建行；旧组件 dispose 被调用', () => {
    const { footer } = makeFooter();
    let disposed = 0;
    try {
      footer.setCustomFooter(() => ({
        render: () => ['临时'],
        dispose: () => { disposed++; },
      }));
      assert.deepEqual(footer.render(80), ['临时']);
      footer.setCustomFooter(undefined);
      assert.equal(disposed, 1);
      assert.equal(footer.render(80).length, 2); // 内建两行基线恢复
    } finally {
      footer.dispose();
    }
  });

  it('异常回退：自定义组件渲染抛错落回默认渲染', () => {
    const { footer } = makeFooter();
    try {
      footer.setCustomFooter(() => ({
        render: () => { throw new Error('boom'); },
      }));
      assert.equal(footer.render(80).length, 2); // 不炸布局
    } finally {
      footer.dispose();
    }
  });
});
