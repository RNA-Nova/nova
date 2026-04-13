import asyncio
import tempfile
import os
from pathlib import Path

from nova_simple_agent.core.resource import (
    DefaultResourceLoader,
    DefaultResourceLoaderOptions,
)


async def main():
    """资源加载器使用示例 - 只接受 Markdown 格式"""
    
    tmpdir = "/root/nova/packages/nova_simple_agent/src/.nova"
    
    # ==========================================
    # 1. 准备测试用的 Markdown 格式提示词模板
    # ==========================================
    custom_prompts_dir = Path(tmpdir) / "custom_prompts"
    custom_prompts_dir.mkdir(exist_ok=True)
    
    # 创建 Markdown 格式的提示词模板（使用 YAML Front Matter）
    (custom_prompts_dir / "greeting.md").write_text("""---
name: greeting
description: 问候语模板
version: "1.0"
template_type: markdown
---
你好，{{name}}！欢迎使用 {{service}}。
""", encoding='utf-8')
    
    (custom_prompts_dir / "common.md").write_text("""---
name: system_prompt
description: 系统提示词（自定义版本，会覆盖默认）
version: "2.0"
---
你是一个有帮助的助手（自定义覆盖版本）。
""", encoding='utf-8')
    
    another_dir = Path(tmpdir) / "extra_prompts"
    another_dir.mkdir(exist_ok=True)
    (another_dir / "duplicate.md").write_text("""---
name: greeting
description: 重复的名称，用于测试冲突检测
version: "1.0"
---
这是另一个 greeting 模板。
""", encoding='utf-8')
    
    # ==========================================
    # 2. 配置加载器选项（关键修改点）
    # ==========================================
    # 如果 DefaultResourceLoaderOptions 支持 file_extensions 或 pattern 参数：
    options = DefaultResourceLoaderOptions(
        cwd=tmpdir,
        agent_dir=tmpdir,
        additional_prompt_template_paths=[str(another_dir)],
        no_prompt_templates=True,
        # 尝试添加文件过滤配置（取决于具体实现）
        # file_extensions=[".md"],  # 如果有此参数
        # pattern="**/*.md",        # 或者此参数
    )
    
    # ==========================================
    # 3. 初始化资源加载器
    # ==========================================
    loader = DefaultResourceLoader(options)
    
    # ==========================================
    # 4. 加载资源（异步）
    # ==========================================
    print("🔄 正在加载 Markdown 格式的提示词模板...")
    await loader.reload()
    
    # ==========================================
    # 5. 获取并处理结果
    # ==========================================
    result = loader.get_prompts()
    prompts = result["prompts"]
    diagnostics = result["diagnostics"]
    
    # 筛选出 .md 文件（如果 loader 不支持原生过滤）
    md_prompts = [p for p in prompts if str(p.file_path).endswith('.md')]
    
    print(f"\n✅ 成功加载 {len(md_prompts)} 个 Markdown 提示词模板：")
    for prompt in md_prompts:
        print(f"   - {prompt.name}: {prompt.description} (来自: {prompt.file_path})")
    
    # 展示诊断信息
    if diagnostics:
        print(f"\n⚠️  发现 {len(diagnostics)} 个资源冲突：")
        for diag in diagnostics:
            print(f"   冲突类型: {diag['type']}")
            print(f"   消息: {diag['message']}")
    else:
        print("\n✨ 未发现资源冲突")
    
    # ==========================================
    # 6. 动态重新加载
    # ==========================================
    print("\n🔄 模拟动态重新加载...")
    
    # 添加新的 Markdown 提示词文件
    (custom_prompts_dir / "new_feature.md").write_text("""---
name: new_feature
description: 新功能提示词
version: "1.0"
---
这是一个动态添加的新功能说明。
""", encoding='utf-8')
    
    await loader.reload()
    
    new_result = loader.get_prompts()
    # 只统计 .md 文件
    new_md_prompts = [p for p in new_result["prompts"] if str(p.file_path).endswith('.md')]
    new_prompt_names = [p.name for p in new_md_prompts]
    print(f"   重新加载后共有 {len(new_md_prompts)} 个 Markdown 模板")
    print(f"   包含: {', '.join(new_prompt_names)}")


def example_with_disabled_defaults():
    """示例：只使用自定义路径的 Markdown 模板"""
    
    with tempfile.TemporaryDirectory() as tmpdir:
        custom_dir = Path(tmpdir) / "my_custom_prompts"
        custom_dir.mkdir()
        
        # 创建 Markdown 模板
        (custom_dir / "my_prompt.md").write_text("""---
name: my_prompt
description: 我的自定义提示词
version: "1.0"
---
这是 Markdown 格式的提示词内容。
""", encoding='utf-8')
        
        options = DefaultResourceLoaderOptions(
            cwd=tmpdir,
            agent_dir="/some/agent/path",  # 会被忽略
            additional_prompt_template_paths=[str(custom_dir)],
            no_prompt_templates=True,  # 禁用默认模板
        )
        
        loader = DefaultResourceLoader(options)


if __name__ == "__main__":
    asyncio.run(main())