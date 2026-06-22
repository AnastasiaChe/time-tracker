# Anastasia Che Time Tracker

Локальный трекер времени для фрилансера или небольшой студии. Работает в браузере, хранит данные в SQLite на вашем компьютере и не отправляет их на внешний сервер.

## Стек

- Python 3.11 или 3.12
- Встроенный HTTP-сервер Python
- SQLite
- HTML, CSS, JavaScript без сборщика
- ReportLab для внутренней генерации PDF, если используется endpoint экспорта
- pdfplumber для импорта PDF из Clockify
- Font Awesome Free, подключен локально

## Что умеет

- Клиенты: название, контакт, email, валюта.
- Проекты: клиент, название, почасовая ставка, валюта.
- Таймер: старт, стоп, редактирование деталей во время работы.
- Записи времени: ручное добавление, редактирование, удаление, повторный запуск по старой записи.
- Отчеты: фильтры по датам, клиенту, проекту и тегам.
- Дашборд: часы, активности, разбивка по проектам и клиентам.
- Печать отчета: A4-страницы с логотипом.
- Импорт Clockify PDF.
- Печатная версия отчета из браузера.
- Настройки брендинга: название компании, горизонтальный логотип интерфейса, квадратный логотип для печатного отчета.

## Установка на macOS

1. Установите Python 3.11 или 3.12:

   ```bash
   python3 --version
   ```

   Если Python не установлен, поставьте его с [python.org](https://www.python.org/downloads/macos/).

2. Скачайте проект одним из способов.

   Простой способ: откройте страницу проекта на GitHub, нажмите `Code` → `Download ZIP`, распакуйте архив в удобную папку. Если папка после распаковки называется `time-tracker-main`, можно переименовать ее в `time-tracker` или использовать реальное имя папки в команде `cd`.

   Способ через Git:

   ```bash
   git clone https://github.com/AnastasiaChe/time-tracker.git
   ```

3. Перейдите в папку проекта:

   ```bash
   cd /ваш/путь/до/папки/time-tracker
   ```

4. Создайте виртуальное окружение:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

5. Установите зависимости:

   ```bash
   pip install -r requirements.txt
   ```

6. Добавьте тестового клиента и демо-записи:

   ```bash
   python app.py --seed-demo
   ```

7. Откройте приложение:

   ```text
   http://127.0.0.1:8000
   ```

## Установка на Windows

1. Установите Python 3.11 или 3.12 с [python.org](https://www.python.org/downloads/windows/).

   Во время установки включите галочку `Add python.exe to PATH`.

2. Скачайте проект одним из способов.

   Простой способ: откройте страницу проекта на GitHub, нажмите `Code` → `Download ZIP`, распакуйте архив в удобную папку. Если папка после распаковки называется `time-tracker-main`, можно переименовать ее в `time-tracker` или использовать реальное имя папки в команде `cd`.

   Способ через Git:

   ```powershell
   git clone https://github.com/AnastasiaChe/time-tracker.git
   ```

3. Откройте PowerShell и перейдите в папку проекта:

   ```powershell
   cd C:\ваш\путь\до\папки\time-tracker
   ```

4. Создайте виртуальное окружение:

   ```powershell
   py -3 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

   Если PowerShell ругается на запуск скриптов, выполните:

   ```powershell
   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
   .\.venv\Scripts\Activate.ps1
   ```

5. Установите зависимости:

   ```powershell
   pip install -r requirements.txt
   ```

6. Добавьте тестового клиента и демо-записи:

   ```powershell
   python app.py --seed-demo
   ```

7. Откройте приложение:

   ```text
   http://127.0.0.1:8000
   ```

## Запуск

Каждый раз перед запуском нужно перейти в папку проекта.

macOS:

```bash
cd /ваш/путь/до/папки/time-tracker
source .venv/bin/activate
python app.py
```

Windows PowerShell:

```powershell
cd C:\ваш\путь\до\папки\time-tracker
.\.venv\Scripts\Activate.ps1
python app.py
```

После запуска откройте в браузере:

```text
http://127.0.0.1:8000
```

Запуск на другом порту:

```bash
cd /ваш/путь/до/папки/time-tracker
python app.py 8080
```

Запуск с демо-данными:

```bash
cd /ваш/путь/до/папки/time-tracker
python app.py --seed-demo
```

Команда `--seed-demo` не очищает вашу базу. Она добавляет демо-клиента, два проекта и несколько записей, если таких записей еще нет.

## Где лежат данные

- Основная база: `data/time_tracker.sqlite3`
- Загруженные логотипы: `static/uploads/`
- Стандартные логотипы: `static/assets/`
- Локальные шрифты: `static/vendor/fonts/`
- Интерфейс: `static/index.html`, `static/app.js`, `static/styles.css`
- Сервер и работа с базой: `app.py`

База создается автоматически при первом запуске приложения.

## Настройки компании

Откройте пункт меню `Настройки` и загрузите:

1. Горизонтальный логотип для интерфейса.
2. Квадратный логотип для печатного отчета.
3. Название компании для title браузера.

Поддерживаются PNG, JPG, WEBP и SVG.

## Шрифты и иконки

Пользователю не нужно отдельно устанавливать шрифты или Font Awesome.

- `Roboto` для интерфейса лежит в `static/vendor/fonts/roboto/`.
- `Mulish` для печатной версии отчета лежит в `static/vendor/fonts/mulish/`.
- Font Awesome лежит в `static/vendor/fontawesome/` и `chrome-extension/vendor/fontawesome/`.

Все подключено локально из репозитория, без обращения к Google Fonts или CDN.

## Chrome extension

Папка `chrome-extension/` содержит локальное расширение для Chrome. Расширение работает вместе с локальным сервером, поэтому сначала запустите приложение:

```bash
cd /ваш/путь/до/папки/time-tracker
python app.py
```

Потом установите расширение:

1. Откройте Chrome.
2. Перейдите на страницу:

   ```text
   chrome://extensions
   ```

3. Включите `Developer mode` в правом верхнем углу.
4. Нажмите `Load unpacked`.
5. Выберите папку:

   ```text
   chrome-extension
   ```

6. Закрепите расширение на панели Chrome, если хотите запускать таймер быстрее.

Если расширение не видит приложение, проверьте, что локальный сервер открыт по адресу:

```text
http://127.0.0.1:8000
```

## Лицензия

Проект доступен бесплатно для личного некоммерческого использования. Коммерческое использование, перепродажа, публикация модифицированных копий и использование в продуктах для клиентов требуют отдельного письменного разрешения.

Полный текст: [LICENSE](LICENSE).

Сторонние шрифты и Font Awesome описаны отдельно: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
