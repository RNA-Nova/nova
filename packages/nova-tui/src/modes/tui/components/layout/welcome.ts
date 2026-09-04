/**
 * WelcomeView：启动欢迎区。
 *
 * 内容：logo + 版本 / 模型 + cwd / 键位提示（compact ↔ expanded 两态跟随
 * ctrl+o 全局展开态）/ onboarding 文案。quiet_startup 时整体不挂载
 * （装配根决定）。键位提示经键位表动态生成（hints.ts）——改键即变。
 */

import { Container, Spacer, Text } from '@earendil-works/pi-tui';

import { colors } from '../../themes/index.js';
import { BLOB_WIDTH, blobMascotLines } from './blob-mascot.js';
import { keyHint, keyText, rawKeyHint } from '../pickers/hints.js';
import type { ExpansionState } from '../transcript/expansion.js';

export interface WelcomeOptions {
  version: string;
  cwd: string;
  /** 当前模型文案（装配根给 getter——模型切换后 refresh 取新值）。 */
  model: () => string | undefined;
  expansion: ExpansionState;
}

export class WelcomeView extends Container {
  constructor(private readonly options: WelcomeOptions) {
    super();
    this.refresh();
  }

  /** 重建内容（主题切换 / 展开态切换 / 模型变化后调用）。 */
  refresh(): void {
    this.clear();
    const { version, cwd, model, expansion } = this.options;

    // nova 吉祥物（粉团小人——原图逐像素复刻，blob-mascot.ts）；
    // Claude 欢迎区"吉祥物在左、信息在右"的对位
    const mascot = blobMascotLines();

    const logo = colors.accent('nova') + (version ? colors.dim(` v${version}`) : '');
    const modelText = model() ?? '未配置模型';
    const info = colors.muted(modelText) + colors.dim(' · ') + colors.muted(cwd);

    const infoLines = [logo, info];

    if (expansion.expanded) {
      infoLines.push(
        keyHint('app.interrupt', '中断运行'),
        rawKeyHint(`${keyText('app.clear')} 双击`, '退出'),
        keyHint('app.exit', '空输入退出'),
        keyHint('app.tools.expand', '展开/折叠长输出'),
        keyHint('app.clipboard.paste', '粘贴（图片 → 临时文件）'),
        rawKeyHint('/', '命令'),
        rawKeyHint('!', 'bash'),
        rawKeyHint('/theme', '切换主题'),
      );
    } else {
      infoLines.push(
        [
          keyHint('app.interrupt', '中断'),
          rawKeyHint('/', '命令'),
          rawKeyHint('!', 'bash'),
          keyHint('app.tools.expand', '更多'),
        ].join(colors.muted(' · ')),
      );
    }

    // 左侧吉祥物（定宽 5）+ 右侧信息行——逐行并置
    const MASCOT_WIDTH = BLOB_WIDTH;
    const rowCount = Math.max(mascot.length, infoLines.length);
    for (let i = 0; i < rowCount; i++) {
      const left = mascot[i] ?? ' '.repeat(MASCOT_WIDTH);
      const right = infoLines[i] ?? '';
      this.addChild(new Text(` ${left}  ${right}`, 1, 0));
    }

    this.addChild(new Spacer(1));
  }
}
