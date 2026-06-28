# nova_agent 事件流日志

本文件由 `tests/generate_event_flow_log.py` 自动生成。

每个场景按实际发生顺序列出事件，格式为：`event_type | 附加字段`。


## 真实模型场景（依赖 VOLCENGINE_API_KEY）


### A. 纯文本回复（无工具）
**说明**：真实模型，未注册任何工具

  1. agent_start
  2. turn_start
  3. message_start | role=user | text='用一句话介绍你自己。'
  4. message_end | role=user | text='用一句话介绍你自己。'
  5. message_start | role=assistant | content=[]
  6. message_update | role=assistant | content=['text'] × 30
  7. message_end | role=assistant | content=['text']
  8. turn_end | role=assistant | content=['text']
  9. agent_end


### B. 单次工具调用成功
**说明**：真实模型 + EchoTool

  1. agent_start
  2. turn_start
  3. message_start | role=user | text='请调用 echo 工具，参数 {"message": "hello"}。只返回工具结果。'
  4. message_end | role=user | text='请调用 echo 工具，参数 {"message": "hello"}。只返回工具结果。'
  5. message_start | role=assistant | content=[]
  6. message_update | role=assistant | content=['toolCall'] × 7
  7. message_end | role=assistant | content=['toolCall']
  8. tool_execution_start | tool=echo | id=call_p7h
  9. tool_execution_update | tool=echo | id=call_p7h
 10. tool_execution_end | tool=echo | id=call_p7h | is_error=False
 11. message_start | role=toolResult | text='echo: hello'
 12. message_end | role=toolResult | text='echo: hello'
 13. turn_end | role=assistant | content=['toolCall']
 14. turn_start
 15. message_start | role=assistant | content=[]
 16. message_update | role=assistant | content=['text'] × 11
 17. message_end | role=assistant | content=['text']
 18. turn_end | role=assistant | content=['text']
 19. agent_end


### C. 工具执行异常
**说明**：真实模型 + ErrorTool

  1. agent_start
  2. turn_start
  3. message_start | role=user | text='请调用 error_tool 工具。'
  4. message_end | role=user | text='请调用 error_tool 工具。'
  5. message_start | role=assistant | content=[]
  6. message_update | role=assistant | content=['toolCall'] × 4
  7. message_end | role=assistant | content=['toolCall']
  8. tool_execution_start | tool=error_tool | id=call_nvw
  9. tool_execution_end | tool=error_tool | id=call_nvw | is_error=True
 10. message_start | role=toolResult | text='intentional tool error'
 11. message_end | role=toolResult | text='intentional tool error'
 12. turn_end | role=assistant | content=['toolCall']
 13. turn_start
 14. message_start | role=assistant | content=[]
 15. message_update | role=assistant | content=['text'] × 12
 16. message_end | role=assistant | content=['text']
 17. turn_end | role=assistant | content=['text']
 18. agent_end


### D. before_tool_call 阻断
**说明**：真实模型 + EchoTool + before block

  1. agent_start
  2. turn_start
  3. message_start | role=user | text='请调用 echo 工具，参数 {"message": "hello"}。'
  4. message_end | role=user | text='请调用 echo 工具，参数 {"message": "hello"}。'
  5. message_start | role=assistant | content=[]
  6. message_update | role=assistant | content=['toolCall'] × 7
  7. message_end | role=assistant | content=['toolCall']
  8. tool_execution_start | tool=echo | id=call_j4s
  9. tool_execution_end | tool=echo | id=call_j4s | is_error=True
 10. message_start | role=toolResult | text='blocked by test'
 11. message_end | role=toolResult | text='blocked by test'
 12. turn_end | role=assistant | content=['toolCall']
 13. turn_start
 14. message_start | role=assistant | content=[]
 15. message_update | role=assistant | content=['text'] × 41
 16. message_end | role=assistant | content=['text']
 17. turn_end | role=assistant | content=['text']
 18. agent_end


### E. after_tool_call 覆盖结果
**说明**：真实模型 + EchoTool + after override

  1. agent_start
  2. turn_start
  3. message_start | role=user | text='请调用 echo 工具，参数 {"message": "hello"}。'
  4. message_end | role=user | text='请调用 echo 工具，参数 {"message": "hello"}。'
  5. message_start | role=assistant | content=[]
  6. message_update | role=assistant | content=['toolCall'] × 7
  7. message_end | role=assistant | content=['toolCall']
  8. tool_execution_start | tool=echo | id=call_3xy
  9. tool_execution_update | tool=echo | id=call_3xy
 10. tool_execution_end | tool=echo | id=call_3xy | is_error=False
 11. message_start | role=toolResult | text='overridden'
 12. message_end | role=toolResult | text='overridden'
 13. turn_end | role=assistant | content=['toolCall']
 14. turn_start
 15. message_start | role=assistant | content=[]
 16. message_update | role=assistant | content=['text'] × 14
 17. message_end | role=assistant | content=['text']
 18. turn_end | role=assistant | content=['text']
 19. agent_end


### F. 工具执行过程中 abort
**说明**：真实模型 + SlowTool

  1. agent_start
  2. turn_start
  3. message_start | role=user | text='请调用 slow_tool 工具。'
  4. message_end | role=user | text='请调用 slow_tool 工具。'
  5. message_start | role=assistant | content=[]
  6. message_update | role=assistant | content=['text'] × 16
  7. message_end | role=assistant | content=['text', 'toolCall']
  8. tool_execution_start | tool=slow_tool | id=call_x3k
  9. tool_execution_end | tool=slow_tool | id=call_x3k | is_error=True
 10. message_start | role=toolResult | text='Operation aborted'
 11. message_end | role=toolResult | text='Operation aborted'
 12. turn_end | role=assistant | content=['text', 'toolCall']
 13. turn_start
 14. message_start | role=assistant | content=[]
 15. message_end | role=assistant | content=[]
 16. turn_end | role=assistant | content=[]
 17. agent_end


### G. 多个工具调用
**说明**：真实模型 + GetDateTool + GetTimeTool

  1. agent_start
  2. turn_start
  3. message_start | role=user | text='请同时调用 get_date 和 get_time 两个工具，并把结果汇总成一句话。'
  4. message_end | role=user | text='请同时调用 get_date 和 get_time 两个工具，并把结果汇总成一句话。'
  5. message_start | role=assistant | content=[]
  6. message_update | role=assistant | content=['toolCall'] × 8
  7. message_end | role=assistant | content=['toolCall', 'toolCall']
  8. tool_execution_start | tool=get_date | id=call_oil
  9. tool_execution_start | tool=get_time | id=call_8j4
 10. tool_execution_end | tool=get_date | id=call_oil | is_error=False
 11. tool_execution_end | tool=get_time | id=call_8j4 | is_error=False
 12. message_start | role=toolResult | text='2026-06-10'
 13. message_end | role=toolResult | text='2026-06-10'
 14. message_start | role=toolResult | text='12:00'
 15. message_end | role=toolResult | text='12:00'
 16. turn_end | role=assistant | content=['toolCall', 'toolCall']
 17. turn_start
 18. message_start | role=assistant | content=[]
 19. message_update | role=assistant | content=['text'] × 22
 20. message_end | role=assistant | content=['text']
 21. turn_end | role=assistant | content=['text']
 22. agent_end


### L. 运行中 steer 注入
**说明**：真实模型

  1. agent_start
  2. turn_start
  3. message_start | role=user | text='请用 50 字以内简要介绍机器学习。'
  4. message_end | role=user | text='请用 50 字以内简要介绍机器学习。'
  5. message_start | role=assistant | content=[]
  6. message_update | role=assistant | content=['text'] × 24
  7. message_end | role=assistant | content=['text']
  8. turn_end | role=assistant | content=['text']
  9. turn_start
 10. message_start | role=user | text='停止，直接回答：ok'
 11. message_end | role=user | text='停止，直接回答：ok'
 12. message_start | role=assistant | content=[]
 13. message_update | role=assistant | content=['text'] × 3
 14. message_end | role=assistant | content=['text']
 15. turn_end | role=assistant | content=['text']
 16. agent_end


### M. follow_up 队列
**说明**：真实模型

  1. agent_start
  2. turn_start
  3. message_start | role=user | text='你好'
  4. message_end | role=user | text='你好'
  5. message_start | role=assistant | content=[]
  6. message_update | role=assistant | content=['text'] × 30
  7. message_end | role=assistant | content=['text']
  8. turn_end | role=assistant | content=['text']
  9. turn_start
 10. message_start | role=user | text='再问候一次'
 11. message_end | role=user | text='再问候一次'
 12. message_start | role=assistant | content=[]
 13. message_update | role=assistant | content=['text'] × 26
 14. message_end | role=assistant | content=['text']
 15. turn_end | role=assistant | content=['text']
 16. agent_end


## Mock 场景（不依赖真实模型）


### H. 工具不存在（mock）
**说明**：stream_fn 第一次返回未知 tool call，之后返回文本回复

  1. agent_start
  2. turn_start
  3. message_start | role=user | text='trigger tool call'
  4. message_end | role=user | text='trigger tool call'
  5. message_start | role=assistant | content=['toolCall']
  6. message_update | role=assistant | content=['toolCall']
  7. message_end | role=assistant | content=['toolCall']
  8. tool_execution_start | tool=nonexistent_tool | id=tc-1
  9. tool_execution_end | tool=nonexistent_tool | id=tc-1 | is_error=True
 10. message_start | role=toolResult | text='Tool nonexistent_tool not found'
 11. message_end | role=toolResult | text='Tool nonexistent_tool not found'
 12. turn_end | role=assistant | content=['toolCall']
 13. turn_start
 14. message_start | role=assistant | content=['text']
 15. message_end | role=assistant | content=['text']
 16. turn_end | role=assistant | content=['text']
 17. agent_end


### I. 参数校验失败（mock）
**说明**：stream_fn 第一次返回非法 echo 调用，之后返回文本回复

  1. agent_start
  2. turn_start
  3. message_start | role=user | text='trigger tool call'
  4. message_end | role=user | text='trigger tool call'
  5. message_start | role=assistant | content=['toolCall']
  6. message_update | role=assistant | content=['toolCall']
  7. message_end | role=assistant | content=['toolCall']
  8. tool_execution_start | tool=echo | id=tc-1
  9. tool_execution_end | tool=echo | id=tc-1 | is_error=True
 10. message_start | role=toolResult | text='Validation failed for tool "echo":\n  - message: is required\n\nReceived arguments:\n{}'
 11. message_end | role=toolResult | text='Validation failed for tool "echo":\n  - message: is required\n\nReceived arguments:\n{}'
 12. turn_end | role=assistant | content=['toolCall']
 13. turn_start
 14. message_start | role=assistant | content=['text']
 15. message_end | role=assistant | content=['text']
 16. turn_end | role=assistant | content=['text']
 17. agent_end


### J. 准备阶段 abort（mock）
**说明**：before hook 第一次调用时设置 signal，之后返回文本回复

  1. agent_start
  2. turn_start
  3. message_start | role=user | text='trigger tool call'
  4. message_end | role=user | text='trigger tool call'
  5. message_start | role=assistant | content=['toolCall']
  6. message_update | role=assistant | content=['toolCall']
  7. message_end | role=assistant | content=['toolCall']
  8. tool_execution_start | tool=echo | id=tc-1
  9. tool_execution_end | tool=echo | id=tc-1 | is_error=True
 10. message_start | role=toolResult | text='Operation aborted'
 11. message_end | role=toolResult | text='Operation aborted'
 12. turn_end | role=assistant | content=['toolCall']
 13. turn_start
 14. message_start | role=assistant | content=['text']
 15. message_end | role=assistant | content=['text']
 16. turn_end | role=assistant | content=['text']
 17. agent_end


### K. continue_ 续跑（mock）
**说明**：上下文已包含 toolResult

  1. agent_start
  2. turn_start
  3. message_start | role=assistant | content=['text']
  4. message_end | role=assistant | content=['text']
  5. turn_end | role=assistant | content=['text']
  6. agent_end
