/**
 * 前台任务取消登记处（Esc 域级路由的一环）。
 *
 * 用途：navigateTree 分支摘要、/share gist 创建等**非 run 的在飞任务**——
 * 它们不反映在 store.status（不是 run/retry/compaction），Esc 域级路由
 * 需要一个独立登记点。同一时刻最多一个前台任务（UI 单焦点）；新任务
 * 注册即取消旧任务（防御——正常流程不会叠加）。
 *
 * 路由优先级（keymap Esc）：对话框 > 前台任务 > run/retry/compacting >
 * 双 Esc 导航。
 */
export class ForegroundTasks {
  private cancel: (() => void) | undefined;

  /** 登记在飞任务的取消闭包；返回注销函数（任务收尾时调用）。 */
  register(cancel: () => void): () => void {
    const previous = this.cancel;
    this.cancel = cancel;
    if (previous) previous(); // 顶掉旧任务（防御性——正常不叠加）
    return () => {
      if (this.cancel === cancel) this.cancel = undefined;
    };
  }

  /** Esc 消费：有在飞任务则取消之并返回 true。 */
  consume(): boolean {
    const cancel = this.cancel;
    this.cancel = undefined;
    if (!cancel) return false;
    cancel();
    return true;
  }

  /** 是否有在飞任务。 */
  get active(): boolean {
    return this.cancel !== undefined;
  }
}
