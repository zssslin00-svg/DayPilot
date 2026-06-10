PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS user_profile (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  display_name TEXT NOT NULL DEFAULT 'DayPilot User',
  long_term_direction TEXT NOT NULL,
  current_focus_projects TEXT NOT NULL DEFAULT '[]',
  goal_preferences TEXT NOT NULL DEFAULT '{}',
  avoid_patterns TEXT NOT NULL DEFAULT '[]',
  default_available_minutes INTEGER NOT NULL DEFAULT 90
    CHECK (default_available_minutes BETWEEN 15 AND 360),
  timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
  workday_rule TEXT NOT NULL DEFAULT '{"days":[1,2,3,4,5]}',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  priority TEXT NOT NULL DEFAULT 'P2'
    CHECK (priority IN ('P0', 'P1', 'P2')),
  role TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'paused', 'completed', 'archived')),
  status_summary TEXT NOT NULL DEFAULT '',
  planning_bias TEXT NOT NULL DEFAULT '',
  source_payload TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS daily_goals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id INTEGER NOT NULL DEFAULT 1,
  project_id INTEGER NOT NULL,
  goal_date TEXT NOT NULL,
  week_id TEXT NOT NULL,
  weekday INTEGER NOT NULL CHECK (weekday BETWEEN 1 AND 7),
  is_workday INTEGER NOT NULL CHECK (is_workday IN (0, 1)),
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'checked_in', 'skipped', 'archived')),
  active_version_id INTEGER,
  context_snapshot TEXT NOT NULL DEFAULT '{}',
  revision_count INTEGER NOT NULL DEFAULT 0 CHECK (revision_count >= 0),
  generated_at TEXT,
  checked_in_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (profile_id) REFERENCES user_profile(id),
  FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
  FOREIGN KEY (active_version_id) REFERENCES goal_versions(id)
    ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,
  UNIQUE (goal_date, project_id)
);

CREATE TABLE IF NOT EXISTS goal_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  daily_goal_id INTEGER NOT NULL,
  version_no INTEGER NOT NULL CHECK (version_no >= 1),
  is_active INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
  main_goal TEXT NOT NULL,
  goal_reason TEXT,
  success_criteria TEXT NOT NULL DEFAULT '[]',
  estimated_minutes INTEGER CHECK (estimated_minutes IS NULL OR estimated_minutes > 0),
  difficulty_level INTEGER CHECK (difficulty_level IS NULL OR difficulty_level BETWEEN 1 AND 5),
  minimum_version TEXT NOT NULL,
  stretch_challenge TEXT,
  avoid_today TEXT,
  goal_type TEXT,
  revision_source TEXT NOT NULL
    CHECK (revision_source IN ('initial_generation', 'user_feedback', 'system_regeneration')),
  revision_reason TEXT,
  feedback_message_id INTEGER,
  critic_result TEXT NOT NULL DEFAULT '{}',
  prompt_version TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (daily_goal_id) REFERENCES daily_goals(id) ON DELETE CASCADE,
  FOREIGN KEY (feedback_message_id) REFERENCES feedback_messages(id) ON DELETE SET NULL,
  UNIQUE (daily_goal_id, version_no)
);

CREATE TABLE IF NOT EXISTS daily_checkins (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  daily_goal_id INTEGER NOT NULL UNIQUE,
  checkin_date TEXT NOT NULL,
  week_id TEXT NOT NULL,
  is_workday INTEGER NOT NULL DEFAULT 1 CHECK (is_workday IN (0, 1)),
  completion_status TEXT NOT NULL DEFAULT 'completed'
    CHECK (completion_status IN ('completed', 'incomplete')),
  completion_text TEXT NOT NULL,
  felt_difficulty INTEGER NOT NULL CHECK (felt_difficulty BETWEEN 1 AND 5),
  tomorrow_direction TEXT NULL,
  parsed_completion_rate REAL CHECK (
    parsed_completion_rate IS NULL OR parsed_completion_rate BETWEEN 0 AND 1
  ),
  completed_items TEXT NOT NULL DEFAULT '[]',
  unfinished_items TEXT NOT NULL DEFAULT '[]',
  blockers TEXT NOT NULL DEFAULT '[]',
  actual_outputs TEXT NOT NULL DEFAULT '[]',
  processor_snapshot TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (daily_goal_id) REFERENCES daily_goals(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS project_progress_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  event_date TEXT NOT NULL,
  source_type TEXT NOT NULL
    CHECK (source_type IN ('daily_checkin')),
  source_id INTEGER NOT NULL,
  event_status TEXT NOT NULL DEFAULT 'active'
    CHECK (event_status IN ('active', 'superseded')),
  progress_delta TEXT NOT NULL,
  evidence_text TEXT NOT NULL,
  confidence REAL CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
  applied_to_summary INTEGER NOT NULL DEFAULT 1 CHECK (applied_to_summary IN (0, 1)),
  previous_status_summary TEXT,
  new_status_summary TEXT NOT NULL,
  reason TEXT,
  llm_metadata TEXT NOT NULL DEFAULT '{}',
  raw_output TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
  FOREIGN KEY (source_id) REFERENCES daily_checkins(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS project_lifecycle_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_date TEXT NOT NULL DEFAULT (date('now')),
  raw_message TEXT NOT NULL,
  action TEXT NOT NULL
    CHECK (action IN ('create_project', 'complete_project', 'update_project', 'no_change')),
  project_id INTEGER,
  project_name TEXT,
  priority TEXT,
  previous_status TEXT,
  new_status TEXT,
  previous_status_summary TEXT,
  new_status_summary TEXT,
  planning_bias TEXT,
  confidence REAL CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
  applied INTEGER NOT NULL DEFAULT 0 CHECK (applied IN (0, 1)),
  reason TEXT,
  llm_metadata TEXT NOT NULL DEFAULT '{}',
  raw_output TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS feedback_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  daily_goal_id INTEGER NOT NULL,
  before_version_id INTEGER,
  after_version_id INTEGER,
  raw_message TEXT NOT NULL,
  feedback_type TEXT NOT NULL
    CHECK (feedback_type IN (
      'day_constraint',
      'short_term_preference',
      'long_term_preference',
      'quality_issue',
      'other'
    )),
  affected_scope TEXT NOT NULL
    CHECK (affected_scope IN ('today', 'next_3_7_days', 'long_term', 'current_goal')),
  interpretation_json TEXT NOT NULL DEFAULT '{}',
  extracted_constraints TEXT NOT NULL DEFAULT '{}',
  extracted_preferences TEXT NOT NULL DEFAULT '{}',
  memory_action TEXT NOT NULL DEFAULT 'none'
    CHECK (memory_action IN (
      'none',
      'update_short_term_preference',
      'update_long_term_preference',
      'update_guardrail'
    )),
  should_regenerate_goal INTEGER NOT NULL DEFAULT 1 CHECK (should_regenerate_goal IN (0, 1)),
  is_resolved INTEGER NOT NULL DEFAULT 0 CHECK (is_resolved IN (0, 1)),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (daily_goal_id) REFERENCES daily_goals(id) ON DELETE CASCADE,
  FOREIGN KEY (before_version_id) REFERENCES goal_versions(id) ON DELETE SET NULL,
  FOREIGN KEY (after_version_id) REFERENCES goal_versions(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS profile_memory_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  feedback_message_id INTEGER,
  daily_goal_id INTEGER,
  raw_feedback TEXT NOT NULL,
  preference_items TEXT NOT NULL DEFAULT '[]',
  avoid_items TEXT NOT NULL DEFAULT '[]',
  time_scope_rules TEXT NOT NULL DEFAULT '[]',
  ignored_items TEXT NOT NULL DEFAULT '[]',
  previous_goal_preferences TEXT NOT NULL DEFAULT '{}',
  new_goal_preferences TEXT NOT NULL DEFAULT '{}',
  soul_backup_path TEXT,
  confidence REAL CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
  applied INTEGER NOT NULL DEFAULT 0 CHECK (applied IN (0, 1)),
  reason TEXT,
  llm_metadata TEXT NOT NULL DEFAULT '{}',
  raw_output TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (feedback_message_id) REFERENCES feedback_messages(id) ON DELETE SET NULL,
  FOREIGN KEY (daily_goal_id) REFERENCES daily_goals(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS soul_sync_retry_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_type TEXT NOT NULL
    CHECK (job_type IN ('profile_memory', 'project_lifecycle')),
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'retrying', 'succeeded', 'failed')),
  source_table TEXT,
  source_id INTEGER,
  payload TEXT NOT NULL DEFAULT '{}',
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  last_error TEXT,
  next_retry_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ability_state (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  state_date TEXT NOT NULL,
  source_checkin_id INTEGER,
  source_feedback_message_id INTEGER,
  current_difficulty REAL NOT NULL CHECK (current_difficulty BETWEEN 1 AND 5),
  target_difficulty_level INTEGER NOT NULL CHECK (target_difficulty_level BETWEEN 1 AND 5),
  recent_completion_rate REAL CHECK (
    recent_completion_rate IS NULL OR recent_completion_rate BETWEEN 0 AND 1
  ),
  recent_felt_difficulty_avg REAL CHECK (
    recent_felt_difficulty_avg IS NULL OR recent_felt_difficulty_avg BETWEEN 1 AND 5
  ),
  completion_streak INTEGER NOT NULL DEFAULT 0 CHECK (completion_streak >= 0),
  low_completion_streak INTEGER NOT NULL DEFAULT 0 CHECK (low_completion_streak >= 0),
  overload_count INTEGER NOT NULL DEFAULT 0 CHECK (overload_count >= 0),
  underload_count INTEGER NOT NULL DEFAULT 0 CHECK (underload_count >= 0),
  default_estimated_minutes INTEGER NOT NULL DEFAULT 90 CHECK (default_estimated_minutes > 0),
  preferred_goal_type_weights TEXT NOT NULL DEFAULT '{}',
  short_term_preferences TEXT NOT NULL DEFAULT '{}',
  long_term_preferences_snapshot TEXT NOT NULL DEFAULT '{}',
  avoid_patterns_snapshot TEXT NOT NULL DEFAULT '[]',
  adjustment_direction TEXT NOT NULL DEFAULT 'hold'
    CHECK (adjustment_direction IN ('increase', 'decrease', 'hold', 'change_direction', 'initial')),
  update_reason TEXT NOT NULL,
  is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (source_checkin_id) REFERENCES daily_checkins(id) ON DELETE SET NULL,
  FOREIGN KEY (source_feedback_message_id) REFERENCES feedback_messages(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS weekly_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  week_id TEXT NOT NULL UNIQUE,
  week_start_date TEXT NOT NULL,
  week_end_date TEXT NOT NULL,
  generated_on_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'final'
    CHECK (status IN ('draft', 'final', 'regenerated')),
  completed_work TEXT NOT NULL,
  next_week_plan TEXT NOT NULL,
  weekly_reflection TEXT NOT NULL,
  report_text TEXT NOT NULL,
  source_snapshot TEXT NOT NULL,
  next_week_focus_summary TEXT,
  quality_score INTEGER CHECK (quality_score IS NULL OR quality_score BETWEEN 1 AND 5),
  prompt_version TEXT,
  model_name TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS weekly_report_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  weekly_report_id INTEGER NOT NULL,
  week_id TEXT NOT NULL,
  version_no INTEGER NOT NULL CHECK (version_no >= 1),
  revision_source TEXT NOT NULL
    CHECK (revision_source IN (
      'initial_generation',
      'manual_regeneration',
      'checkin_refresh',
      'user_feedback',
      'current_snapshot'
    )),
  revision_reason TEXT,
  feedback_message TEXT,
  completed_work TEXT NOT NULL,
  next_week_plan TEXT NOT NULL,
  weekly_reflection TEXT NOT NULL,
  report_text TEXT NOT NULL,
  source_snapshot TEXT NOT NULL DEFAULT '{}',
  llm_metadata TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (weekly_report_id) REFERENCES weekly_reports(id) ON DELETE CASCADE,
  UNIQUE (weekly_report_id, version_no)
);

CREATE TABLE IF NOT EXISTS weekly_focus (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  weekly_report_id INTEGER NOT NULL,
  source_week_id TEXT NOT NULL,
  target_week_id TEXT NOT NULL,
  focus_order INTEGER NOT NULL CHECK (focus_order >= 1),
  focus_text TEXT NOT NULL,
  desired_outcome TEXT NOT NULL,
  focus_type TEXT,
  priority INTEGER NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'used', 'archived', 'superseded')),
  context_payload TEXT NOT NULL DEFAULT '{}',
  carried_into_goal_id INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (weekly_report_id) REFERENCES weekly_reports(id) ON DELETE CASCADE,
  FOREIGN KEY (carried_into_goal_id) REFERENCES daily_goals(id) ON DELETE SET NULL,
  UNIQUE (weekly_report_id, focus_order)
);

CREATE INDEX IF NOT EXISTS idx_daily_goals_week ON daily_goals(week_id, goal_date);
CREATE INDEX IF NOT EXISTS idx_daily_goals_status ON daily_goals(status, goal_date);
CREATE INDEX IF NOT EXISTS idx_projects_priority ON projects(priority, status, id);
CREATE INDEX IF NOT EXISTS idx_project_progress_project_date
  ON project_progress_events(project_id, event_date, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_project_progress_active_source
  ON project_progress_events(source_type, source_id)
  WHERE event_status = 'active';
CREATE INDEX IF NOT EXISTS idx_project_lifecycle_project_date
  ON project_lifecycle_events(project_id, event_date, created_at);
CREATE INDEX IF NOT EXISTS idx_goal_versions_goal ON goal_versions(daily_goal_id, version_no);
CREATE UNIQUE INDEX IF NOT EXISTS uq_goal_versions_one_active
  ON goal_versions(daily_goal_id)
  WHERE is_active = 1;
CREATE INDEX IF NOT EXISTS idx_checkins_week ON daily_checkins(week_id, checkin_date);
CREATE INDEX IF NOT EXISTS idx_feedback_goal_created ON feedback_messages(daily_goal_id, created_at);
CREATE INDEX IF NOT EXISTS idx_feedback_type_scope ON feedback_messages(feedback_type, affected_scope);
CREATE INDEX IF NOT EXISTS idx_profile_memory_feedback
  ON profile_memory_events(feedback_message_id, created_at);
CREATE INDEX IF NOT EXISTS idx_soul_sync_retry_status
  ON soul_sync_retry_jobs(status, next_retry_at, created_at);
CREATE INDEX IF NOT EXISTS idx_ability_state_date ON ability_state(state_date);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ability_state_current
  ON ability_state(is_current)
  WHERE is_current = 1;
CREATE INDEX IF NOT EXISTS idx_weekly_report_versions_report
  ON weekly_report_versions(weekly_report_id, version_no);
CREATE INDEX IF NOT EXISTS idx_weekly_focus_target_status
  ON weekly_focus(target_week_id, status, priority);
