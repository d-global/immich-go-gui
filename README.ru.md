# Immich-Go GUI

**Русский** · [English](README.md)

> Этот репозиторий является публичным форком проекта [shitan198u/immich-go-gui](https://github.com/shitan198u/immich-go-gui). Мы сохраняем совместимость с upstream и добавляем функции для управляемой миграции больших фотоархивов. Оригинальные авторские права и MIT-лицензия сохраняются.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/downloads/)
[![immich-go](https://img.shields.io/badge/immich--go-0.32.0%20tested-blueviolet.svg)](https://github.com/simulot/immich-go)

Immich-Go GUI — кроссплатформенный графический интерфейс для [immich-go](https://github.com/simulot/immich-go). Он позволяет настраивать загрузку и архивирование через формы, безопасно хранить API-ключи, предварительно проверять соединение и запускать операции с [Immich](https://immich.app/) без ручной сборки длинных команд.

## Что уже умеет upstream

- загрузка из обычных папок, Google Photos, iCloud, Picasa и другого Immich;
- архивирование из поддерживаемых источников;
- Stack;
- профили для разных серверов и пользователей;
- API-ключи в системном keyring;
- безопасная передача секретов через переменные окружения;
- проверка соединения перед запуском;
- автоматическая загрузка совместимого `immich-go` с SHA-256 проверкой;
- Backup Monitor, фоновые загрузки, повторы, логи и системный tray.

## Что добавляет этот форк

В отдельной ветке разработки создаётся **Archive Migration Queue** для разовой миграции старых, вручную организованных фотоархивов.

Целевая логика:

1. выбрать корень архива;
2. просканировать только папки первого уровня;
3. видеть количество файлов и размер каждой папки;
4. хранить состояния `TODO / READY / UPLOADING / DONE / PARTIAL / ERROR / SKIP`;
5. искать, сортировать и фильтровать список;
6. скрывать уже загруженные `DONE`;
7. массово выбирать папки;
8. загружать очередь последовательно;
9. создавать для каждой папки первого уровня отдельный альбом Immich с тем же именем;
10. сохранять состояние после каждой папки, чтобы после остановки или перезапуска не гадать, что уже было загружено.

Разработка идёт в ветке:

`feature/archive-migration-queue`

Draft PR:

https://github.com/d-global/immich-go-gui/pull/1

## Статус

Функция Archive Migration пока находится в разработке и не является готовым релизом. Основная ветка `master` остаётся максимально близкой к upstream до завершения и проверки функции.

## Установка

Пока для стабильного использования рекомендуется официальный upstream-релиз:

https://github.com/shitan198u/immich-go-gui/releases/latest

Когда fork-версия Archive Migration будет готова, здесь появятся отдельные GitHub Releases с Windows Setup/Portable и, по возможности, теми же форматами для macOS/Linux.

## Разработка

Требования:

- Python 3.13;
- `uv`;
- PySide6 и остальные зависимости из `pyproject.toml`.

```bash
git clone https://github.com/d-global/immich-go-gui.git
cd immich-go-gui
git checkout feature/archive-migration-queue
uv sync --dev
uv run app.py
```

Тесты:

```bash
uv run pytest
```

## Документация

- [Документация upstream](docs/README.md)
- [Архитектура Archive Migration — EN](docs/developer-guide/archive-migration.md)
- [Архитектура Archive Migration — RU](docs/developer-guide/archive-migration.ru.md)
- [Правила сопровождения форка — EN](docs/developer-guide/fork-maintenance.md)
- [Правила сопровождения форка — RU](docs/developer-guide/fork-maintenance.ru.md)

## Языки

Для новых пользовательских функций этого форка документация ведётся на двух языках: **English + Русский**. Английский остаётся основным языком кода, имён сущностей, коммитов и совместимости с upstream; русская версия поддерживается параллельно для пользовательской документации и заметок о fork-специфичных функциях.

## Upstream и синхронизация

Fork не отрывается от оригинального проекта. Изменения upstream периодически подтягиваются в `master`, после чего рабочие feature-ветки обновляются поверх актуальной базы. Мы избегаем переписывания уже существующих upstream-компонентов и добавляем только недостающую функциональность.

## Лицензия и авторство

Проект распространяется по [MIT License](LICENSE.txt). Этот fork сохраняет исходную лицензию, copyright notices и историю авторства upstream. Дополнительные изменения в fork поддерживаются `d-global`.
