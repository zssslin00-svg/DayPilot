const API_BASE =
  new URLSearchParams(window.location.search).get("api") ||
  window.DAYPILOT_API_BASE ||
  "http://127.0.0.1:8000";
const DAYPILOT_TIME_ZONE = "Asia/Shanghai";
const CROSS_DAY_CHECK_INTERVAL_MS = 60 * 1000;

const elements = {
  tabs: Array.from(document.querySelectorAll(".tab-button")),
  views: Array.from(document.querySelectorAll(".view-panel")),
  alert: document.querySelector("#app-alert"),
  alertMessage: document.querySelector("#app-alert-message"),
  alertClose: document.querySelector("#app-alert-close"),
  projectOpen: document.querySelector("#project-update-open"),
  projectModal: document.querySelector("#project-modal"),
  projectClose: document.querySelector("#project-update-close"),
  projectActiveList: document.querySelector("#project-active-list"),
  projectCompletedList: document.querySelector("#project-completed-list"),
  projectForm: document.querySelector("#project-lifecycle-form"),
  projectMessage: document.querySelector("#project-lifecycle-message"),
  projectSubmit: document.querySelector("#project-lifecycle-submit"),
  projectResult: document.querySelector("#project-lifecycle-result"),
  todayRefresh: document.querySelector("#today-refresh"),
  historyRefresh: document.querySelector("#history-refresh"),
  todayEmpty: document.querySelector("#today-empty"),
  goalContent: document.querySelector("#goal-content"),
  mainGoal: document.querySelector("#main-goal"),
  goalDate: document.querySelector("#goal-date"),
  estimatedMinutes: document.querySelector("#estimated-minutes"),
  difficultyValue: document.querySelector("#difficulty-value"),
  goalType: document.querySelector("#goal-type"),
  completionCriteria: document.querySelector("#completion-criteria"),
  minimumResult: document.querySelector("#minimum-result"),
  feedbackForm: document.querySelector("#goal-feedback-form"),
  feedbackMessage: document.querySelector("#goal-feedback-message"),
  feedbackSubmit: document.querySelector("#goal-feedback-submit"),
  checkinForm: document.querySelector("#checkin-form"),
  completionText: document.querySelector("#completion-text"),
  tomorrowDirection: document.querySelector("#tomorrow-direction"),
  checkinSubmit: document.querySelector("#checkin-submit"),
  historyList: document.querySelector("#history-list"),
  weeklyGenerate: document.querySelector("#weekly-report-generate"),
  weeklyEmpty: document.querySelector("#weekly-empty"),
  weeklyContent: document.querySelector("#weekly-report-content"),
  weeklyWeek: document.querySelector("#weekly-report-week"),
  weeklyUpdated: document.querySelector("#weekly-report-updated"),
  weeklyCompleted: document.querySelector("#weekly-completed-work"),
  weeklyNextPlan: document.querySelector("#weekly-next-plan"),
  weeklyReflection: document.querySelector("#weekly-reflection"),
  weeklyFeedbackForm: document.querySelector("#weekly-feedback-form"),
  weeklyFeedbackMessage: document.querySelector("#weekly-feedback-message"),
  weeklyFeedbackSubmit: document.querySelector("#weekly-feedback-submit"),
  weeklyVersions: document.querySelector("#weekly-report-versions"),
  careerNewSession: document.querySelector("#career-new-session"),
  careerSessionList: document.querySelector("#career-session-list"),
  careerMessageList: document.querySelector("#career-message-list"),
  careerForm: document.querySelector("#career-chat-form"),
  careerMessage: document.querySelector("#career-chat-message"),
  careerAvailableMinutes: document.querySelector("#career-available-minutes"),
  careerSubmit: document.querySelector("#career-chat-submit"),
  careerResults: document.querySelector("#career-results"),
  careerRecommendations: document.querySelector("#career-recommendations"),
  careerProfileSuggestions: document.querySelector("#career-profile-suggestions"),
  careerSuggestionList: document.querySelector("#career-suggestion-list"),
};

let currentGoalRecord = null;
let currentGoalRecords = [];
let currentApiDate = null;
let checkedInGoalDate = null;
let checkedInGoalIds = new Set();
let currentWeekId = null;
let canGenerateWeeklyReport = false;
let latestWeeklyBundle = null;
let currentCareerSessionId = null;
let careerLoaded = false;
let currentClientDate = chinaDateString();

bindEvents();
startCrossDayRefreshWatcher();
setTodayFormsEnabled(false);
setWeeklyFeedbackEnabled(false);
loadInitialData();

function bindEvents() {
  elements.tabs.forEach((tab) => {
    tab.addEventListener("click", () => switchView(tab.dataset.view));
  });
  elements.alertClose.addEventListener("click", hideAlert);
  elements.projectOpen.addEventListener("click", openProjectModal);
  elements.projectClose.addEventListener("click", closeProjectModal);
  elements.projectModal.addEventListener("click", (event) => {
    if (event.target === elements.projectModal) {
      closeProjectModal();
    }
  });
  elements.projectForm.addEventListener("submit", handleProjectLifecycleSubmit);
  elements.todayRefresh.addEventListener("click", handleTodayRefresh);
  elements.historyRefresh.addEventListener("click", loadHistory);
  elements.feedbackForm.addEventListener("submit", handleGoalFeedbackSubmit);
  elements.checkinForm.addEventListener("submit", handleCheckinSubmit);
  elements.weeklyGenerate.addEventListener("click", handleWeeklyReportGenerate);
  elements.weeklyFeedbackForm.addEventListener("submit", handleWeeklyFeedbackSubmit);
  elements.careerNewSession.addEventListener("click", startNewCareerSession);
  elements.careerForm.addEventListener("submit", handleCareerChatSubmit);
}

async function loadInitialData() {
  hideAlert();
  await loadTodayGoal();
}

async function handleTodayRefresh() {
  hideAlert();
  await regenerateTodayGoal();
  await loadHistory();
}

function startCrossDayRefreshWatcher() {
  window.setInterval(checkForCrossDayRefresh, CROSS_DAY_CHECK_INTERVAL_MS);
  window.addEventListener("focus", checkForCrossDayRefresh);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      checkForCrossDayRefresh();
    }
  });
}

function checkForCrossDayRefresh() {
  const nextDate = chinaDateString();
  if (nextDate === currentClientDate) {
    return;
  }
  currentClientDate = nextDate;
  resetTodayInputs();
  loadInitialData();
}

function chinaDateString(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: DAYPILOT_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function resetTodayInputs() {
  elements.feedbackMessage.value = "";
  elements.completionText.value = "";
  elements.tomorrowDirection.value = "";
  elements.goalContent.querySelectorAll("textarea").forEach((textarea) => {
    textarea.value = "";
  });
  elements.goalContent.querySelectorAll('input[type="radio"][value="completed"]').forEach((input) => {
    input.checked = true;
  });
  elements.goalContent.querySelectorAll('input[type="radio"][value="3"]').forEach((input) => {
    if (input.name.startsWith("felt_difficulty_")) {
      input.checked = true;
    }
  });
  const defaultDifficulty = elements.checkinForm.querySelector('input[name="felt_difficulty"][value="3"]');
  if (defaultDifficulty) {
    defaultDifficulty.checked = true;
  }
}

async function loadTodayGoal() {
  currentGoalRecord = null;
  currentGoalRecords = [];
  currentApiDate = null;
  currentWeekId = null;
  setTodayFormsEnabled(false);
  renderGoalEmpty("正在读取今日目标。");

  try {
    const payload = await requestJson("/api/today-goal");
    await renderTodayGoalAndSyncHistory(payload);
  } catch (error) {
    renderGoalEmpty("今日目标读取失败。");
    showAlert(errorMessage(error));
  }
}

async function regenerateTodayGoal() {
  currentGoalRecord = null;
  currentGoalRecords = [];
  currentApiDate = null;
  currentWeekId = null;
  setTodayFormsEnabled(false);
  renderGoalEmpty("正在重新生成今日目标。");

  try {
    const payload = await requestJson("/api/today-goal/regenerate", { method: "POST" });
    await renderTodayGoalAndSyncHistory(payload);
  } catch (error) {
    renderGoalEmpty("今日目标重新生成失败。");
    showAlert(errorMessage(error));
  }
}

async function loadHistory() {
  try {
    const payload = await requestJson("/api/history?days=30");
    renderHistory(payload.daily_records || []);
    renderLatestWeekly(payload.weekly_reports || []);
    syncTodayCheckin(payload.daily_records || []);
  } catch (error) {
    showAlert(errorMessage(error));
  }
}

async function openProjectModal() {
  hideAlert();
  elements.projectModal.hidden = false;
  elements.projectResult.textContent = "";
  await loadProjects();
  elements.projectMessage.focus();
}

function closeProjectModal() {
  elements.projectModal.hidden = true;
  elements.projectResult.textContent = "";
}

async function loadProjects() {
  try {
    const payload = await requestJson("/api/projects");
    renderProjectLists(payload.active_projects || [], payload.completed_projects || []);
  } catch (error) {
    showAlert(errorMessage(error));
  }
}

function renderProjectLists(activeProjects, completedProjects) {
  renderProjectList(elements.projectActiveList, activeProjects, "暂无当前项目。");
  renderProjectList(elements.projectCompletedList, completedProjects, "暂无完成项目。");
}

function renderProjectList(container, projects, emptyText) {
  container.replaceChildren();
  if (!projects.length) {
    container.append(emptyBlock(emptyText));
    return;
  }
  projects.forEach((project) => {
    const item = document.createElement("article");
    item.className = "project-item";
    const head = document.createElement("div");
    head.className = "history-head";
    head.append(textBlock("strong", project.name || "-"));
    head.append(textBlock("span", project.status || "active"));
    const summary = document.createElement("p");
    summary.className = "muted compact";
    summary.textContent = project.status_summary || project.planning_bias || "暂无摘要。";
    item.append(head, summary);
    container.append(item);
  });
}

async function handleProjectLifecycleSubmit(event) {
  event.preventDefault();
  const message = elements.projectMessage.value.trim();
  if (!message) {
    showAlert("项目更新内容不能为空。");
    elements.projectMessage.focus();
    return;
  }

  setBusy(elements.projectSubmit, true);
  try {
    const payload = await requestJson("/api/projects/lifecycle", {
      method: "POST",
      body: { message },
    });
    if (payload.status === "failed") {
      showAlert(payload.reason || "项目更新失败。");
      return;
    }
    elements.projectMessage.value = "";
    elements.projectResult.textContent = payload.message || "项目信息已更新。";
    await loadProjects();
    await loadInitialData();
  } catch (error) {
    showAlert(errorMessage(error));
  } finally {
    setBusy(elements.projectSubmit, false);
  }
}

async function renderTodayGoalAndSyncHistory(payload) {
  renderTodayGoal(payload);
  await loadHistory();
}

function renderTodayGoal(payload) {
  currentApiDate = payload.date || null;
  resetCheckedInGoalsForDate(currentApiDate);

  if (payload.is_workday === false) {
    renderGoalEmpty(payload.message || "今天不是工作日。");
    return;
  }

  const goalRecords = Array.isArray(payload.goals) ? payload.goals : payload.goal ? [payload.goal] : [];
  if (!goalRecords.length) {
    renderGoalEmpty("当前没有 active 项目，今天没有可生成的项目目标。");
    return;
  }

  currentGoalRecords = goalRecords;
  markCheckedInGoalsFromRecords(goalRecords);
  currentWeekId = goalRecords[0]?.daily_goal?.week_id || null;
  renderVisibleTodayGoalCards(payload.date);
  setTodayFormsEnabled(false);
  const generatedThisRequest =
    Boolean(payload.created) ||
    Number(payload.created_count || 0) > 0 ||
    Number(payload.carried_over_count || 0) > 0;
  if (generatedThisRequest) {
    showFallbackIfAny(
      goalRecords.flatMap((goalRecord) => [
        goalRecord.daily_goal?.context_snapshot,
        goalRecord.active_version?.critic_result,
      ]),
    );
  } else {
    hideAlert();
  }
}

function renderVisibleTodayGoalCards(fallbackDate = currentApiDate) {
  const visibleGoalRecords = currentGoalRecords.filter((goalRecord) => !isCheckedInGoalRecord(goalRecord));
  currentGoalRecord = visibleGoalRecords[0] || null;
  elements.goalContent.replaceChildren();

  if (!visibleGoalRecords.length) {
    renderGoalEmpty("今天的项目都已 check-in，记录可在历史中修改。");
    return visibleGoalRecords;
  }

  elements.todayEmpty.hidden = true;
  elements.goalContent.hidden = false;
  const legacyTaskGrid = elements.checkinForm?.closest(".task-grid");
  if (legacyTaskGrid) {
    legacyTaskGrid.hidden = true;
  }
  elements.goalContent.className = "project-goal-list";
  visibleGoalRecords.forEach((goalRecord) => {
    elements.goalContent.append(projectGoalCard(goalRecord, fallbackDate));
  });
  return visibleGoalRecords;
}

function renderGoalEmpty(message) {
  elements.todayEmpty.textContent = message;
  elements.todayEmpty.hidden = false;
  elements.goalContent.hidden = true;
  elements.goalContent.replaceChildren();
  const legacyTaskGrid = elements.checkinForm?.closest(".task-grid");
  if (legacyTaskGrid) {
    legacyTaskGrid.hidden = true;
  }
  setTodayFormsEnabled(false);
}

function resetCheckedInGoalsForDate(goalDate) {
  if (!goalDate) {
    checkedInGoalDate = null;
    checkedInGoalIds = new Set();
    return;
  }
  if (checkedInGoalDate !== goalDate) {
    checkedInGoalDate = goalDate;
    checkedInGoalIds = new Set();
  }
}

function markCheckedInGoalsFromRecords(records) {
  records.forEach((record) => {
    const goalId = goalIdFromRecord(record);
    if (!goalId) {
      return;
    }
    const status = String(record?.daily_goal?.status || "");
    if (status === "checked_in" || record?.daily_checkin) {
      checkedInGoalIds.add(goalId);
    }
  });
}

function markTodayGoalCheckedIn(goalId, checkin) {
  const normalizedGoalId = Number(goalId);
  if (!Number.isFinite(normalizedGoalId)) {
    return;
  }
  checkedInGoalIds.add(normalizedGoalId);
  currentGoalRecords = currentGoalRecords.map((goalRecord) => {
    if (goalIdFromRecord(goalRecord) !== normalizedGoalId) {
      return goalRecord;
    }
    return {
      ...goalRecord,
      daily_goal: {
        ...(goalRecord.daily_goal || {}),
        status: "checked_in",
        checked_in_at: checkin?.updated_at || checkin?.created_at || goalRecord.daily_goal?.checked_in_at || null,
      },
      daily_checkin: checkin || goalRecord.daily_checkin || null,
    };
  });
  renderVisibleTodayGoalCards(currentApiDate);
}

function isCheckedInGoalRecord(goalRecord) {
  const goalId = goalIdFromRecord(goalRecord);
  const status = String(goalRecord?.daily_goal?.status || "");
  return Boolean(goalId && checkedInGoalIds.has(goalId)) || status === "checked_in" || Boolean(goalRecord?.daily_checkin);
}

function goalIdFromRecord(goalRecord) {
  const goalId = Number(goalRecord?.daily_goal?.id);
  return Number.isFinite(goalId) ? goalId : null;
}

function projectGoalCard(goalRecord, fallbackDate) {
  const goal = goalRecord?.goal_output || {};
  const dailyGoal = goalRecord?.daily_goal || {};
  const project = goalRecord?.project || {};
  const goalId = dailyGoal.id;
  const card = document.createElement("article");
  card.className = "project-goal-card";
  card.dataset.goalId = goalId || "";

  const header = document.createElement("div");
  header.className = "project-goal-header";
  const projectName = textBlock("strong", project.name || `项目 ${dailyGoal.project_id || "-"}`);
  projectName.className = "project-goal-project";
  const metrics = document.createElement("div");
  metrics.className = "metric-row project-goal-chips";
  metrics.append(textBlock("span", goal.goal_date || fallbackDate || ""));
  metrics.append(textBlock("span", `${numberOrDash(goal.estimated_minutes)} 分钟`));
  metrics.append(textBlock("span", `难度 ${numberOrDash(goal.difficulty)}/5`));
  metrics.append(textBlock("span", goal.goal_type || "未分类"));
  header.append(projectName, metrics);

  const titleBlock = document.createElement("div");
  titleBlock.className = "goal-title-block";
  titleBlock.append(textBlock("span", "今日目标"));
  const title = document.createElement("h3");
  title.textContent = displayGoalTitle(goal.main_goal);
  titleBlock.append(title);

  const criteria = document.createElement("section");
  criteria.className = "goal-info-block";
  criteria.append(textBlock("h4", "完成标准"));
  const criteriaList = document.createElement("ol");
  renderList(criteriaList, goal.completion_criteria, "暂无完成标准。");
  criteria.append(criteriaList);

  const minimum = document.createElement("section");
  minimum.className = "goal-info-block";
  minimum.append(textBlock("h4", "最低成果"));
  minimum.append(textBlock("p", textOrDash(goal.minimum_acceptable_result)));

  const details = document.createElement("div");
  details.className = "goal-detail-grid";
  details.append(criteria, minimum);

  const actions = document.createElement("div");
  actions.className = "goal-action-grid";
  actions.append(projectFeedbackForm(goalRecord), projectCheckinForm(goalRecord));
  card.append(header, titleBlock, details, actions);
  return card;
}

function projectFeedbackForm(goalRecord) {
  const form = document.createElement("form");
  form.className = "work-form compact-form feedback-compact-form";
  const textarea = document.createElement("textarea");
  textarea.name = "message";
  textarea.rows = 2;
  textarea.required = true;
  textarea.setAttribute("aria-label", "修改意见");
  const button = document.createElement("button");
  button.className = "text-button";
  button.type = "submit";
  button.textContent = "修正该项目目标";
  form.append(textBlock("h4", "反馈修正"), textarea, button);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitProjectGoalFeedback(goalRecord, textarea, button);
  });
  return form;
}

function projectCheckinForm(goalRecord) {
  const form = document.createElement("form");
  form.className = "work-form compact-form checkin-compact-form";
  form.dataset.goalId = goalRecord?.daily_goal?.id || "";
  form.dataset.date = goalRecord?.daily_goal?.goal_date || "";

  const status = document.createElement("fieldset");
  status.append(textBlock("legend", "完成状态"));
  const statusRow = document.createElement("div");
  statusRow.className = "segmented";
  ["completed", "incomplete"].forEach((value) => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "radio";
    input.name = `completion_status_${goalRecord.daily_goal.id}`;
    input.value = value;
    input.checked = value === "completed";
    label.append(input, document.createTextNode(value === "completed" ? "完成" : "未完成"));
    statusRow.append(label);
  });
  status.append(statusRow);

  const completion = document.createElement("textarea");
  completion.name = "completion_text";
  completion.rows = 3;
  completion.setAttribute("aria-label", "完成说明（可空）");
  const completionLabel = document.createElement("label");
  completionLabel.className = "checkin-text-field";
  completionLabel.append(textBlock("span", "完成说明（可空）"), completion);

  const difficulty = document.createElement("fieldset");
  difficulty.append(textBlock("legend", "主观难度"));
  const difficultyRow = document.createElement("div");
  difficultyRow.className = "segmented";
  [1, 2, 3, 4, 5].forEach((value) => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "radio";
    input.name = `felt_difficulty_${goalRecord.daily_goal.id}`;
    input.value = String(value);
    input.checked = value === 3;
    label.append(input, document.createTextNode(String(value)));
    difficultyRow.append(label);
  });
  difficulty.append(difficultyRow);

  const tomorrow = document.createElement("textarea");
  tomorrow.name = "tomorrow_direction";
  tomorrow.rows = 2;
  tomorrow.setAttribute("aria-label", "明天方向（可空）");
  const tomorrowLabel = document.createElement("label");
  tomorrowLabel.className = "checkin-text-field";
  tomorrowLabel.append(textBlock("span", "明天方向（可空）"), tomorrow);

  const button = document.createElement("button");
  button.className = "text-button";
  button.type = "submit";
  button.textContent = "保存该项目 check-in";
  form.append(textBlock("h4", "Check-in"), status, completionLabel, difficulty, tomorrowLabel, button);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = checkinBodyFromForm(form, {
      date: form.dataset.date,
      goalId: form.dataset.goalId,
      completionField: completion,
      tomorrowField: tomorrow,
    });
    if (body) {
      await saveCheckin(body, button);
    }
  });
  return form;
}

async function submitProjectGoalFeedback(goalRecord, textarea, button) {
  const message = textarea.value.trim();
  if (!message) {
    showAlert("反馈内容不能为空。");
    textarea.focus();
    return;
  }
  setBusy(button, true);
  try {
    const payload = await requestJson("/api/goal-feedback", {
      method: "POST",
      body: {
        date: goalRecord.daily_goal.goal_date,
        goal_id: goalRecord.daily_goal.id,
        message,
      },
    });
    textarea.value = "";
    const index = currentGoalRecords.findIndex(
      (record) => Number(record.daily_goal?.id) === Number(goalRecord.daily_goal.id),
    );
    if (index >= 0) {
      currentGoalRecords[index] = payload.updated_goal;
    } else {
      currentGoalRecords = [payload.updated_goal];
    }
    await renderTodayGoalAndSyncHistory({
      date: currentApiDate,
      is_workday: true,
      goals: currentGoalRecords,
    });
  } catch (error) {
    showAlert(errorMessage(error));
  } finally {
    setBusy(button, false);
  }
}

async function handleGoalFeedbackSubmit(event) {
  event.preventDefault();
  if (!currentGoalRecord?.daily_goal?.id || !currentApiDate) {
    showAlert("请先读取今日目标。");
    return;
  }

  const message = elements.feedbackMessage.value.trim();
  if (!message) {
    showAlert("反馈内容不能为空。");
    elements.feedbackMessage.focus();
    return;
  }

  setBusy(elements.feedbackSubmit, true);
  try {
    const payload = await requestJson("/api/goal-feedback", {
      method: "POST",
      body: {
        date: currentApiDate,
        goal_id: currentGoalRecord.daily_goal.id,
        message,
      },
    });
    elements.feedbackMessage.value = "";
    await renderTodayGoalAndSyncHistory({
      date: currentApiDate,
      is_workday: true,
      goal: payload.updated_goal,
    });
    const memoryUpdate = payload.memory_update || {};
    if (memoryUpdate.status === "failed") {
      showAlert(`目标已修正，但用户画像同步失败：${memoryUpdate.reason || "未知原因"}`);
    }
  } catch (error) {
    showAlert(errorMessage(error));
  } finally {
    setBusy(elements.feedbackSubmit, false);
  }
}

async function handleCheckinSubmit(event) {
  event.preventDefault();
  if (!currentGoalRecord?.daily_goal?.id || !currentApiDate) {
    showAlert("请先读取今日目标。");
    return;
  }

  const body = checkinBodyFromForm(elements.checkinForm, {
    date: currentApiDate,
    goalId: currentGoalRecord.daily_goal.id,
    completionField: elements.completionText,
    tomorrowField: elements.tomorrowDirection,
  });
  if (!body) {
    return;
  }

  await saveCheckin(body, elements.checkinSubmit);
}

async function saveCheckin(body, button) {
  hideAlert();
  setBusy(button, true);
  try {
    const notices = [];
    const payload = await requestJson("/api/checkin", {
      method: "POST",
      body,
    });
    markTodayGoalCheckedIn(payload.checkin?.daily_goal_id || body.goal_id, payload.checkin);
    canGenerateWeeklyReport = Boolean(payload.can_generate_weekly_report);
    updateWeeklyGenerateButton();
    await loadHistory();
    const projectProgress = payload.project_progress_update || {};
    if (projectProgress.status === "failed") {
      notices.push(projectProgress.reason || "项目进度自动更新失败。");
    }
    const refresh = payload.weekly_report_refresh || {};
    if (refresh.status === "failed") {
      notices.push(refresh.reason || "check-in 已保存，但周报自动重生成失败。");
    }
    await maybeAutoGenerateWeeklyReportAfterCheckin(payload, notices);
    if (notices.length) {
      showAlert(notices.join(" "));
    }
  } catch (error) {
    showAlert(errorMessage(error));
  } finally {
    setBusy(button, false);
  }
}

async function maybeAutoGenerateWeeklyReportAfterCheckin(checkinPayload, notices) {
  const weekId = checkinPayload.checkin?.week_id || currentWeekId || currentGoalRecord?.daily_goal?.week_id;
  if (!checkinPayload.can_generate_weekly_report || !weekId || weeklyReportExists(weekId)) {
    return;
  }
  try {
    const payload = await requestJson("/api/weekly-report/generate", {
      method: "POST",
      body: { week_id: weekId },
    });
    renderWeeklyReport({
      weekly_report: payload.weekly_report,
      report_output: payload.report_output,
      versions: payload.weekly_report_versions,
      weekly_focus: payload.weekly_focus,
    });
    await loadHistory();
    notices.push("周五 check-in 已保存，并已自动生成本周周报。");
  } catch (error) {
    notices.push(`check-in 已保存，但周报自动生成失败：${errorMessage(error)}`);
  } finally {
    updateWeeklyGenerateButton();
  }
}

function weeklyReportExists(weekId) {
  return latestWeeklyBundle?.weekly_report?.week_id === weekId;
}

function renderHistory(records) {
  elements.historyList.replaceChildren();
  if (!records.length) {
    elements.historyList.append(emptyBlock("最近 30 天还没有记录。"));
    return;
  }

  groupHistoryRecordsByDate(records).forEach((group) => {
    const dayGroup = document.createElement("section");
    dayGroup.className = "history-day-group";

    const dayHeader = document.createElement("div");
    dayHeader.className = "history-day-head";
    dayHeader.append(textBlock("strong", `${group.date} · ${group.records.length} 条记录`));
    dayGroup.append(dayHeader);

    const dayRecords = document.createElement("div");
    dayRecords.className = "history-day-records";
    group.records.forEach((record) => {
      dayRecords.append(historyEntry(record));
    });
    dayGroup.append(dayRecords);

    elements.historyList.append(dayGroup);
  });
}

function groupHistoryRecordsByDate(records) {
  const groups = [];
  const byDate = new Map();
  records.forEach((record) => {
    const date = record.daily_goal?.goal_date || "-";
    if (!byDate.has(date)) {
      const group = { date, records: [] };
      byDate.set(date, group);
      groups.push(group);
    }
    byDate.get(date).records.push(record);
  });
  return groups;
}

function historyEntry(record) {
  const goal = record.goal_output || {};
  const checkin = record.daily_checkin;
  const entry = document.createElement("article");
  entry.className = "history-entry";

  const header = document.createElement("div");
  header.className = "history-head";
  header.append(textBlock("strong", record.project?.name || "项目"));
  header.append(textBlock("span", `v${record.active_version?.version_no || 1}`));
  entry.append(header);

  const title = document.createElement("p");
  title.className = "history-goal";
  title.textContent = goal.main_goal || record.active_version?.main_goal || "没有目标内容。";
  entry.append(title);

  const feedback = record.feedback_messages || [];
  if (feedback.length) {
    const note = document.createElement("p");
    note.className = "muted compact";
    note.textContent = `反馈 ${feedback.length} 条`;
    entry.append(note);
  }

  if (checkin && record.checkin_editable) {
    entry.append(historyCheckinForm(record, checkin));
  } else if (checkin) {
    entry.append(historyCheckinSummary(record, checkin));
  } else {
    const empty = document.createElement("p");
    empty.className = "muted compact";
    empty.textContent = "未 check-in";
    entry.append(empty);
  }

  return entry;
}

function historyCheckinForm(record, checkin) {
  const form = document.createElement("form");
  form.className = "history-checkin";
  form.dataset.date = record.daily_goal?.goal_date || "";
  form.dataset.goalId = record.daily_goal?.id || "";

  const completion = document.createElement("textarea");
  completion.name = "completion_text";
  completion.rows = 3;
  completion.value = checkin.completion_text || "";
  completion.setAttribute("aria-label", "完成说明（可空）");
  const completionLabel = document.createElement("label");
  completionLabel.className = "checkin-text-field";
  completionLabel.append(textBlock("span", "完成说明（可空）"), completion);

  const row = document.createElement("div");
  row.className = "history-edit-row";

  const status = document.createElement("select");
  status.name = "completion_status";
  status.setAttribute("aria-label", "完成状态");
  [
    ["completed", "完成"],
    ["incomplete", "未完成"],
  ].forEach(([value, label]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    option.selected = (checkin.completion_status || "completed") === value;
    status.append(option);
  });

  const difficulty = document.createElement("select");
  difficulty.name = "felt_difficulty";
  difficulty.setAttribute("aria-label", "主观难度");
  [1, 2, 3, 4, 5].forEach((value) => {
    const option = document.createElement("option");
    option.value = String(value);
    option.textContent = `难度 ${value}`;
    option.selected = Number(checkin.felt_difficulty) === value;
    difficulty.append(option);
  });

  const tomorrow = document.createElement("textarea");
  tomorrow.name = "tomorrow_direction";
  tomorrow.rows = 2;
  tomorrow.value = checkin.tomorrow_direction || "";
  tomorrow.setAttribute("aria-label", "明天方向（可空）");
  const tomorrowLabel = document.createElement("label");
  tomorrowLabel.className = "checkin-text-field";
  tomorrowLabel.append(textBlock("span", "明天方向（可空）"), tomorrow);

  const button = document.createElement("button");
  button.className = "text-button secondary";
  button.type = "submit";
  button.textContent = "保存修改";

  row.append(status, difficulty, button);
  form.append(completionLabel, tomorrowLabel, row);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = checkinBodyFromForm(form, {
      date: form.dataset.date,
      goalId: form.dataset.goalId,
      completionField: completion,
      tomorrowField: tomorrow,
    });
    if (body) {
      await saveCheckin(body, button);
    }
  });
  return form;
}

function historyCheckinSummary(record, checkin) {
  const summary = document.createElement("div");
  summary.className = "history-checkin-summary";

  const reason = document.createElement("p");
  reason.className = "muted compact";
  reason.textContent = record.checkin_edit_lock_reason || "已过提交当天，仅展示最新可用版本。";

  const meta = document.createElement("dl");
  meta.className = "history-checkin-meta";
  appendMeta(meta, "完成状态", checkin.completion_status === "completed" ? "完成" : "未完成");
  appendMeta(meta, "主观难度", `难度 ${numberOrDash(checkin.felt_difficulty)}`);
  appendMeta(meta, "完成说明", textOrDash(checkin.completion_text));
  appendMeta(meta, "明天方向", textOrDash(checkin.tomorrow_direction));

  summary.append(reason, meta);
  return summary;
}

function appendMeta(container, label, value) {
  const term = document.createElement("dt");
  term.textContent = label;
  const detail = document.createElement("dd");
  detail.textContent = value;
  container.append(term, detail);
}

function syncTodayCheckin(records) {
  if (!currentApiDate) {
    return;
  }
  const todayRecords = records.filter((record) => record.daily_goal?.goal_date === currentApiDate);
  const byGoalId = new Map(
    todayRecords.map((record) => [Number(record.daily_goal?.id), record]),
  );
  todayRecords.forEach((record) => {
    const goalId = Number(record.daily_goal?.id);
    if (Number.isFinite(goalId) && (record.daily_checkin || record.daily_goal?.status === "checked_in")) {
      checkedInGoalIds.add(goalId);
    }
  });
  if (currentGoalRecords.length) {
    currentGoalRecords = currentGoalRecords.map((goalRecord) => {
      const historyRecord = byGoalId.get(goalIdFromRecord(goalRecord));
      if (!historyRecord) {
        return goalRecord;
      }
      return {
        ...goalRecord,
        daily_goal: {
          ...(goalRecord.daily_goal || {}),
          ...(historyRecord.daily_goal || {}),
        },
        daily_checkin: historyRecord.daily_checkin || goalRecord.daily_checkin || null,
      };
    });
    renderVisibleTodayGoalCards(currentApiDate);
  }
  canGenerateWeeklyReport = Boolean(
    todayRecords.length &&
      todayRecords.every((record) => record.daily_checkin) &&
      todayRecords.some((record) => record.daily_goal?.weekday === 5),
  );
  updateWeeklyGenerateButton();
}

function renderLatestWeekly(reports) {
  latestWeeklyBundle = reports[0] || null;
  if (!latestWeeklyBundle) {
    renderWeeklyEmpty();
    return;
  }
  renderWeeklyReport(latestWeeklyBundle);
}

function renderWeeklyReport(bundle) {
  const report = bundle.weekly_report || {};
  const output = bundle.report_output || {};
  latestWeeklyBundle = bundle;
  currentWeekId = currentWeekId || report.week_id || null;
  elements.weeklyEmpty.hidden = true;
  elements.weeklyContent.hidden = false;
  elements.weeklyWeek.textContent = report.week_id || "-";
  elements.weeklyUpdated.textContent = report.updated_at ? `更新于 ${report.updated_at}` : "";
  renderList(elements.weeklyCompleted, output.completed_work, "暂无完成记录。");
  renderList(elements.weeklyNextPlan, output.next_week_plan, "暂无下周计划。");
  renderList(elements.weeklyReflection, output.weekly_reflection, "暂无复盘。");
  renderWeeklyVersions(bundle.versions || []);
  setWeeklyFeedbackEnabled(true);
  showFallbackIfAny([report.source_snapshot]);
}

function renderWeeklyEmpty() {
  latestWeeklyBundle = null;
  elements.weeklyEmpty.hidden = false;
  elements.weeklyContent.hidden = true;
  renderList(elements.weeklyCompleted, []);
  renderList(elements.weeklyNextPlan, []);
  renderList(elements.weeklyReflection, []);
  renderWeeklyVersions([]);
  setWeeklyFeedbackEnabled(false);
}

async function handleWeeklyReportGenerate() {
  const weekId = currentWeekId || currentGoalRecord?.daily_goal?.week_id;
  if (!weekId) {
    showAlert("还没有可生成周报的 week_id。");
    return;
  }

  setBusy(elements.weeklyGenerate, true);
  try {
    const payload = await requestJson("/api/weekly-report/generate", {
      method: "POST",
      body: { week_id: weekId },
    });
    renderWeeklyReport({
      weekly_report: payload.weekly_report,
      report_output: payload.report_output,
      versions: payload.weekly_report_versions,
      weekly_focus: payload.weekly_focus,
    });
    await loadHistory();
  } catch (error) {
    showAlert(errorMessage(error));
  } finally {
    setBusy(elements.weeklyGenerate, false);
    updateWeeklyGenerateButton();
  }
}

async function handleWeeklyFeedbackSubmit(event) {
  event.preventDefault();
  const weekId = latestWeeklyBundle?.weekly_report?.week_id;
  const message = elements.weeklyFeedbackMessage.value.trim();
  if (!weekId) {
    showAlert("还没有可修改的周报。");
    return;
  }
  if (!message) {
    showAlert("周报修改意见不能为空。");
    elements.weeklyFeedbackMessage.focus();
    return;
  }

  setBusy(elements.weeklyFeedbackSubmit, true);
  try {
    const payload = await requestJson("/api/weekly-report/feedback", {
      method: "POST",
      body: { week_id: weekId, message },
    });
    elements.weeklyFeedbackMessage.value = "";
    renderWeeklyReport({
      weekly_report: payload.weekly_report,
      report_output: payload.report_output,
      versions: payload.weekly_report_versions,
      weekly_focus: payload.weekly_focus,
    });
    await loadHistory();
  } catch (error) {
    showAlert(errorMessage(error));
  } finally {
    setBusy(elements.weeklyFeedbackSubmit, false);
  }
}

function renderWeeklyVersions(versions) {
  elements.weeklyVersions.replaceChildren();
  if (!versions.length) {
    elements.weeklyVersions.append(emptyBlock("暂无版本。"));
    return;
  }
  versions
    .slice()
    .reverse()
    .forEach((version) => {
      const item = document.createElement("details");
      item.className = "version-item";
      const summary = document.createElement("summary");
      summary.textContent = `v${version.version_no} · ${version.revision_source || "unknown"}`;
      item.append(summary);
      if (version.feedback_message) {
        const feedback = document.createElement("p");
        feedback.className = "muted compact";
        feedback.textContent = version.feedback_message;
        item.append(feedback);
      }
      const output = version.report_output || {};
      item.append(versionSection("本周完成", output.completed_work));
      item.append(versionSection("下周计划", output.next_week_plan));
      elements.weeklyVersions.append(item);
    });
}

function startNewCareerSession() {
  currentCareerSessionId = null;
  renderCareerMessages([]);
  renderCareerRecommendations([]);
  renderCareerSuggestions([]);
  elements.careerMessage.value = "";
  elements.careerAvailableMinutes.value = "";
  elements.careerMessage.focus();
}

async function ensureCareerLoaded() {
  if (careerLoaded) {
    return;
  }
  careerLoaded = true;
  await loadCareerSessions();
}

async function loadCareerSessions() {
  try {
    const payload = await requestJson("/api/career-chat/sessions");
    renderCareerSessions(payload.sessions || []);
    if (!currentCareerSessionId && payload.sessions?.length) {
      await loadCareerHistory(payload.sessions[0].id);
    } else if (!payload.sessions?.length) {
      renderCareerMessages([]);
      renderCareerRecommendations([]);
      renderCareerSuggestions([]);
    }
  } catch (error) {
    showAlert(errorMessage(error));
  }
}

async function loadCareerHistory(sessionId) {
  if (!sessionId) {
    return;
  }
  try {
    const payload = await requestJson(`/api/career-chat/history?session_id=${encodeURIComponent(sessionId)}`);
    currentCareerSessionId = payload.session?.id || sessionId;
    updateCareerSessionSelection();
    renderCareerMessages(payload.messages || []);
    const latestAssistant = [...(payload.messages || [])]
      .reverse()
      .find((message) => message.role === "assistant");
    renderCareerRecommendations(latestAssistant?.recommendations || []);
    renderCareerSuggestions(payload.pending_profile_update_suggestions || []);
  } catch (error) {
    showAlert(errorMessage(error));
  }
}

function updateCareerSessionSelection() {
  elements.careerSessionList.querySelectorAll(".career-session-button").forEach((button) => {
    button.classList.toggle("active", Number(button.dataset.sessionId) === Number(currentCareerSessionId));
  });
}

async function handleCareerChatSubmit(event) {
  event.preventDefault();
  const message = elements.careerMessage.value.trim();
  if (!message) {
    showAlert("职业规划问题不能为空。");
    elements.careerMessage.focus();
    return;
  }
  const availableMinutes = Number(elements.careerAvailableMinutes.value);
  const body = { message, session_id: currentCareerSessionId };
  if (Number.isFinite(availableMinutes) && availableMinutes > 0) {
    body.available_minutes = availableMinutes;
  }

  setBusy(elements.careerSubmit, true);
  try {
    const payload = await requestJson("/api/career-chat", {
      method: "POST",
      body,
    });
    currentCareerSessionId = payload.session_id;
    elements.careerMessage.value = "";
    renderCareerRecommendations(payload.recommendations || []);
    renderCareerSuggestions(payload.profile_update_suggestions || []);
    await loadCareerHistory(currentCareerSessionId);
    await loadCareerSessions();
  } catch (error) {
    showAlert(errorMessage(error));
  } finally {
    setBusy(elements.careerSubmit, false);
  }
}

function renderCareerSessions(sessions) {
  elements.careerSessionList.replaceChildren();
  if (!sessions.length) {
    elements.careerSessionList.append(emptyBlock("还没有职业规划会话。"));
    return;
  }
  sessions.forEach((session) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "career-session-button";
    button.dataset.sessionId = session.id;
    button.classList.toggle("active", Number(session.id) === Number(currentCareerSessionId));
    button.append(textBlock("strong", session.title || `会话 ${session.id}`));
    button.append(textBlock("span", session.updated_at || session.created_at || ""));
    button.addEventListener("click", () => loadCareerHistory(session.id));
    elements.careerSessionList.append(button);
  });
}

function renderCareerMessages(messages) {
  elements.careerMessageList.replaceChildren();
  if (!messages.length) {
    elements.careerMessageList.append(emptyBlock("可以直接描述你的技能、性格、发展意愿或当前困惑。"));
    return;
  }
  messages.forEach((message) => {
    const item = document.createElement("article");
    item.className = `career-message ${message.role === "assistant" ? "assistant" : "user"}`;
    item.append(textBlock("strong", message.role === "assistant" ? "DayPilot" : "你"));
    item.append(textBlock("p", message.content || ""));
    elements.careerMessageList.append(item);
  });
  elements.careerMessageList.scrollTop = elements.careerMessageList.scrollHeight;
}

function renderCareerRecommendations(recommendations) {
  elements.careerRecommendations.replaceChildren();
  const normalized = Array.isArray(recommendations) ? recommendations : [];
  elements.careerResults.hidden = !normalized.length;
  normalized.forEach((recommendation) => {
    const card = document.createElement("article");
    card.className = "career-recommendation-card";
    card.append(textBlock("h4", recommendation.title || "成长项目"));
    card.append(careerMetaBlock("为什么适合", recommendation.why_it_fits));
    const skills = document.createElement("ul");
    renderList(skills, recommendation.skills_to_build || [], "暂未列出技能。");
    const skillSection = document.createElement("section");
    skillSection.append(textBlock("h5", "要提升的技能"), skills);
    card.append(skillSection);
    card.append(careerMetaBlock("预计时间", recommendation.estimated_time));
    card.append(careerMetaBlock("可交付物", recommendation.deliverable));
    card.append(careerMetaBlock("第一步", recommendation.first_step));
    card.append(careerMetaBlock("风险", recommendation.risks));
    card.append(careerMetaBlock("不建议现在做的理由", recommendation.not_now_reason));
    elements.careerRecommendations.append(card);
  });
}

function renderCareerSuggestions(suggestions) {
  elements.careerSuggestionList.replaceChildren();
  const pending = (Array.isArray(suggestions) ? suggestions : []).filter(
    (suggestion) => suggestion.status === "pending",
  );
  elements.careerProfileSuggestions.hidden = !pending.length;
  pending.forEach((suggestion) => {
    const card = document.createElement("article");
    card.className = "career-suggestion-card";
    card.append(textBlock("h4", careerCategoryLabel(suggestion.category)));
    const list = document.createElement("ul");
    renderList(list, suggestion.items || [], "暂未列出。");
    card.append(list);
    card.append(careerMetaBlock("依据", suggestion.evidence));
    card.append(careerMetaBlock("保存原因", suggestion.reason));
    const actions = document.createElement("div");
    actions.className = "career-suggestion-actions";
    const applyButton = document.createElement("button");
    applyButton.className = "text-button";
    applyButton.type = "button";
    applyButton.textContent = "确认保存";
    applyButton.addEventListener("click", () => handleCareerSuggestionDecision(suggestion.id, "apply", applyButton));
    const dismissButton = document.createElement("button");
    dismissButton.className = "text-button secondary";
    dismissButton.type = "button";
    dismissButton.textContent = "忽略";
    dismissButton.addEventListener("click", () =>
      handleCareerSuggestionDecision(suggestion.id, "dismiss", dismissButton),
    );
    actions.append(applyButton, dismissButton);
    card.append(actions);
    elements.careerSuggestionList.append(card);
  });
}

async function handleCareerSuggestionDecision(suggestionId, decision, button) {
  setBusy(button, true);
  try {
    const payload = await requestJson("/api/career-chat/profile-suggestion", {
      method: "POST",
      body: { suggestion_id: suggestionId, decision },
    });
    if (payload.soul_sync_error) {
      showAlert(`画像已保存到数据库，但 SOUL.md 同步失败：${payload.soul_sync_error}`);
    }
    await loadCareerHistory(currentCareerSessionId);
  } catch (error) {
    showAlert(errorMessage(error));
  } finally {
    setBusy(button, false);
  }
}

function careerMetaBlock(label, value) {
  const block = document.createElement("section");
  block.className = "career-meta-block";
  block.append(textBlock("h5", label));
  block.append(textBlock("p", textOrDash(value)));
  return block;
}

function careerCategoryLabel(category) {
  return {
    current_skills: "当前技能点",
    personality_and_work_style: "性格与工作方式",
    development_intentions: "发展意愿",
    career_values_and_constraints: "职业价值观与约束",
  }[category] || "画像信息";
}

function checkinBodyFromForm(form, options) {
  const completion = options.completionField.value.trim();
  const data = new FormData(form);
  const goalId = Number(options.goalId);
  return {
    date: options.date,
    goal_id: goalId,
    completion_status:
      data.get("completion_status") || data.get(`completion_status_${goalId}`) || "completed",
    completion_text: completion,
    felt_difficulty: Number(data.get("felt_difficulty") || data.get(`felt_difficulty_${goalId}`) || 3),
    tomorrow_direction: options.tomorrowField.value.trim(),
  };
}

async function requestJson(path, options = {}) {
  const init = {
    method: options.method || "GET",
    headers: { Accept: "application/json" },
  };
  if (options.body) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }

  const response = await fetch(`${API_BASE}${path}`, init);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || payload.error || `请求失败：${response.status}`);
  }
  return payload;
}

function switchView(viewId) {
  elements.views.forEach((view) => {
    const active = view.id === viewId;
    view.hidden = !active;
    view.classList.toggle("active", active);
  });
  elements.tabs.forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.view === viewId);
  });
  if (viewId === "career-view") {
    ensureCareerLoaded();
  }
}

function setTodayFormsEnabled(enabled) {
  setFormEnabled(elements.feedbackForm, enabled);
  setFormEnabled(elements.checkinForm, enabled);
}

function setWeeklyFeedbackEnabled(enabled) {
  setFormEnabled(elements.weeklyFeedbackForm, enabled);
  elements.weeklyFeedbackSubmit.disabled = !enabled;
}

function setFormEnabled(form, enabled) {
  Array.from(form.elements).forEach((element) => {
    element.disabled = !enabled;
  });
}

function setBusy(button, busy) {
  button.disabled = busy;
  button.classList.toggle("busy", busy);
}

function updateWeeklyGenerateButton() {
  elements.weeklyGenerate.disabled = !canGenerateWeeklyReport;
}

function showAlert(message) {
  elements.alertMessage.textContent = message;
  elements.alert.hidden = false;
}

function hideAlert() {
  elements.alert.hidden = true;
  elements.alertMessage.textContent = "";
}

function showFallbackIfAny(sources) {
  const reason = sources
    .flatMap((source) => fallbackReasons(source))
    .find(Boolean);
  if (reason) {
    showAlert(`已回退到本地 mock：${reason}`);
  } else {
    hideAlert();
  }
}

function fallbackReasons(source) {
  if (!source || typeof source !== "object") {
    return [];
  }
  const metadata = source.llm_metadata || source;
  const reasons = [];
  if (metadata.fallback_reason) {
    reasons.push(String(metadata.fallback_reason));
  }
  if (source.critic_result?.llm_metadata?.fallback_reason) {
    reasons.push(String(source.critic_result.llm_metadata.fallback_reason));
  }
  return reasons;
}

function renderList(container, items, emptyText = "-") {
  container.replaceChildren();
  const normalized = Array.isArray(items) ? items.filter(Boolean) : [];
  if (!normalized.length) {
    const empty = document.createElement("li");
    empty.textContent = emptyText;
    container.append(empty);
    return;
  }
  normalized.forEach((item) => {
    const element = document.createElement("li");
    element.textContent = item;
    container.append(element);
  });
}

function versionSection(title, items) {
  const section = document.createElement("section");
  const heading = document.createElement("h4");
  const list = document.createElement("ul");
  heading.textContent = title;
  renderList(list, items, "暂无内容。");
  section.append(heading, list);
  return section;
}

function emptyBlock(text) {
  const element = document.createElement("p");
  element.className = "muted";
  element.textContent = text;
  return element;
}

function textBlock(tag, text) {
  const element = document.createElement(tag);
  element.textContent = text;
  return element;
}

function textOrDash(value) {
  const text = String(value || "").trim();
  return text || "-";
}

function displayGoalTitle(value) {
  const text = textOrDash(value);
  const cleaned = text
    .replace(/继续完成「[^」]+」未完成目标[：:]\s*/u, "")
    .replace(/继续完成『[^』]+』未完成目标[：:]\s*/u, "")
    .replace(/继续完成"[^"]+"未完成目标[：:]\s*/u, "")
    .replace(/继续完成“[^”]+”未完成目标[：:]\s*/u, "")
    .replace(/继续完成未完成目标[：:]\s*/u, "")
    .replace(/^(缩小范围|交付明确成果)[：:]\s*/u, "")
    .trim();
  return cleaned || text;
}

function numberOrDash(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : "-";
}

function errorMessage(error) {
  return error instanceof Error ? error.message : "未知错误";
}
