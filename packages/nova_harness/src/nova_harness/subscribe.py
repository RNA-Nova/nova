from pi_agent import AgentEvent
def on_print(event: AgentEvent):
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