import assert from 'node:assert/strict'
import test from 'node:test'

import { useScheduleForm } from '../src/composables/useScheduleForm.ts'

test('default trade frequency is every 30 minutes with 8 daily runs and disabled by default', () => {
  const { buildPayload, getDailyEstimatedRuns } = useScheduleForm()

  const payload = buildPayload([])
  const morningRun = payload.find((item) => item.name === '上午运行')
  const afternoonRun = payload.find((item) => item.name === '下午运行')

  assert.equal(morningRun?.cron_expression, 'trade-window:09:30-11:30/30m')
  assert.equal(afternoonRun?.cron_expression, 'trade-window:13:00-15:00/30m')
  assert.equal(morningRun?.enabled, false)
  assert.equal(afternoonRun?.enabled, false)
  assert.equal(getDailyEstimatedRuns(), 8)
})

test('getMorningRunSummary supports second-level frequencies', () => {
  const { scheduleSettings, getMorningRunSummary } = useScheduleForm()

  scheduleSettings.morning.intervalValue = 30
  scheduleSettings.morning.intervalUnit = 'seconds'

  assert.equal(getMorningRunSummary(), '09:30 开始，每30秒执行一次，11:30 前结束')
})

test('buildPayload creates single trade window schedule for each session', () => {
  const { scheduleSettings, buildPayload } = useScheduleForm()

  scheduleSettings.morning.intervalValue = 30
  scheduleSettings.morning.intervalUnit = 'seconds'
  scheduleSettings.afternoon.intervalValue = 1
  scheduleSettings.afternoon.intervalUnit = 'minutes'

  const payload = buildPayload([])
  const morningRuns = payload.filter((item) => item.name.startsWith('上午运行'))
  const afternoonRuns = payload.filter((item) => item.name.startsWith('下午运行'))

  assert.equal(morningRuns.length, 1)
  assert.equal(afternoonRuns.length, 1)
  assert.equal(morningRuns.every((item) => item.run_type === 'trade'), true)
  assert.equal(morningRuns[0]?.cron_expression, 'trade-window:09:30-11:30/30s')
  assert.equal(afternoonRuns[0]?.cron_expression, 'trade-window:13:00-15:00/1m')
})

test('buildPayload marks fixed tasks as analysis', () => {
  const { buildPayload } = useScheduleForm()

  const payload = buildPayload([])
  const fixedRuns = payload.filter((item) => ['盘前分析', '午间复盘', '收盘分析'].includes(item.name))

  assert.equal(fixedRuns.length, 3)
  assert.equal(fixedRuns.every((item) => item.run_type === 'analysis'), true)
})

test('buildPayload preserves disabled session schedules', () => {
  const { scheduleSettings, syncFromSchedules, buildPayload } = useScheduleForm()

  syncFromSchedules([
    {
      id: 11,
      name: '上午运行',
      cron_expression: 'trade-window:09:30-11:30/30m',
      task_prompt: 'session',
      timeout_seconds: 1800,
      enabled: false,
    },
  ])

  const payload = buildPayload([
    {
      id: 11,
      name: '上午运行',
      cron_expression: 'trade-window:09:30-11:30/30m',
      task_prompt: 'session',
      timeout_seconds: 1800,
      enabled: false,
    },
  ])

  const morningRuns = payload.filter((item) => item.name.startsWith('上午运行'))
  assert.equal(morningRuns.length, 1)
  assert.equal(morningRuns.every((item) => item.enabled === false), true)
})

test('syncFromSchedules reads trade window frequency expression', () => {
  const { scheduleSettings, syncFromSchedules } = useScheduleForm()

  syncFromSchedules([
    {
      id: 20,
      name: '下午运行',
      cron_expression: 'trade-window:13:00-15:00/30s',
      task_prompt: 'session',
      timeout_seconds: 1800,
      enabled: true,
    },
  ])

  assert.equal(scheduleSettings.afternoon.intervalValue, 30)
  assert.equal(scheduleSettings.afternoon.intervalUnit, 'seconds')
})

test('syncFromSchedules migrates legacy session schedules into inferred minute frequency', () => {
  const { scheduleSettings, syncFromSchedules } = useScheduleForm()

  syncFromSchedules([
    {
      id: 31,
      name: '上午运行1号',
      cron_expression: '0 10 * * 1-5',
      task_prompt: 'legacy',
      timeout_seconds: 1800,
      enabled: true,
    },
    {
      id: 32,
      name: '上午运行2号',
      cron_expression: '0 11 * * 1-5',
      task_prompt: 'legacy',
      timeout_seconds: 1800,
      enabled: true,
    },
  ])

  assert.equal(scheduleSettings.morning.intervalValue, 60)
  assert.equal(scheduleSettings.morning.intervalUnit, 'minutes')
})

test('syncFromSchedules normalizes pre-market times to supported button options', () => {
  const { scheduleSettings, syncFromSchedules } = useScheduleForm()

  syncFromSchedules([
    {
      id: 1,
      name: '盘前分析',
      cron_expression: '30 7 * * 1-5',
      task_prompt: 'a',
      timeout_seconds: 1800,
      enabled: true,
    },
  ])

  assert.equal(scheduleSettings.preMarket.hour, 8)
  assert.equal(scheduleSettings.preMarket.minute, 0)
})

test('pre-market default display time is 08:00', () => {
  const { scheduleSettings } = useScheduleForm()

  assert.equal(scheduleSettings.preMarket.hour, 8)
  assert.equal(scheduleSettings.preMarket.minute, 0)
})

test('syncFromSchedules migrates legacy pre-market default 07:15 to 08:00', () => {
  const { scheduleSettings, syncFromSchedules } = useScheduleForm()

  syncFromSchedules([
    {
      id: 9,
      name: '盘前分析',
      cron_expression: '15 7 * * 1-5',
      task_prompt: 'legacy',
      timeout_seconds: 1800,
      enabled: true,
    },
  ])

  assert.equal(scheduleSettings.preMarket.hour, 8)
  assert.equal(scheduleSettings.preMarket.minute, 0)
})

test('syncFromSchedules normalizes midday times to 12:00/15/30/45 options', () => {
  const { scheduleSettings, syncFromSchedules } = useScheduleForm()

  syncFromSchedules([
    {
      id: 2,
      name: '午间复盘',
      cron_expression: '45 11 * * 1-5',
      task_prompt: 'b',
      timeout_seconds: 1800,
      enabled: true,
    },
  ])

  assert.equal(scheduleSettings.midday.hour, 12)
  assert.equal(scheduleSettings.midday.minute, 0)
})

test('syncFromSchedules normalizes post-market times to supported button options', () => {
  const { scheduleSettings, syncFromSchedules } = useScheduleForm()

  syncFromSchedules([
    {
      id: 3,
      name: '收盘分析',
      cron_expression: '15 16 * * 1-5',
      task_prompt: 'c',
      timeout_seconds: 1800,
      enabled: true,
    },
  ])

  assert.equal(scheduleSettings.postMarket.hour, 16)
  assert.equal(scheduleSettings.postMarket.minute, 0)
})
