# Code testing

Derive tests from observable behavior and risk. Identify the focused unit,
integration, and regression coverage that is necessary for the approved change.
Cover normal behavior, edge cases, and meaningful failure paths; prefer
deterministic inputs and assertions over tests that merely reproduce private
implementation details.

Explain the purpose of each recommended test and the boundary it protects. Reuse
existing test conventions and fixtures where appropriate, and avoid claiming a
test proves behavior outside its controlled environment.

Always distinguish a **proposed test** from a **test actually executed**. This
Skill grants no repository, file-editing, shell, runtime, or network authority.
When test execution is unavailable, report the tests that should be run rather
than asserting that they passed.
