请基于以下上下文，生成 {{goal_date}} 的 DayPilot 每日主目标。

只返回符合 DailyGoalOutput JSON Schema 的 JSON 对象，不要返回 Markdown，不要返回额外解释。

[用户画像]
长期发展方向：{{user_profile.long_term_direction}}
当前重点项目：{{user_profile.current_projects}}
目标偏好：{{user_profile.goal_preferences}}
需要避免的模式：{{user_profile.avoid_patterns}}

[最近 7 天每日目标]
{{#each recent_daily_goals}}
- 日期：{{date}}
  目标：{{main_goal}}
  类型：{{goal_type}}
  预计耗时：{{estimated_minutes}} 分钟
  难度：{{difficulty}}
  状态：{{status}}
{{/each}}

[最近 7 天完成情况]
{{#each recent_checkins}}
- 日期：{{date}}
  完成率：{{completion_rate}}
  用户感觉难度：{{felt_difficulty}}
  完成内容：{{completed_items}}
  未完成内容：{{unfinished_items}}
  卡点：{{blockers}}
  明天希望工作方向：{{tomorrow_direction}}
{{/each}}

[最近的在线目标调整反馈]
{{#each recent_feedback_messages}}
- 时间：{{created_at}}
  用户原话：{{message}}
  反馈类型：{{feedback_type}}
  影响范围：{{scope}}
  结构化信号：{{structured_signal}}
{{/each}}

[当前能力状态]
current_difficulty：{{ability_state.current_difficulty}}
recent_completion_rate：{{ability_state.recent_completion_rate}}
recent_felt_difficulty_avg：{{ability_state.recent_felt_difficulty_avg}}
preferred_goal_type_weights：{{ability_state.preferred_goal_type_weights}}
difficulty_update_reason：{{ability_state.update_reason}}

[明天希望工作方向]
{{tomorrow_direction_or_empty}}

[上周周报的下周重点]
{{last_week_report.next_week_focus}}

生成前请执行以下判断：

1. 如果明天方向为空，基于当前项目、最近记录、上周重点和能力状态自主决定。
2. 如果明天方向过大，缩小成 30-150 分钟内可推进的交付物。
3. 如果最近多次未完成或用户觉得困难，降低目标颗粒度。
4. 如果最近反馈中出现“太虚”，目标、完成标准和最低成果必须包含明确交付物。
5. 最终只能输出一个主目标。
