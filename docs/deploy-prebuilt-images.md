# Развёртывание из готовых образов

Альтернатива сборке на месте: образы наших сервисов собираются на машине с интернетом,
публикуются в Docker Hub, а в корпоративном контуре скачиваются через прокси Docker Hub (Nexus)
и запускаются без сборки. PyPI и npm в контуре при этом не нужны.

Наших образов семь (воркеры и планировщик используют образы своих сервисов):

| Образ | Сервисы compose |
|---|---|
| `cds-admin` | `admin`, `scheduler` |
| `cds-curves` | `curves`, `curves-worker` |
| `cds-credit` | `credit`, `credit-worker` |
| `cds-web-shell` | `shell` |
| `cds-web-mfe-admin` | `mfe-admin` |
| `cds-web-mfe-curves` | `mfe-curves` |
| `cds-web-mfe-credit` | `mfe-credit` |

Сторонние образы (Postgres, Keycloak, Elasticsearch, Fluent Bit, nginx) по-прежнему берутся
через `DOCKERHUB_MIRROR` / `QUAY_MIRROR`.

В образах нет секретов: пароли и ключи передаются в рантайме из `.env`, а `.env` не входит
в контекст сборки. Поэтому образы можно публиковать открыто (они содержат тот же код,
что и публичный репозиторий). Прокси Docker Hub в Nexus обычно ходит в Docker Hub анонимно,
поэтому репозитории должны быть **публичными**.

## 1. Публикация (машина с интернетом)

Один раз войдите в Docker Hub:

```bash
docker login
```

Соберите и опубликуйте образы (`<user>` — ваш логин Docker Hub, `0.5.1` — версия):

```powershell
$env:CDS_IMAGE_PREFIX = "docker.io/<user>"
$env:CDS_VERSION = "0.5.1"
docker compose build admin curves credit shell mfe-admin mfe-curves mfe-credit
docker compose push admin curves credit shell mfe-admin mfe-curves mfe-credit
```

То же в bash:

```bash
CDS_IMAGE_PREFIX=docker.io/<user> CDS_VERSION=0.5.1 docker compose build admin curves credit shell mfe-admin mfe-curves mfe-credit
```

```bash
CDS_IMAGE_PREFIX=docker.io/<user> CDS_VERSION=0.5.1 docker compose push admin curves credit shell mfe-admin mfe-curves mfe-credit
```

`push` перечисляет сервисы явно: остальные сервисы используют чужие образы, их публиковать не нужно.
Если Docker Desktop падает во время сборки (не хватает памяти виртуальной машины), собирайте сервисы
по одному: `docker compose build admin`, затем `curves` и т. д.
Для каждой новой версии меняйте `CDS_VERSION` — тогда на стенде всегда понятно, что запущено.

## 2. Запуск в контуре

Нужны файлы репозитория (compose и конфиги из `infra/` монтируются в контейнеры) и `.env`.
В `.env` укажите, откуда брать наши образы, — через тот же прокси Docker Hub:

```env
DOCKERHUB_MIRROR=nexus.corp.local/dockerhub
CDS_IMAGE_PREFIX=nexus.corp.local/dockerhub/<user>
CDS_VERSION=0.5.1
```

Скачайте образы и запустите без сборки:

```bash
docker compose pull
```

```bash
docker compose up -d --no-build
```

## Обновление с сохранением данных

Данные живут в томах Docker (`pg-data` — база с кривыми, журналом, пользователями Keycloak;
`es-data` — логи). Обычное обновление их не трогает. **Никогда не используйте `docker compose down -v`**
на системе с данными: ключ `-v` удаляет тома.

1. Резервная копия базы — в ней и данные сервисов, и пользователи Keycloak (PowerShell или bash;
   в Git Bash на Windows сначала выполните `export MSYS_NO_PATHCONV=1`). Файл появится в текущей папке:
   ```bash
   docker compose exec -T postgres pg_dump -U postgres -d cds -Fc -f /tmp/cds-backup.dump
   ```
   ```bash
   docker compose cp postgres:/tmp/cds-backup.dump ./cds-backup.dump
   ```
2. Обновите файлы репозитория (compose и `infra/`) до той же версии, что и образы.
   `.env` не перезаписывайте — поменяйте в нём `CDS_VERSION` и допишите новые переменные
   (существующие значения скрипт не трогает):
   ```bash
   python scripts/gen-env.py --update
   ```
3. Скачайте образы и перезапустите — без `down`:
   ```bash
   docker compose pull
   ```
   ```bash
   docker compose up -d --no-build
   ```

При запуске сервисы сами применяют миграции своих схем БД, `gateway` пересоздаётся (его метка зависит от
`CDS_VERSION`) и подхватывает новые маршруты из `infra/gateway/nginx.conf`, а задача `keycloak-config` доводит
настройки realm Keycloak (пользователи, пароли и роли сохраняются). Её результат:

```bash
docker compose logs keycloak-config
```

Восстановление из копии (только если что-то пошло не так):

```bash
docker compose cp ./cds-backup.dump postgres:/tmp/cds-backup.dump
```

```bash
docker compose exec -T postgres pg_restore -U postgres -d cds --clean --if-exists /tmp/cds-backup.dump
```

### Переход на 0.5.1 (новый сертификат Мосбиржи)

* `iss.moex.com` перешёл на сертификат Russian Trusted Sub CA (Минцифры), и загрузки из ISS падали с
  `CERTIFICATE_VERIFY_FAILED`. В 0.5.1 корневой и промежуточный сертификаты Минцифры встроены в образ `credit`
  и доверяются только для запросов к ISS.
* Достаточно `CDS_VERSION=0.5.1`, `docker compose pull` и `docker compose up -d --no-build`; схема БД и `.env`
  не меняются.
* `ISS_CA_BUNDLE` (корпоративный CA TLS-прокси) теперь добавляется к доверенным CA, а не заменяет их.

### Переход на 0.5.0 (прайсинг CDS)

* Новый модуль в разделе кредитных кривых: CDS-кривые контрольных лиц, калькулятор CDS (СПФИ и Сбер), календари
  рабочих дней, файл для position keeping (см. docs/cds-pricing.md).
* Достаточно `CDS_VERSION=0.5.0`, `docker compose pull` и `docker compose up -d --no-build`: сервис `credit` сам
  применит миграцию `0002_cds` (новые таблицы в схеме `credit`, календари «Москва» и «СПФИ»), новых переменных
  в `.env` нет. Обновите оба сервиса `credit` и `curves` одной версией: `curves` после сохранения процентной кривой
  ставит задачу пересчёта CDS-кривых.
* После обновления:
  1. **внесите праздники** в календари «Москва» и «СПФИ» (Календари рабочих дней) — пока их нет, рабочими
     считаются все будни, и даты начала срока, ребейта и платежей могут сместиться;
  2. проверьте настройки «Прайсинг CDS» (Настройки кредитных кривых): R<sub>q</sub>, сроки, режим ребейта;
  3. заведите контрольные лица (CDS-кривые → «Контрольное лицо») с привязкой к кредитным кривым — CDS-кривая
     посчитается сразу и дальше будет пересчитываться при каждом сохранении кредитной или процентной кривой.

### Переход на 0.4.0 (детализация расчётов в Excel)

* Кнопка **Excel** у процентных и кредитных кривых: файл с формулами для проверки расчётов
  (см. последние разделы docs/curves-math.md и docs/credit-math.md).
* Схема БД не меняется: достаточно `CDS_VERSION=0.4.0`, `docker compose pull` и `docker compose up -d --no-build`.
* Кредитные кривые, сохранённые раньше, выгружаются пересчётом по сохранённому составу и параметрам; новые сохранения
  хранят детали расчёта, и их файл воспроизводим в любой момент.
* Сборка на месте (без готовых образов) требует пакет `openpyxl` в зеркале PyPI.

### Переход на 0.3.1 (доработки интерфейса)

* Кнопки стандартных значений гладкости (0, 100, 250 … 10000) у процентных и кредитных кривых, более
  широкое левое меню. Данные и настройки не меняются: достаточно `CDS_VERSION=0.3.1`, `docker compose pull`
  и `docker compose up -d --no-build` (gateway пересоздастся сам).

### Переход на 0.3.0 (кредитные кривые)

* Новый сервис `credit` (+ `credit-worker`) и микрофронтенд `mfe-credit`: образы `cds-credit`, `cds-web-mfe-credit`.
* Новая схема БД `credit` и её роль создаются задачей `db-init` автоматически; пароль роли —
  `DB_PASSWORD_CREDIT`, его добавляет `python scripts/gen-env.py --update`. Без него `docker compose`
  остановится с подсказкой.
* Сервису нужен выход к `https://iss.moex.com` через `HTTPS_PROXY` — проверка и настройка TLS-прокси в
  [corporate-setup.md](corporate-setup.md#доступ-к-мосбирже-iss-для-кредитных-кривых). В `NO_PROXY` стоит
  дописать `credit,credit-worker,mfe-credit` (межсервисные вызовы сервиса работают и без этого).
* Фоновые задачи `credit.*` и их расписания появятся сами при первом запуске воркера.
* Остальные данные (процентные кривые, пользователи, журнал) не затрагиваются.
* В меню «Модуль «credit» недоступен» — работает старый `gateway`: nginx читает `nginx.conf` только при
  старте, а в compose-файле коммита `89240f2` gateway при обновлении ещё не пересоздавался. Пересоздайте его
  и обновите страницу:
  ```bash
  docker compose up -d --no-build --force-recreate gateway
  ```
  В следующих версиях это происходит само.

### Переход на 0.2.0 (своя страница входа)

* Страницы входа Keycloak больше не используются: вход — на странице CDS, по логину и паролю,
  которые у пользователей уже есть. `keycloak-config` включает для этого клиенту `cds-web` вход по паролю.
* Если `keycloak-config` завершился с ошибкой (пароль `KC_BOOTSTRAP_ADMIN_PASSWORD` из `.env` менялся
  в консоли Keycloak), вход покажет «Вход не настроен в Keycloak». Тогда укажите в `.env` действующий
  пароль администратора master-realm и повторите `docker compose up -d --no-build`.
* Пар-кривая теперь линейная между тенорами; у ранее сохранённых кривых она пересчитывается при показе,
  сами сохранённые данные не меняются.
* Переменная `CDS_DEV_REDIRECT_URI` больше не нужна — её можно удалить из `.env`.

## 3. Если прокси-репозиторий не отдаёт образ

Некоторые Nexus пропускают не все образы Docker Hub (политика репозитория). Если
`docker compose pull` пишет `not found` на образы `cds-*`, попросите администраторов
разрешить ваш namespace — или перенесите образы файлом, вообще без реестра.

На машине с образами:

```bash
docker save -o cds-images.tar local/cds-admin:0.5.1 local/cds-curves:0.5.1 local/cds-credit:0.5.1 local/cds-web-shell:0.5.1 local/cds-web-mfe-admin:0.5.1 local/cds-web-mfe-curves:0.5.1 local/cds-web-mfe-credit:0.5.1
```

В контуре (в `.env` оставьте `CDS_IMAGE_PREFIX=local`):

```bash
docker load -i cds-images.tar
```

```bash
docker compose up -d --no-build
```
