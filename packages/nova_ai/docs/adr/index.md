<title>架构决策记录</title>

<p>记录 <code>nova_ai</code> 包的关键设计决策及其理由，防止以后重复讨论同样的问题。</p>

<h2>目录</h2>

<table>
  <thead>
    <tr>
      <th>ADR</th>
      <th>主题</th>
      <th>解决的问题</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><cite type="doc" doc-id="QUXodaEqDo7Al7x8XqYcRrp7n4e"></cite></td>
      <td>为什么 <code>ApiAdapter</code> 用 Protocol</td>
      <td>避免以后提议"改成 ABC"</td>
    </tr>
    <tr>
      <td><cite type="doc" doc-id="POVcdZN9AoSH1NxyXUlcgndXn1g"></cite></td>
      <td>为什么单例和类定义放同一个文件</td>
      <td>避免提议拆分 registry</td>
    </tr>
    <tr>
      <td><cite type="doc" doc-id="C5I0dTQtPoIRufx03yucVpCvnfc"></cite></td>
      <td>为什么从 <code>asyncio.Queue</code> 改回直接握手</td>
      <td>避免提议"为什么不用 Queue"</td>
    </tr>
    <tr>
      <td><cite type="doc" doc-id="ItdkdluBlo0PS8xoCdZc3REsnHb"></cite></td>
      <td>包内导入规则</td>
      <td>避免导入风格 drift</td>
    </tr>
    <tr>
      <td><cite type="doc" doc-id="EdBKdisrlopeXPxgLjYc2p2AnDc"></cite></td>
      <td>为什么 <code>api.py</code> 改名 <code>invoke.py</code></td>
      <td>避免命名冲突</td>
    </tr>
  </tbody>
</table>

<h2>格式</h2>

<p>每条 ADR 包含：状态、背景、决策、理由、后果、相关讨论。</p>
