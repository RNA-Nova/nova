// jsdiff 9.0.0（MIT）的裁剪类型声明——只声明我们用到的面。
// 契约锚点：edit-preview 只经 createTwoFilesPatch 生成 unified diff。
export interface CreateTwoFilesPatchOptions {
  context?: number;
  ignoreWhitespace?: boolean;
  stripTrailingCr?: boolean;
}
export function createTwoFilesPatch(
  oldFileName: string,
  newFileName: string,
  oldStr: string,
  newStr: string,
  oldHeader?: string,
  newHeader?: string,
  options?: CreateTwoFilesPatchOptions,
): string;
