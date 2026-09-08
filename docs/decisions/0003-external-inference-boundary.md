# ADR-0003: Граница внешнего inference

- Статус: Accepted
- Дата: 2026-09-08
- Владельцы решения: сопровождающие проекта

## Контекст

Qwen Code — CLI/agent framework, тогда как модель Qwen обслуживается существующим развёртыванием Ollama. Локальные и корпоративные адреса Ollama, модели, аутентификация и сетевые пути могут различаться. Включение model server в каждый agent runtime смешало бы execution и inference и сделало бы корпоративную интеграцию непрактичной.

## Решение

Запускать Qwen Code внутри изолированного agent runtime, а Ollama эксплуатировать как отдельный внешний service. Передавать ссылку на endpoint Ollama, имя модели и любые provider options через явную deployment configuration. Передавать credentials во время выполнения через environment или эквивалентный secret mechanism; никогда не хранить их в repository configuration или записях Agent.

Ни один обязательный host, IP, port, model, credential или corporate endpoint не hardcode-ится. Можно предоставлять безопасные mock/local примеры. TASK-005 должна валидировать фактические актуальные configuration и protocol Qwen Code-to-Ollama до заявлений о реализации.

## Последствия

- Образы Agent остаются меньшими и не дублируют ресурсы модели.
- Локальные Docker и корпоративные Kata runtimes могут использовать разные развёртывания Ollama без изменения логики Orchestrator.
- Network policy должна явно разрешать выбранный inference endpoint.
- Ошибки доступности inference и аутентификации должны сообщаться отдельно от ошибках lifecycle runtime.
- Необходимо тестировать connectivity изнутри каждого runtime; нельзя предполагать, что host `localhost` указывает на Ollama.

## Отклонённые альтернативы

- Запуск Ollama внутри каждого agent runtime: расходует ресурсы и противоречит требованию существующего service.
- Hardcode локального адреса/модели Ollama: не работает между Docker hosts и корпоративными средами.
- Рассмотрение Qwen Code и Qwen LLM как одного компонента: скрывает границы configuration, failures и security.
