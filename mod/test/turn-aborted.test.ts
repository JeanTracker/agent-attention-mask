import { expect, mock, test } from 'claude-code/testing'

const ENV = {
  AMASK_HOOK_EMITTER: '/clone/bin/amask-hook',
  AMASK_HOOK_FIFO: '/tmp/amask-hooks-1.fifo',
  AMASK_HOOK_TOKEN: 'tok',
}

const RAN = { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false }

function turn(reason: 'answer' | 'aborted' | 'error', agentId?: string) {
  return { answer: '', durationMs: 1, turnId: 't1', isAborted: reason === 'aborted', reason, agentId }
}

test('an interrupted turn reaches the runner', async ($, on) => {
  mock.env(on, ENV)
  const runs: (readonly string[])[] = []
  on('process.run', (_$, e) => { runs.push(e.argv); return RAN })
  on('turn.complete', () => ({ text: '' }))
  await $.turn.complete(turn('aborted'))
  expect(runs).toEqual([['/clone/bin/amask-hook', '/tmp/amask-hooks-1.fifo', 'TurnAborted', 'tok']])
})

test('a finished turn is left to Stop', async ($, on) => {
  mock.env(on, ENV)
  const runs: (readonly string[])[] = []
  on('process.run', (_$, e) => { runs.push(e.argv); return RAN })
  on('turn.complete', () => ({ text: '' }))
  await $.turn.complete(turn('answer'))
  await $.turn.complete(turn('error'))
  expect(runs).toEqual([])
})

test("a subagent's interrupted turn is not the main thread's", async ($, on) => {
  mock.env(on, ENV)
  const runs: (readonly string[])[] = []
  on('process.run', (_$, e) => { runs.push(e.argv); return RAN })
  on('turn.complete', () => ({ text: '' }))
  await $.turn.complete(turn('aborted', 'agent-1'))
  expect(runs).toEqual([])
})

test('outside amask it does nothing', async ($, on) => {
  mock.env(on, {})
  const runs: (readonly string[])[] = []
  on('process.run', (_$, e) => { runs.push(e.argv); return RAN })
  on('turn.complete', () => ({ text: '' }))
  await $.turn.complete(turn('aborted'))
  expect(runs).toEqual([])
})

test('a failing emitter never fails the turn', async ($, on) => {
  mock.env(on, ENV)
  on('process.run', () => { throw new Error('ENOENT') })
  on('turn.complete', () => ({ text: 'done' }))
  const result = await $.turn.complete(turn('aborted'))
  expect(result.text).toBe('done')
})
