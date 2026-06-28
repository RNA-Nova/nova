/**
 * 状态/通知消息组件。
 */

import { Container, Text } from '@earendil-works/pi-tui';
import chalk from 'chalk';

import { getColors } from '../theme/colors.js';

export class StatusMessageComponent extends Container {
  constructor(message: string, color?: string, detail?: string) {
    super();
    const colors = getColors();
    const content = detail ? `${message} — ${detail}` : message;
    const hex = color || colors.textDim;
    this.addChild(new Text(`  ${chalk.hex(hex)(content)}`, 0, 0));
  }
}
