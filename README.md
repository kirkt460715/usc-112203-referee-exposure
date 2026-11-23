# 跨赛区裁判培训的试题暴露与资格证据治理

赛区 east 和 west 的共同题以 common 标记。资格区分 review 复判和 field 现场执裁，正式通知由主管保存。

records 保存可离线使用的合成业务记录。每行一个 UTF-8 JSON 对象，id 在各数据表内唯一，跨表键沿用业务记录中的标识。catalog.json 保存行数及原始文件校验值。

| 记录 | 业务含义 |
| --- | --- |
| items.jsonl | 共同题 form=common。item_fact 指判定必需的原案事实。同案同岗共享证据来源。 |
| applicants.jsonl | 地区是应考卷，course 为实际参加的讲评班。 |
| answers.jsonl | score 为失误分：0 = 判定正确，1 = 判定错误；submit 为正式提交。 |
| disclosures.jsonl | attendees 指确已听到讲评的人。scheduled 但未 actual 的记录仅用于未来命题。received 为台账登记时刻。 |
| policy.jsonl | distinct_cases 是每岗最低不同原案数。anchors 是每类卷间最低可比共同题数。 |
| forms.jsonl | 下一轮考核投放地区、题量、时间和通知截止。 |
| notices.jsonl | 已发送资格通知及审核采用的资料时点。 |

## 核对资料

运行环境为 Python 3.11，使用标准库，无需下载依赖。用 `python3 -m unittest discover -s tests -v` 核对发行资料的完整性与约定的输入关系。

## 资格证据核对

`python3 audit.py` 读取 records/，输出可复核的 Markdown 结论；`python3 audit.py > report.md` 可留档。判定口径写在脚本头部，要点如下：

- `score=0` 为判定正确。两份已发通知的持有人答卷全为 0，且在签发时点均达标，只有这一读法自洽。
- 个人答卷的剔除条件：讲评已实际举行（`actual` 非空）、本人在 `attendees`、讲评事实等于题目 `item_fact`、且讲评时间早于本卷 `submit`。`received` 只用于还原“通知签发时主管手上有哪些信息”，不改变证据本身。
- 同一原案的派生题按原案合并计分，不作相互独立的同类证据。
- 共同题依赖的事实一经任何赛区课堂揭示，该题即失去跨卷可比资格；不能删掉疑似题目后沿用原分数线。
- 覆盖不足、共同项失效（不可比）、低于续签线三类问题分别列示；三项都过才继续有效。
- 未来命题用 `actual or scheduled`：已排期未举行的讲评也会烧掉相应题目。
- 已发通知按“签发时点资料”和“当前台账”各算一次：签发后登记且改变结论的课堂信息列为重审理由；正常执裁仍以主管确认的通知为准。
