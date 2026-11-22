# 跨赛区裁判培训的试题暴露与资格证据治理

赛区 east 和 west 的共同题以 common 标记。资格区分 review 复判和 field 现场执裁，正式通知由主管保存。

records 保存可离线使用的合成业务记录。每行一个 UTF-8 JSON 对象，id 在各数据表内唯一，跨表键沿用业务记录中的标识。catalog.json 保存行数及原始文件校验值。

| 记录 | 业务含义 |
| --- | --- |
| items.jsonl | 共同题 form=common。item_fact 指判定必需的原案事实。同案同岗共享证据来源。 |
| applicants.jsonl | 地区是应考卷，course 为实际参加的讲评班。 |
| answers.jsonl | score 取 0 或 1，submit 为正式提交。 |
| disclosures.jsonl | attendees 指确已听到讲评的人。scheduled 但未 actual 的记录仅用于未来命题。received 为台账登记时刻。 |
| policy.jsonl | distinct_cases 是每岗最低不同原案数。anchors 是每类卷间最低可比共同题数。 |
| forms.jsonl | 下一轮考核投放地区、题量、时间和通知截止。 |
| notices.jsonl | 已发送资格通知及审核采用的资料时点。 |

## 核对资料

运行环境为 Python 3.11，使用标准库，无需下载依赖。用 `python3 -m unittest discover -s tests -v` 核对发行资料的完整性与约定的输入关系。
