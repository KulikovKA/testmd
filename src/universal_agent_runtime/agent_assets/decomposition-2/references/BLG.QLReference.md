## BLG.QLReference - QL reference (subset)

> **Reference** (not a pattern): the supported QL subset for `BLG.TextToQL`. Source:
> the QL manual of Сфера.Задачи; QL expressions and system codes are kept verbatim.

```episteme id="BLG.QLReference" context="BacklogManagement"
UseThisWhen:
  building or checking a QL query for BLG.TextToQL: operators, precedence, and their allowed operand types
  not for QL constructs outside this subset
Precedence:
  query execution priority depends on the keywords (logical operators) that join the query clauses
  and groups clauses (it has priority: it turns them into a single combined clause); or separates clauses
  execution order: category 4 operators first, then category 3, then category 2
  parentheses override the order: operators inside parentheses run first, then those outside
  example: status = 'closed' and area = 'PPTS' or assignee=me() → all closed tasks of space "PPTS" plus all tasks assigned to the current user
  example: (area='PPTS' and status='closed') or assignee=me() → parentheses set the precedence
  note: != compares the current field value with the right operand; an entity whose field is unset is excluded from the selection
  note: to include entities with unset values, combine with = null, e.g. "assignee != 'user_login' or assignee = null"
Operators:
  a QL operator compares the field value on its left with operands on its right so that only true values are selected; not may also negate a whole expression or part of it
  not: category 1; not; logical; example: not(priority='low' or type='task')
  '()': category 1; parentheses; any operand type; example: hasOpenedChildren() or (statusCategory != 'Done' and state = 'Normal')
  or: category 2; logical OR; logical; example: assignee = me() or owner = me()
  and: category 3; logical AND; logical; example: type = 2 and status = 3
  'not in': category 4; logical NOT IN; numeric, string (text), date-time; example: type not in (2, 3, 4)
  in: category 4; in; numeric, string (text), date-time; example: type in (2, 3, 4)
  '~': category 4; like (substring search); string (text); example: name ~'карточки'
  '=': category 4; equal; any; example: state = 'Normal'
  '!=': category 4; not equal; numeric, string (text), date-time; example: statusCategory != 'Done'
  '>': category 4; greater; numeric, date-time; example: spent > 4
  '<': category 4; less; numeric, date-time; example: estimation < 100
  '>=': category 4; greater or equal; numeric, date-time; example: estimation >= 120
  '<=': category 4; less or equal; numeric, date-time; example: spent <= 12
  '= not null': category 4; conditional NOT EMPTY; numeric, string (text), date-time; example: number = not null
  '= null': category 4; conditional EMPTY; numeric, string (text), date-time; example: assignee = null
```

```episteme id="BLG.QLReference.Fields" context="BacklogManagement"
UseThisWhen:
  selecting system or custom task fields and the operators each supports
SystemFields:
  dates format: YYYY-MM-DD
  number: supports =, !=, in, not in, ~, = null; example PPTS-15
  name: supports =, !=, in, not in, ~, = null; example "Research the product card section"
  type: supports =, !=, >, <, >=, <=, in, not in, = null; example epic
  area: supports =, !=, >, <, >=, <=, in, not in, = null; example CRM
  priority: supports =, !=, in, not in, = null, = not null; example high
  parent: supports =, !=, in, not in, = null, = not null; example PPTS-1
  status: supports =, !=, >, <, >=, <=, in, not in, = null; example closed
  statusCategory: supports =, !=, in, not in, ~, = null, = not null; example Done
  assignee: supports =, !=, >, <, >=, <=, in, not in, = null, = not null; value is a login
  owner: supports =, !=, >, <, >=, <=, in, not in, = null; value is a login
  createdBy: supports =, !=, >, <, >=, <=, in, not in, = null; value is a login
  updatedBy: supports =, !=, >, <, >=, <=, in, not in, = null; value is a login
  dueDate: supports =, !=, >, <, >=, <=, in, not in, = null, = not null; example 2022-11-23
  createDate: supports =, !=, >, <, >=, <=, in, not in, = null, = not null; example 2022-11-23
  updateDate: supports =, !=, >, <, >=, <=, in, not in, = null, = not null; example 2022-11-23
  estimation: supports =, !=, >, <, >=, <=, in, not in, = null, = not null; example 5
  spent: supports =, !=, >, <, >=, <=, in, not in, = null, = not null; example 4
  remainder: supports =, !=, >, <, >=, <=, in, not in, = null, = not null; example 3
  workLogRemain: supports =, !=, >, <, >=, <=, in, not in, = null, = not null; example 42
  sprint: supports =, !=, in, not in; example 19
  state: supports =, !=, in, not in, ~, = null; example Normal
  label: supports =, !=, in, not in; example label name
  component: supports =, !=, in, not in; example component name
  projectionTeam: supports =, !=, in, not in, ~, = null, = not null; example team_name
  securityLevel: supports =, !=, in, not in, ~, = null, = not null; example default
TaskTypes:
  epic ("Эпик"); feature ("Фича"); story ("История"); task ("Задача"); defect ("Дефект"); subtask ("Подзадача")
  example: type='story'
  example: type in ('epic', 'feature')
Priorities:
  critical ("Критический"); high ("Высокий"); average ("Средний"); low ("Низкий")
  example: priority='average'
Statuses:
  created ("Создано") → Open; waiting ("В ожидании") → Open
  inProgress ("В работе") → In progress; onTheQueue ("В очереди") → In progress; review ("Подтверждение") → In progress; analysis ("Анализ") → In progress; design ("Проектирование") → In progress; readyForDevelopment ("Готово к разработке") → In progress; development ("Разработка") → In progress; st ("СТ") → In progress; stCompleted ("СТ Завершено") → In progress; ift ("ИТ") → In progress; at ("ПСИ") → In progress; introduction ("Внедрение") → In progress; localization ("Локализация") → In progress; fixing ("Исправление") → In progress; hold ("Отложен") → In progress; testing ("Тестирование") → In progress; verification ("Подтверждение исправления") → In progress
  rejectedByThePerformer ("Отклонен исполнителем") → Done; done ("Выполнено") → Done; closed ("Закрыто") → Done
  example: status='closed'
  example: status in ('st', 'stCompleted', 'ift')
  example: statusCategory='Open'; statusCategory='In progress'; statusCategory='Done'
CustomFieldTypes:
  STRING (string): supports =, !=, in, not in, ~, = not null; field named at creation; example Тест
  TEXT (multiline text): supports =, !=, in, not in, ~, = not null; field named at creation; example Тест
  NUMBER (integer): supports =, !=, >, <, >=, <=, in, not in, = not null; field named at creation; example 15
  DATE (date): supports =, !=, >, <, >=, <=, in, not in, = null, = not null; field named at creation; example 2022-11-23
  DICTIONARY: supports =, !=, in, not in, ~, = not null; field named at creation; example "dictionary value 1"
  BOOLEAN: supports =; field named at creation; example true
  COMPLEX_DICTIONARY: supports =, !=, = null; field named at creation; example "complex dictionary value 1"
  DATE-TIME: supports =, !=, >, <, >=, <=, in, not in, ~, = not null; field named at creation; example 2024-12-26T00:08:51Z, or 2024-12-26 for >,<,>=,<=
  USER: supports =, !=, in, not in, ~, = not null; field named at creation; example user_login
```

```episteme id="BLG.QLReference.Functions" context="BacklogManagement"
UseThisWhen:
  selecting a QL function: its allowed operators, allowed fields, and syntax
Functions:
  me(): operators =, !=; allowed fields assignee, owner, createdBy, updatedBy; finds tasks related to the current user; example: assignee = me()
  now('offset', 'interval'): operators =, !=, >, >=, <, <=; date fields; returns the current date and time; "offset" a string param with the number of calendar days to shift the date, format '-+{integer}', limits -10000..+10000; "interval" a string param for the time unit to add or subtract: 'Y' year, 'M' month, 'W' week, 'D' day; example: dueDate < now('+1', 'D')
  hasActiveSprint(): no operators; finds tasks added to the active sprint; example: hasActiveSprint()
  hasPlannedSprint(): no operators; finds tasks added to the planned sprint; example: hasPlannedSprint()
  hasOpenedChildren(): no operators; finds tasks with unclosed child tasks; uses only the system Parent-Child link, custom links (Parent of, Child of) are ignored; recursive over found children; example: hasOpenedChildren()
  hasOnlyActiveOrPlannedSprint(): does not work with not; finds tasks added to the active OR planned sprint; example: hasOnlyActiveOrPlannedSprint()
  entity in linkedEntities('task_number', 'link_type'): finds tasks by linked tasks with a given link type (see LinkTypes); if the type is omitted, searches linked tasks regardless of type; example: entity in linkedEntities('ECOM-903', 'isDependentOn')
  hasLinkedSystems('system_prefix'): finds tasks by linked systems, i.e. the prefix of a link in Linked artifacts; available prefixes: incidents, knowledge, other; example: hasLinkedSystems('incidents')
  hasSprintInStatus: finds tasks in a sprint with the given status; example: hasSprintInStatus('A')
  hasRelease('release_code'): finds stories and defects included in the specified release (Release field); example: hasRelease('RL-ABC-512')
  hasRelease(): finds stories and defects included in any release (Release field); example: hasRelease()
  hasReleases(['release_code', 'release_code']): finds stories and defects included in the listed releases (Release field); cannot be used without release numbers; example: hasReleases(['RL-ABC-512', 'RL-ABC-600', 'RL-ABC-100'])
  hasAffectedRelease('release_code'): finds defects affecting the specified release (Affected releases field); example: hasAffectedRelease('RL-ABC-512')
  hasAffectedRelease(): finds defects affecting any release (Affected releases field); example: hasAffectedRelease()
  hasAffectedReleases(['release_code', 'release_code']): finds defects affecting the listed releases (Affected releases field); cannot be used without release numbers; example: hasAffectedReleases(['RL-ABC-512', 'RL-ABC-600', 'RL-ABC-100'])
  startOfDay('offset', 'interval'): operators =, !=, >, >=, <, <=; date fields; returns the current date; params like now(); example: dueDate = startOfDay('+1', 'D')
  startOfWeek('offset', 'interval'): operators =, !=, >, >=, <, <=; date fields; returns the current week; params like now(); example: dueDate = startOfWeek('+1', 'W')
  startOfMonth('offset', 'interval'): operators =, !=, >, >=, <, <=; date fields; returns the current month; params like now(); example: dueDate = startOfMonth('+1', 'M')
  startOfYear('offset', 'interval'): operators =, !=, >, >=, <, <=; date fields; returns the current year; params like now(); example: dueDate = startOfYear('+1', 'Y')
  currentAreaSprint('space_code', 'offset'): finds tasks added to the active sprint in a space, or a sprint shifted N positions from active; returns a task list; "space_code" a required string, the space code; "offset" an optional string, the number of positions to shift, format '-+{integer}', limits -1000..+1000 including default 0; if omitted, the active sprint in the space; example: currentAreaSprint('PPTS', '+1')
  currentAreaSuperSprint('space_code', 'offset'): finds tasks added to the active super-sprint in a space, or a super-sprint shifted N positions from active; returns a task list; params like currentAreaSprint(); example: currentAreaSuperSprint('PPTS', '+1')
  assigneeIsMemberOfTeam(): finds tasks whose assignee is a member of the team; example: assigneeIsMemberOfTeam(null)
  assigneeIsMemberOfTeam(['team_id']): finds tasks whose assignee is a member of the specified team; example: assigneeIsMemberOfTeam(['team_abc'])
  fieldValueInComplDict("fieldCode.param operator 'value'"): finds tasks by attribute values of complex dictionaries (type complexDictionary); attribute name = code of the card attribute with type complexDictionary; param name = code of the dictionary's internal param (Cyrillic/Latin letters, digits, varchar-like symbols, UTF-8); to search by displayed drop-down values use 'NAME' as the param name (e.g. "КИБ" is a displayed value); allowed field type complexDictionary; example: fieldValueInComplDict("projectConsumer.name = 'КИБ'")
  linkedEntitiesOf("subquery", "link_type"): operators in, not in; allowed field number; finds tasks where an attribute of one of the linked tasks equals the condition: finds all tasks matching the nested QL subquery, then selects all tasks linked to them by process links; the second argument narrows by link type; example: status = "In Progress" AND number IN linkedEntitiesOf("type = 'incident'", "blocks")
LinkTypes:
  isBlockedBy ("Заблокирована"); blocks ("Блокирует"); duplicates ("Дублирует"); isDependentOn ("Зависима от"); dependsOn ("Зависит"); doAfter ("Должна быть сделана после"); doBefore ("Должна быть сделана до"); startsImmediatelyAfter ("Стартует сразу по выполнению"); endIsStartOf ("После выполнения стартует"); startWith ("Начать вместе с"); finishWith ("Закончить вместе с"); relatedTo ("Относится к"); dividedFrom ("Разделена от"); dividedInto ("Разделена на"); isClonedBy ("Склонирована"); clones ("Клонирует"); linkToRequest ("Связь с заявкой"); linksWithRequest ("Связи с заявкой"); initiatedBy ("Инициирована"); initiates ("Инициирует"); childsource ("Child Of")
```

```episteme id="BLG.QLReference.Examples" context="BacklogManagement"
UseThisWhen:
  checking the expected selection result of a QL query
Examples:
  area = 'CRM' and assignee = me() → all tasks of space 'CRM' whose assignee is the current user
  area = 'CRM' and status != 'closed' → all tasks of space 'CRM' whose status is not closed
  number=not null and not hasActiveSprint() and not hasPlannedSprint() → tasks without sprints
  status = 'closed' and area = 'CRM' or assignee=me() → all closed tasks of space 'CRM' plus all tasks assigned to the current user
```

### BLG.QLReference:End
