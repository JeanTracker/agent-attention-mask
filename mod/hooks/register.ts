import type { Register } from 'claude-code'

// An interrupted turn fires no Claude Code hook -- not Stop, not anything --
// so the runner kept the rain up until HOOK_STALL ran out (D-036). A mod sees
// the turn end either way; this hands that one fact to the runner's FIFO
// through the same emitter the hooks use (D-056).
//
// amask passes this folder with --plugin-dir and sets the three variables.
// Run any other way they are unset and this does nothing.
//
// Inline rather than through a helper: before 2.1.260 the engine refuses a
// module that hands $ to a function of its own.
export const register: Register = on => {
  on('turn.complete', async ($, e, next) => {
    const result = await next(e)
    // A subagent's turn ending says nothing about the main thread (D-033),
    // and a finished turn already has its Stop.
    if (e.agentId === undefined && e.reason === 'aborted') {
      const emitter = await $.env.get('AMASK_HOOK_EMITTER')
      const fifo = await $.env.get('AMASK_HOOK_FIFO')
      const token = await $.env.get('AMASK_HOOK_TOKEN')
      if (emitter && fifo && token) {
        try {
          await $.process.run([emitter, fifo, 'TurnAborted', token], { stdin: '{}', timeoutMs: 5000 })
        } catch {
          // The runner may be gone; that is not the agent's problem (P-101).
        }
      }
    }
    return result
  })
}
