# 🧠 Agent Memory 总索引

> Agent 的长期记忆库。执行新任务**之前**先读本页与相关记忆；任务**之后**把情景/经验/发现蒸馏回来。
> 结构：`tasks/`（情景记忆）· `learnings/`（工程经验）· `playbooks/`（可复用流程）· `discoveries/`（新发现）· `hypotheses/`（假设待验）· `literature/`（文献知识）· `entities/`（实体卡片）· `_templates/`（模板）

## 📋 任务（情景记忆）

```dataview
TABLE skill AS "Skill", date AS "日期", status AS "状态", expected_count AS "要求数量"
FROM #experiment
WHERE type = "episodic"
SORT date DESC
```

## 💡 经验（语义记忆）

```dataview
TABLE id AS "ID", severity AS "严重度", date AS "日期"
FROM #learning
WHERE type = "semantic"
SORT severity ASC, date DESC
```

## 🔬 发现（discoveries）

```dataview
TABLE id AS "ID", confidence AS "置信度", validation AS "验证状态", date AS "日期"
FROM #discovery
WHERE type = "discovery"
SORT date DESC
```

## ❓ 假设待验（hypotheses）

```dataview
TABLE id AS "ID", priority AS "优先级", status AS "状态", date AS "日期"
FROM #hypothesis
WHERE type = "hypothesis" AND status != "obsolete"
SORT priority ASC, date DESC
```

## 📚 文献知识（literature）

```dataview
TABLE id AS "ID", date AS "日期"
FROM #literature
WHERE type = "literature"
SORT date DESC
```

## 🧩 实体卡片（entities）

```dataview
TABLE id AS "ID", date AS "日期"
FROM #entity
WHERE type = "entity"
SORT date DESC
```

## 📖 可复用流程（playbooks）

```dataview
TABLE id AS "ID", date AS "日期"
FROM #playbook
WHERE type = "procedural"
SORT date DESC
```
