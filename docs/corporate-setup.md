# Развёртывание в корпоративном контуре

Все внешние зависимости тянутся через настраиваемые зеркала и прокси. Настройки — в `.env`.

## 1. Docker-образы

Все базовые образы, кроме Keycloak, берутся с Docker Hub, Keycloak — с quay.io.
Адреса задаются префиксом (используется и в `docker-compose.yml`, и в `Dockerfile` через build-arg):

```env
# Nexus docker-proxy / Harbor proxy-cache / Artifactory remote repo
DOCKERHUB_MIRROR=nexus.corp.local:8443/dockerhub
QUAY_MIRROR=nexus.corp.local:8443/quay
```

Итоговые имена: `${DOCKERHUB_MIRROR}/library/postgres:16.6-alpine`,
`${QUAY_MIRROR}/keycloak/keycloak:26.0.7` и т. п. Для Harbor proxy-cache обычно
`harbor.corp.local/dockerhub-proxy`.

Альтернатива для Docker Hub — зеркало на уровне демона (`/etc/docker/daemon.json`):

```json
{ "registry-mirrors": ["https://nexus.corp.local:8443"] }
```

Список используемых образов: `postgres:16.6-alpine`, `nginx:1.27.3-alpine`, `python:3.12.8-slim`,
`node:22.12.0-alpine`, `elasticsearch:8.15.3`, `fluent/fluent-bit:3.1.9`,
`quay.io/keycloak/keycloak:26.0.7`. Проверить, что зеркало отдаёт их все, можно заранее:

```bash
docker compose pull --ignore-buildable
```

Если прокси-репозиторий отвечает `not found` на конкретный образ при том, что остальные качаются,
обычно образ не разрешён политикой репозитория (или тег не закэширован) — попросите
администраторов Nexus/Harbor открыть его.

Собственные образы именуются `${CDS_IMAGE_PREFIX}/cds-<service>:${CDS_VERSION}` — задайте
`CDS_IMAGE_PREFIX=registry.corp.local/cds`, соберите и опубликуйте их из CI, а на стендах
запускайте `docker compose pull && docker compose up -d --no-build`. Тот же приём с Docker Hub
вместо внутреннего реестра — в [deploy-prebuilt-images.md](deploy-prebuilt-images.md).

## 2. Python-пакеты

```env
PIP_INDEX_URL=https://nexus.corp.local/repository/pypi-proxy/simple
PIP_TRUSTED_HOST=nexus.corp.local   # только если сертификат не доверенный
```

## 3. npm-пакеты

```env
NPM_REGISTRY=https://nexus.corp.local/repository/npm-proxy/
```

`package-lock.json` содержит адреса registry.npmjs.org — npm (≥ 9) автоматически подменяет их
на настроенный реестр (`replace-registry-host`).

## 4. Прокси для выхода в интернет

```env
HTTP_PROXY=http://proxy.corp.local:3128
HTTPS_PROXY=http://proxy.corp.local:3128
NO_PROXY=localhost,127.0.0.1,postgres,keycloak,elasticsearch,admin,curves,curves-worker,scheduler,shell,mfe-admin,mfe-curves,gateway,fluent-bit,.corp.local
```

* при сборке переменные передаются как стандартные build-args Docker (в слоях образа не сохраняются);
* в рантайме они передаются в контейнеры бэкенда (на случай обращений к внешним API);
* **важно**: `NO_PROXY` должен содержать имена всех внутренних сервисов compose, иначе
  межсервисные вызовы (проверка токенов, Keycloak Admin API, Elasticsearch) пойдут в прокси.
  При добавлении сервиса допишите его имя;
* **важно**: туда же — домен внутренних зеркал (`.corp.local` в примере), иначе pip и npm
  пойдут к Nexus через прокси;
* если все зависимости берутся из внутренних зеркал, прокси при сборке не нужен вовсе —
  оставьте `HTTP_PROXY`/`HTTPS_PROXY` пустыми;
* прокси должен быть доступен **изнутри контейнера**: `127.0.0.1` там — сам контейнер.
  Для прокси, запущенного на вашей машине (cntlm, px), используйте `host.docker.internal`.

Docker-демону прокси для `docker pull` настраивается отдельно (systemd drop-in
`/etc/systemd/system/docker.service.d/http-proxy.conf` или настройки Docker Desktop).
Прокси из настроек Docker Desktop (Settings → Resources → Proxies) Docker тоже подставляет
в контейнеры — внесите домен зеркал и в его список исключений.

### Типовая ошибка

```
WARNING: Retrying ... ProxyError('Cannot connect to proxy.', TimeoutError('timed out')): /repository/pypi-proxy/simple/fastapi/
ERROR: No matching distribution found for fastapi==0.115.6
```

pip обращается к зеркалу через недоступный из контейнера прокси. Добавьте домен зеркала
в `NO_PROXY` (или очистите `HTTP_PROXY`/`HTTPS_PROXY`) и повторите `docker compose up -d --build`.

## 5. Внутренние сертификаты

Если зеркала используют корпоративный CA, добавьте его в образы: для Python — `PIP_CERT`
или `pip config set global.cert`, для Node — `NODE_EXTRA_CA_CERTS`. Простейший путь —
базовые образы из внутреннего реестра, в которые CA уже добавлен.

## 6. Публичный адрес и TLS

`CDS_PUBLIC_URL` — адрес, по которому пользователи открывают систему (используется Keycloak
для issuer и redirect URI, и в magic-ссылках). TLS терминируйте на внешнем балансировщике
перед `gateway` с заголовками `X-Forwarded-Proto/Host`, тогда `CDS_PUBLIC_URL=https://cds.corp.local`.

## 7. Чек-лист перед продуктивом

- [ ] `.env` создан через `scripts/gen-env.py`, хранится в секрет-хранилище, не в git;
- [ ] временный пароль `CDS_BOOTSTRAP_ADMIN_PASSWORD` сменён при первом входе;
- [ ] Elasticsearch и Postgres не опубликованы наружу (в базовом compose портов нет);
- [ ] резервное копирование тома `pg-data`;
- [ ] `CDS_DEV_REDIRECT_URI` указывает на публичный адрес (уберите `localhost:5173`).
