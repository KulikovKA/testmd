# Task decomposition

Help the user decompose a stated task into a small, useful hierarchy.

1. Understand the task and any supplied context. If a referenced task must be
   inspected, use `get_task` only when that capability is available.
2. Propose a structured decomposition before creating or changing anything.
   State assumptions, ordering, and the records that would be created.
3. Incorporate additional context and revise the proposal when the user changes
   requirements. A revised proposal replaces the previous unconfirmed proposal.
4. Ask for explicit confirmation of the current proposal. Words such as
   "looks good" are not confirmation unless the user clearly authorizes creation.
5. Only after confirmation, call the discovered and allowed Task tools needed for
   the confirmed plan. A text-only simulation is not completion. Return each
   created task or subtask record to the user exactly as the tools returned it.

Never call a Task mutation tool before explicit confirmation. Never invent a
tool name, URL, HTTP method, headers, IDs, or a capability that was not made
available. The Skill is instruction only: the runtime tool policy is the source
of authorization.

If the user cancels, stop without writing. If requirements change after
confirmation but before a write, return to proposal and request confirmation
again. If a write partially fails, report the records already returned by the
tool, do not retry blindly, and ask the user whether to inspect or continue.
If a response is invalid or a duplicate is possible, do not guess; report the
safe failure and request clarification or an inspection with an available tool.
