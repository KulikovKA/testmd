# Подготовка Java-проекта

Используй только проект текущей DevelopmentTask в существующем Agent workspace.
Для Maven подготовь pom.xml с Java release=21 и явными версиями необходимых
plugins/dependencies. Для Gradle подготовь settings.gradle и build.gradle с
Java toolchain 21. В образе имеется Gradle; Wrapper применяется только при
наличии доверенной проверяемой конфигурации и wrapper artifacts.

Исходники находятся в src/main/java, тесты — в src/test/java. Добавь .gitignore
для target/, build/ и .gradle/. Не создавай .git, .env, credential files,
системные пути и инструкции установки ПО на host. Эта инструкция сама по себе
не создаёт файлов: изменения передаются как проверяемые ProjectFile предложения.
