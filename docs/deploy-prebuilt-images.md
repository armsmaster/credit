# Развёртывание из готовых образов

Альтернатива сборке на месте: образы наших сервисов собираются на машине с интернетом,
публикуются в Docker Hub, а в корпоративном контуре скачиваются через прокси Docker Hub (Nexus)
и запускаются без сборки. PyPI и npm в контуре при этом не нужны.

Наших образов пять (воркер и планировщик используют образы `curves` и `admin`):

| Образ | Сервисы compose |
|---|---|
| `cds-admin` | `admin`, `scheduler` |
| `cds-curves` | `curves`, `curves-worker` |
| `cds-web-shell` | `shell` |
| `cds-web-mfe-admin` | `mfe-admin` |
| `cds-web-mfe-curves` | `mfe-curves` |

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

Соберите и опубликуйте образы (`<user>` — ваш логин Docker Hub, `0.1.0` — версия):

```powershell
$env:CDS_IMAGE_PREFIX = "docker.io/<user>"
$env:CDS_VERSION = "0.1.0"
docker compose build admin curves shell mfe-admin mfe-curves
docker compose push admin curves shell mfe-admin mfe-curves
```

То же в bash:

```bash
CDS_IMAGE_PREFIX=docker.io/<user> CDS_VERSION=0.1.0 docker compose build admin curves shell mfe-admin mfe-curves
```

```bash
CDS_IMAGE_PREFIX=docker.io/<user> CDS_VERSION=0.1.0 docker compose push admin curves shell mfe-admin mfe-curves
```

`push` перечисляет сервисы явно: остальные сервисы используют чужие образы, их публиковать не нужно.
Для каждой новой версии меняйте `CDS_VERSION` — тогда на стенде всегда понятно, что запущено.

## 2. Запуск в контуре

Нужны файлы репозитория (compose и конфиги из `infra/` монтируются в контейнеры) и `.env`.
В `.env` укажите, откуда брать наши образы, — через тот же прокси Docker Hub:

```env
DOCKERHUB_MIRROR=nexus.corp.local/dockerhub
CDS_IMAGE_PREFIX=nexus.corp.local/dockerhub/<user>
CDS_VERSION=0.1.0
```

Скачайте образы и запустите без сборки:

```bash
docker compose pull
```

```bash
docker compose up -d --no-build
```

Обновление на новую версию: поменять `CDS_VERSION` в `.env` и повторить обе команды.

## 3. Если прокси-репозиторий не отдаёт образ

Некоторые Nexus пропускают не все образы Docker Hub (политика репозитория). Если
`docker compose pull` пишет `not found` на образы `cds-*`, попросите администраторов
разрешить ваш namespace — или перенесите образы файлом, вообще без реестра.

На машине с образами:

```bash
docker save -o cds-images.tar local/cds-admin:0.1.0 local/cds-curves:0.1.0 local/cds-web-shell:0.1.0 local/cds-web-mfe-admin:0.1.0 local/cds-web-mfe-curves:0.1.0
```

В контуре (в `.env` оставьте `CDS_IMAGE_PREFIX=local`):

```bash
docker load -i cds-images.tar
```

```bash
docker compose up -d --no-build
```
