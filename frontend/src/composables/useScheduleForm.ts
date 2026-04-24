import { reactive } from 'vue'

import type { ScheduleConfig } from '@/types'

type ScheduleLike = Pick<ScheduleConfig, 'id' | 'name' | 'run_type' | 'cron_expression' | 'task_prompt' | 'timeout_seconds' | 'enabled'>

type ScheduleKey = 'preMarket' | 'midday' | 'postMarket'
type SessionKey = 'morning' | 'afternoon'
type IntervalUnit = 'seconds' | 'minutes'

type FixedTaskTimeOption = {
  hour: number
  minute: number
  label: string
}

export const FIXED_TASK_TIME_OPTIONS = {
  preMarket: {
    options: [
      { hour: 8, minute: 0, label: '08:00' },
      { hour: 8, minute: 15, label: '08:15' },
      { hour: 8, minute: 30, label: '08:30' },
      { hour: 8, minute: 45, label: '08:45' },
    ] as FixedTaskTimeOption[],
  },
  midday: {
    options: [
      { hour: 12, minute: 0, label: '12:00' },
      { hour: 12, minute: 15, label: '12:15' },
      { hour: 12, minute: 30, label: '12:30' },
      { hour: 12, minute: 45, label: '12:45' },
    ] as FixedTaskTimeOption[],
  },
  postMarket: {
    options: [
      { hour: 15, minute: 15, label: '15:15' },
      { hour: 15, minute: 30, label: '15:30' },
      { hour: 15, minute: 45, label: '15:45' },
      { hour: 16, minute: 0, label: '16:00' },
    ] as FixedTaskTimeOption[],
  },
} as const

export const SESSION_INTERVAL_UNIT_OPTIONS = [
  { value: 'seconds', label: '秒' },
  { value: 'minutes', label: '分钟' },
] as const

export interface ScheduleFormState {
  preMarket: { enabled: boolean; hour: number; minute: number; prompt: string }
  postMarket: { enabled: boolean; hour: number; minute: number; prompt: string }
  midday: { enabled: boolean; hour: number; minute: number; prompt: string }
  morning: { enabled: boolean; intervalValue: number; intervalUnit: IntervalUnit; prompt: string }
  afternoon: { enabled: boolean; intervalValue: number; intervalUnit: IntervalUnit; prompt: string }
}

const FIXED_TASK_NAMES = {
  preMarket: '盘前分析',
  midday: '午间复盘',
  postMarket: '收盘分析',
} as const

const SESSION_TASK_NAMES = {
  morning: '上午运行',
  afternoon: '下午运行',
} as const

const SESSION_WINDOWS = {
  morning: { startHour: 9, startMinute: 30, endHour: 11, endMinute: 30 },
  afternoon: { startHour: 13, startMinute: 0, endHour: 15, endMinute: 0 },
} as const

const LEGACY_SESSION_INTERVAL_MINUTES = {
  1: 120,
  2: 60,
  3: 45,
  4: 30,
} as const

const DEFAULT_TIMEOUT = 1800
const TRADE_WINDOW_EXPRESSION_RE = /^trade-window:(\d{2}):(\d{2})-(\d{2}):(\d{2})\/(\d+)(s|m)$/i

function normalizeSectionTime(section: ScheduleKey, hour: number, minute: number) {
  const options = FIXED_TASK_TIME_OPTIONS[section]

  if (section === 'preMarket' && hour === 7 && minute === 15) {
    return {
      hour: 8,
      minute: 0,
    }
  }

  const exactMatch = options.options.find((option) => option.hour === hour && option.minute === minute)
  if (exactMatch) {
    return {
      hour: exactMatch.hour,
      minute: exactMatch.minute,
    }
  }

  const currentMinutes = hour * 60 + minute
  const nearest = options.options.reduce((best, option) => {
    const optionMinutes = option.hour * 60 + option.minute
    const bestMinutes = best.hour * 60 + best.minute
    return Math.abs(optionMinutes - currentMinutes) < Math.abs(bestMinutes - currentMinutes) ? option : best
  }, options.options[0])

  return {
    hour: nearest.hour,
    minute: nearest.minute,
  }
}

const defaultState = (): ScheduleFormState => ({
  preMarket: { enabled: false, hour: 8, minute: 0, prompt: '你正在执行盘前分析任务，请分析今日市场情况和持仓情况，做好今日市场走势预测，为你决策交易做好准备。' },
  postMarket: { enabled: false, hour: 15, minute: 30, prompt: '你正在执行收盘分析任务，请对今日市场和交易操作进行全面复盘，总结今日市场和明日可能的走势。' },
  midday: { enabled: false, hour: 12, minute: 0, prompt: '你正在执行午间复盘任务，请对上午市场和交易操作进行复盘，做好下午市场走势预测，为你决策交易做好准备。' },
  morning: { enabled: true, intervalValue: 60, intervalUnit: 'minutes', prompt: '你正在执行盘中交易操作，你的唯一目标是追求收益最大化。' },
  afternoon: { enabled: true, intervalValue: 60, intervalUnit: 'minutes', prompt: '你正在执行盘中交易操作，你的唯一目标是追求收益最大化。' },
})

function parseCron(cronExpression: string) {
  const [minuteText = '0', hourText = '0'] = cronExpression.split(' ')
  return {
    minute: Number(minuteText),
    hour: Number(hourText),
  }
}

function buildCron(hour: number, minute: number) {
  return `${minute} ${hour} * * 1-5`
}

function formatTime(hour: number, minute: number) {
  return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`
}

function normalizeSessionIntervalValue(intervalValue: number, intervalUnit: IntervalUnit) {
  const rawValue = Number(intervalValue)
  const safeValue = Number.isFinite(rawValue) ? Math.floor(rawValue) : 0
  const minimum = intervalUnit === 'seconds' ? 30 : 1
  return Math.max(minimum, safeValue)
}

function buildTradeWindowExpression(session: SessionKey, intervalValue: number, intervalUnit: IntervalUnit) {
  const normalizedIntervalValue = normalizeSessionIntervalValue(intervalValue, intervalUnit)
  const window = SESSION_WINDOWS[session]
  const unitSuffix = intervalUnit === 'seconds' ? 's' : 'm'
  return `trade-window:${formatTime(window.startHour, window.startMinute)}-${formatTime(window.endHour, window.endMinute)}/${normalizedIntervalValue}${unitSuffix}`
}

function parseTradeWindowExpression(cronExpression: string) {
  const matched = TRADE_WINDOW_EXPRESSION_RE.exec((cronExpression || '').trim())
  if (!matched) {
    return null
  }

  const [, startHourText, startMinuteText, endHourText, endMinuteText, intervalValueText, unitText] = matched

  return {
    startHour: Number(startHourText),
    startMinute: Number(startMinuteText),
    endHour: Number(endHourText),
    endMinute: Number(endMinuteText),
    intervalValue: Number(intervalValueText),
    intervalUnit: unitText.toLowerCase() === 's' ? 'seconds' as const : 'minutes' as const,
  }
}

function inferLegacySessionInterval(schedules: ScheduleLike[]) {
  const matched = schedules
    .map((item) => parseCron(item.cron_expression))
    .sort((a, b) => (a.hour * 60 + a.minute) - (b.hour * 60 + b.minute))

  if (matched.length >= 2) {
    const intervals = matched
      .slice(1)
      .map((item, index) => (item.hour * 60 + item.minute) - (matched[index].hour * 60 + matched[index].minute))
      .filter((value) => value > 0)

    if (intervals.length > 0 && intervals.every((value) => value === intervals[0])) {
      return {
        intervalValue: intervals[0],
        intervalUnit: 'minutes' as const,
      }
    }
  }

  const fallbackMinutes = LEGACY_SESSION_INTERVAL_MINUTES[matched.length as keyof typeof LEGACY_SESSION_INTERVAL_MINUTES] ?? 60
  return {
    intervalValue: fallbackMinutes,
    intervalUnit: 'minutes' as const,
  }
}

function getSessionFrequencyLabel(intervalValue: number, intervalUnit: IntervalUnit) {
  const normalizedIntervalValue = normalizeSessionIntervalValue(intervalValue, intervalUnit)
  return `每${normalizedIntervalValue}${intervalUnit === 'seconds' ? '秒' : '分钟'}`
}

function getSessionSummaryText(session: SessionKey, intervalValue: number, intervalUnit: IntervalUnit) {
  const window = SESSION_WINDOWS[session]
  return `${formatTime(window.startHour, window.startMinute)} 开始，${getSessionFrequencyLabel(intervalValue, intervalUnit)}执行一次，${formatTime(window.endHour, window.endMinute)} 前结束`
}

function getSessionEstimatedRunCount(session: SessionKey, intervalValue: number, intervalUnit: IntervalUnit) {
  const window = SESSION_WINDOWS[session]
  const durationSeconds = ((window.endHour * 60 + window.endMinute) - (window.startHour * 60 + window.startMinute)) * 60
  const normalizedIntervalValue = normalizeSessionIntervalValue(intervalValue, intervalUnit)
  const intervalSeconds = intervalUnit === 'seconds' ? normalizedIntervalValue : normalizedIntervalValue * 60
  return Math.max(1, Math.ceil(durationSeconds / intervalSeconds))
}

export function useScheduleForm() {
  const scheduleSettings = reactive<ScheduleFormState>(defaultState())

  function syncFromSchedules(schedules: ScheduleLike[]) {
    Object.assign(scheduleSettings, defaultState())

    ;(Object.keys(FIXED_TASK_NAMES) as ScheduleKey[]).forEach((key) => {
      const matched = schedules.find((item) => item.name === FIXED_TASK_NAMES[key])
      if (!matched) {
        return
      }

      const { hour, minute } = parseCron(matched.cron_expression)
      const normalizedTime = normalizeSectionTime(key, hour, minute)
      scheduleSettings[key].enabled = matched.enabled
      scheduleSettings[key].hour = normalizedTime.hour
      scheduleSettings[key].minute = normalizedTime.minute
      scheduleSettings[key].prompt = matched.task_prompt || scheduleSettings[key].prompt
    })

    ;(Object.keys(SESSION_TASK_NAMES) as SessionKey[]).forEach((key) => {
      const matched = schedules
        .filter((item) => item.name.startsWith(SESSION_TASK_NAMES[key]))
        .sort((a, b) => a.cron_expression.localeCompare(b.cron_expression))

      if (matched.length === 0) {
        return
      }

      scheduleSettings[key].enabled = matched.some((item) => item.enabled)
      scheduleSettings[key].prompt = matched[0].task_prompt || scheduleSettings[key].prompt

      const parsedTradeWindow = parseTradeWindowExpression(matched[0].cron_expression)
      if (parsedTradeWindow) {
        scheduleSettings[key].intervalValue = normalizeSessionIntervalValue(parsedTradeWindow.intervalValue, parsedTradeWindow.intervalUnit)
        scheduleSettings[key].intervalUnit = parsedTradeWindow.intervalUnit
        return
      }

      const inferredInterval = inferLegacySessionInterval(matched)
      scheduleSettings[key].intervalValue = inferredInterval.intervalValue
      scheduleSettings[key].intervalUnit = inferredInterval.intervalUnit
    })
  }

  function buildPayload(existingSchedules: ScheduleLike[]) {
    const fixedPayload = (Object.keys(FIXED_TASK_NAMES) as ScheduleKey[]).map((key) => {
      const existing = existingSchedules.find((item) => item.name === FIXED_TASK_NAMES[key])
      const current = scheduleSettings[key]
      return {
        id: existing?.id,
        name: FIXED_TASK_NAMES[key],
        run_type: 'analysis' as const,
        cron_expression: buildCron(current.hour, current.minute),
        task_prompt: current.prompt,
        timeout_seconds: existing?.timeout_seconds ?? DEFAULT_TIMEOUT,
        enabled: current.enabled,
      }
    })

    const sessionPayload = (Object.keys(SESSION_TASK_NAMES) as SessionKey[]).map((key) => {
      const current = scheduleSettings[key]
      const existing = existingSchedules.filter((item) => item.name.startsWith(SESSION_TASK_NAMES[key]))
      const normalizedIntervalValue = normalizeSessionIntervalValue(current.intervalValue, current.intervalUnit)
      current.intervalValue = normalizedIntervalValue
      return {
        id: existing[0]?.id,
        name: SESSION_TASK_NAMES[key],
        run_type: 'trade' as const,
        cron_expression: buildTradeWindowExpression(key, normalizedIntervalValue, current.intervalUnit),
        task_prompt: current.prompt,
        timeout_seconds: existing[0]?.timeout_seconds ?? DEFAULT_TIMEOUT,
        enabled: current.enabled,
      }
    })

    return [...fixedPayload, ...sessionPayload]
  }

  function setFixedTaskTime(section: ScheduleKey, option: FixedTaskTimeOption) {
    scheduleSettings[section].hour = option.hour
    scheduleSettings[section].minute = option.minute
  }

  function autoResizeTextarea(event: Event) {
    const textarea = event.target as HTMLTextAreaElement
    textarea.style.height = 'auto'
    textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`
  }

  function normalizeSessionInterval(session: SessionKey) {
    scheduleSettings[session].intervalValue = normalizeSessionIntervalValue(
      scheduleSettings[session].intervalValue,
      scheduleSettings[session].intervalUnit,
    )
  }

  function getMorningRunSummary() {
    return getSessionSummaryText('morning', scheduleSettings.morning.intervalValue, scheduleSettings.morning.intervalUnit)
  }

  function getAfternoonRunSummary() {
    return getSessionSummaryText('afternoon', scheduleSettings.afternoon.intervalValue, scheduleSettings.afternoon.intervalUnit)
  }

  function getMorningEstimatedRuns() {
    return getSessionEstimatedRunCount('morning', scheduleSettings.morning.intervalValue, scheduleSettings.morning.intervalUnit)
  }

  function getAfternoonEstimatedRuns() {
    return getSessionEstimatedRunCount('afternoon', scheduleSettings.afternoon.intervalValue, scheduleSettings.afternoon.intervalUnit)
  }

  return {
    scheduleSettings,
    fixedTaskTimeOptions: FIXED_TASK_TIME_OPTIONS,
    sessionIntervalUnitOptions: SESSION_INTERVAL_UNIT_OPTIONS,
    syncFromSchedules,
    buildPayload,
    setFixedTaskTime,
    autoResizeTextarea,
    normalizeSessionInterval,
    getMorningRunSummary,
    getAfternoonRunSummary,
    getMorningEstimatedRuns,
    getAfternoonEstimatedRuns,
  }
}
