/**
 * 助手消息视图（复刻 pi components/assistant-message.ts）。
 *
 * contentContainer 整体重建模式：Spacer 起 → thinking（斜体暗色独立区块）
 * → 正文 Markdown；thinking 与正文间补 Spacer（pi 同款节奏）。
 *
 * thinking 显隐（pi ctrl+t 对位）：``hideThinking`` 开启时 thinking 全文
 * 折叠为静态 "Thinking..." 标签（斜体暗色占位——知道模型想过，不刷屏）。
 *
 * 首末行注入 OSC 133 区段标记（终端 prompt 跳转——pi 同款；pi 对含
 * toolCall 的消息跳过，本模型工具调用是独立条目，助手消息恒无内嵌工具）。
 */

import { Container, Markdown, Spacer, Text } from '@earendil-works/pi-tui';

import { colors, markdownTheme } from '../../themes/index.js';

const OSC133_ZONE_START = '\x1b]133;A\x07';
const OSC133_ZONE_END = '\x1b]133;B\x07';
const OSC133_ZONE_FINAL = '\x1b]133;C\x07';

export class AssistantView extends Container {
  private readonly contentContainer = new Container();
  private text: string;
  private thinking?: string;
  private stopReason?: string;
  private errorMessage?: string;

  constructor(
    text: string,
    thinking?: string,
    stopReason?: string,
    errorMessage?: string,
    /** thinking 显隐判定（装配根注入——settings.hide_thinking_block 现取）。 */
    private readonly hideThinking: () => boolean = () => false,
  ) {
    super();
    this.text = text;
    this.thinking = thinking;
    this.stopReason = stopReason;
    this.errorMessage = errorMessage;
    this.addChild(this.contentContainer);
    this.rebuild();
  }

  update(text: string, thinking?: string, stopReason?: string, errorMessage?: string): void {
    // 微守卫：引用/值全等则跳过重建（流式 token 每帧触发 update——
    // 内容未变时 Markdown 组件缓存保住，整棵子树不重排）。
    if (
      text === this.text &&
      thinking === this.thinking &&
      stopReason === this.stopReason &&
      errorMessage === this.errorMessage
    ) {
      return;
    }
    this.text = text;
    this.thinking = thinking;
    this.stopReason = stopReason;
    this.errorMessage = errorMessage;
    this.rebuild();
  }

  override render(width: number): string[] {
    const lines = super.render(width);
    if (lines.length === 0) return lines;
    lines[0] = OSC133_ZONE_START + lines[0];
    lines[lines.length - 1] = OSC133_ZONE_END + OSC133_ZONE_FINAL + lines[lines.length - 1];
    return lines;
  }

  private rebuild(): void {
    this.contentContainer.clear();
    const text = this.text.trim();
    const thinking = (this.thinking ?? '').trim();
    if (!text && !thinking && !this.stopReason) return;

    this.contentContainer.addChild(new Spacer(1));

    if (thinking) {
      if (this.hideThinking()) {
        // ctrl+t 隐藏态：静态标签占位（pi 同款——斜体暗色 "Thinking..."）
        this.contentContainer.addChild(new Text(colors.thinkingText('Thinking...'), 1, 0));
      } else {
        this.contentContainer.addChild(
          new Markdown(thinking, 1, 0, markdownTheme, {
            color: colors.thinkingText,
            italic: true,
          }),
        );
      }
      if (text) this.contentContainer.addChild(new Spacer(1));
    }

    if (text) {
      this.contentContainer.addChild(new Markdown(text, 1, 0, markdownTheme));
    }

    // stopReason 错误行（复刻 pi：length/aborted/error 红字）
    if (this.stopReason === 'length') {
      this.contentContainer.addChild(new Spacer(1));
      this.contentContainer.addChild(
        new Markdown(
          colors.error(
            'Error: Model stopped because it reached the maximum output token limit. The response may be incomplete.',
          ),
          1,
          0,
          markdownTheme,
        ),
      );
    } else if (this.stopReason === 'aborted' || this.stopReason === 'error') {
      const message =
        this.errorMessage && this.errorMessage !== 'Request was aborted'
          ? this.errorMessage
          : this.stopReason === 'aborted'
            ? 'Operation aborted'
            : `Error: ${this.errorMessage ?? 'Unknown error'}`;
      this.contentContainer.addChild(new Spacer(1));
      this.contentContainer.addChild(
        new Markdown(colors.error(message), 1, 0, markdownTheme),
      );
    }
  }
}
