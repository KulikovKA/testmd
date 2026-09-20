# Тестирование Java

Тесты должны проверять поведение из ТЗ, ошибочные входы и значимые граничные случаи.
Подготовь воспроизводимые проверки для выбранного Maven/Gradle. Стандартные
операции — mvn test/package и Gradle test/build, исполняемые application layer
внутри Agent. Не запускай произвольный shell и не меняй server network policy.

Оценивай только переданный результат execution. Отсутствующий инструмент,
таймаут или недоступная зависимость не являются успешной сборкой. Различай
LOCAL_VERIFIED, LOCAL_NOT_AVAILABLE и SERVER_VERIFICATION_REQUIRED.
Не симулируй stdout, exit code, покрытие и прошедшие тесты.
