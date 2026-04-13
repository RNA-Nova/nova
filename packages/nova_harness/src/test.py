import asyncio
import sys
from typing import List, Optional
import os
from nova_simple_agent import RemoteCommandTool, RemoteSkillTool, RemoteWriteTool,RemoteReadTool
from pi_agent import Agent, AgentMessage, AgentTool, ThinkingLevel, AgentToolResult, AgentEvent, CustomAgentMessage, AbortSignal
from nova_ai import Message, get_model, ImageContent, UserMessage, TextContent

# 配置环境变量
os.environ["VOLCENGINE_API_KEY"] = "3b631f71-6bd6-464a-9abc-b0e8d19f25d7"
os.environ["GEMINI_API_KEY"] = "AIzaSyB32Oqk8CDsB4SssbzK76wpfFXvHwxaukg"
# os.environ["HTTP_PROXY"] = "http://10.0.1.158:8118"
# os.environ["HTTPS_PROXY"] = "http://10.0.1.158:8118"

class ConversationAgent:
    """多轮对话Agent管理器"""
    
    def __init__(self):
        self.agent = None
        self.model = None
        self.tools = []
        self.running = True
        
    def setup_agent(self):
        """初始化并配置Agent"""
        print("=" * 50)
        print("初始化AI助手...")
        print("=" * 50)
        
        # 创建Agent实例
        self.agent = Agent(
            steering_mode="one-at-a-time",
            follow_up_mode="all",
            max_retry_delay_ms=30
        )
        
        # 配置模型（可选择不同模型）
        print("\n可用模型:")
        print("1. deepseek-r1-250528 (火山引擎)")
        print("2. gemini-2.0-flash (Google)")
        
        model_choice = input("\n请选择模型 (1/2，默认1): ").strip()
        
        if model_choice == "2":
            self.model = get_model("google", "gemini-2.0-flash")
            print("已选择: Gemini 2.0 Flash")
        else:
            self.model = get_model("volcengine", "deepseek-r1-250528")
            print("已选择: Deepseek R1")
        
        self.agent.set_model(self.model)
        self.agent.set_system_prompt('你是全能的AI助手，你可以灵活调用你的工具。注意你是一个拥有技能的助手，你必须在每次面对问题时优先调用技能SkillTool去查询已有技能！')
        
        # 添加工具
        self.tools = [RemoteCommandTool(), RemoteSkillTool(), RemoteWriteTool(),RemoteReadTool()]
        self.agent.set_tools(self.tools)
        print(f"已加载 {len(self.tools)} 个工具: RemoteCommandTool, RemoteSkillTool, RemoteWriteTool")
        
        # 注册事件监听器
        self.agent.subscribe(self.on_event)
        
    def on_event(self, event: AgentEvent):
        """处理Agent事件"""
        """处理所有Agent事件"""
        event_type = event.type
        
        if event_type == "message_start":
            msg = event.message
            print(f"\n[消息开始] {msg.role}: ...")
        
        # elif event_type == "message_update":
        #     msg = event.message
        #     if msg.role == "assistant":
        #         # 打印流式更新的文本内容
        #         for content in msg.content:
        #             if content.type == "text" and content.text:
        #                 print(content.text, end="", flush=True)
        
        elif event_type == "message_end":
            msg = event.message
            for content in msg.content:
                if content.type == "text" and content.text:
                    print(f"[answer]:{content.text}")
                elif content.type == "thinking":
                    print(f"[thinking]:{content.thinking}")
            error =msg.error_message
            if error:
                print(f"[error]:{error}")
            print(f"\n[消息完成] {msg.role}")
        
        elif event_type == "tool_execution_start":
            print(f"\n[工具开始] {event.tool_name}({event.args})")
        
        elif event_type == "tool_execution_update":
            print(f"  [工具更新] {event.partialResult}")
        
        elif event_type == "tool_execution_end":
            status = "✓ 成功" if not event.is_error else "✗ 失败"
            print(f"[工具结束] {event.tool_name} {status}")
        
        elif event_type == "turn_start":
            print("\n--- 新回合开始 ---")
        
        elif event_type == "turn_end":
            print(f"--- 回合结束 (工具结果数: {len(event.toolResults)}) ---\n")
        
        elif event_type == "agent_start":
            print("\n=== Agent 启动 ===\n")
        
        elif event_type == "agent_end":
            print(f"\n=== Agent 结束 (共 {len(event.messages)} 条消息) ===\n")
    
    
    async def process_message(self, message: str) -> bool:
        """处理单条消息，返回是否继续对话"""
        if not message or message.strip() == "":
            return True
            
        # 检查退出命令
        if message.lower() in ['exit', 'quit', '退出', 'q']:
            print("\n再见！")
            return False
            
        # 检查重置命令
        if message.lower() in ['reset', '重置', 'clear']:
            self.agent.reset()
            print("\n✓ 对话已重置，历史记录已清空")
            return True
            
        # 检查帮助命令
        if message.lower() in ['help', '帮助', '?']:
            self.show_help()
            return True
            
        # 检查模型切换命令
        if message.lower() in ['switch model', '切换模型', 'model']:
            await self.switch_model()
            return True
            
        # 发送消息给Agent
        try:
            await self.agent.prompt(message)
            await self.agent.wait_for_idle()
        except Exception as e:
            print(f"\n[错误]: 处理消息时出错 - {e}")
            
        return True
    
    def show_help(self):
        """显示帮助信息"""
        print("\n" + "=" * 50)
        print("帮助信息")
        print("=" * 50)
        print("可用命令:")
        print("  exit/quit/退出/q  - 退出程序")
        print("  reset/重置/clear  - 重置对话历史")
        print("  switch model/切换模型/model - 切换AI模型")
        print("  help/帮助/?       - 显示此帮助信息")
        print("\n其他输入将作为问题发送给AI助手")
        print("=" * 50)
    
    async def switch_model(self):
        """切换AI模型"""
        print("\n选择模型:")
        print("1. deepseek-r1-250528 (火山引擎)")
        print("2. gemini-2.0-flash (Google)")
        
        choice = input("请选择模型 (1/2): ").strip()
        
        if choice == "2":
            new_model = get_model("google", "gemini-2.0-flash")
            model_name = "Gemini 2.0 Flash"
        else:
            new_model = get_model("volcengine", "deepseek-r1-250528")
            model_name = "Deepseek R1"
        
        self.agent.set_model(new_model)
        self.model = new_model
        print(f"✓ 已切换到模型: {model_name}")
    
    async def run(self):
        """运行主对话循环"""
        # 初始化Agent
        self.setup_agent()
        
        # 显示欢迎信息和帮助
        print("\n" + "=" * 50)
        print("AI助手已启动！")
        print("输入 'help' 查看可用命令")
        print("输入 'exit' 退出程序")
        print("=" * 50 + "\n")
        
        # 主对话循环
        while self.running:
            try:
                # 获取用户输入
                user_input = input("\n[你]: ").strip()
                
                # 处理消息
                should_continue = await self.process_message(user_input)
                
                if not should_continue:
                    break
                    
            except KeyboardInterrupt:
                print("\n\n检测到中断信号，正在退出...")
                break
            except EOFError:
                print("\n\n输入流结束，正在退出...")
                break
            except Exception as e:
                print(f"\n[错误]: {e}")
                print("对话将继续，请输入下一个问题...")
        
        print("\n程序已退出")

async def main():
    """主函数"""
    conversation = ConversationAgent()
    await conversation.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n用户中断执行")
        sys.exit(0)
    except Exception as e:
        print(f"\n错误: {e}")
        sys.exit(1)