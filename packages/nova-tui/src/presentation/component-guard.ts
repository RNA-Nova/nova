/**
 * 包侧组件的行宽防线。
 *
 * pi-tui 的 TUI 渲染器对任何超过终端宽度的行直接抛异常——一行超宽即
 * 进程崩溃（`Rendered line N exceeds terminal width`）。包侧渲染器
 * （`tools/<tool>.ts`、`dialog:*` 工厂等）是第三方代码：一行超宽不得
 * 带走整个 TUI。在每个包组件的挂载点统一包一层——render 后逐行检查，
 * 超宽行截断（`visibleWidth`/`truncateToWidth` 感知 ANSI 与 CJK 宽字符）。
 *
 * 实现为实例级 render 覆盖（保留组件的 handleInput/invalidate 等全部
 * 其余表面），幂等：重复包裹只叠一层无害截断。
 */

import { truncateToWidth, visibleWidth, type Component } from '@earendil-works/pi-tui';

export function guardComponentLineWidth<T extends Component>(component: T): T {
  const original = component.render.bind(component);
  component.render = (width: number): string[] =>
    original(width).map((line) =>
      visibleWidth(line) > width ? truncateToWidth(line, width) : line,
    );
  return component;
}
