# aivo

Зонтичный чарт платформы: `config`, `service-discovery`, `identity`,
`instance`, `retail-ai` и подключаемые сабчарты. У каждого сабчарта свои
`values.yaml` (с комментарием у каждого ключа) и, где нужно, свой `README.md`
в `charts/<имя>/`. Этот файл описывает то, что затрагивает несколько
сабчартов сразу.

## Поставляемые MCP-коннекторы

Два MCP-коннектора поставляются с установкой (AIV-2337), каждый — отдельный
образ и отдельный сабчарт, оба выключены по умолчанию:

| Сабчарт | Образ | Service | Тул в агенте | slug |
|---|---|---|---|---|
| `mcp-telegram` | `ghcr.io/aivoagent/agent/mcp-telegram` | `<release>-mcp-telegram:8931` | `mcp_telegram_send_message` | `telegram` |
| `mcp-email` | `ghcr.io/aivoagent/agent/mcp-email` | `<release>-mcp-email:8931` | `mcp_email_send_message` | `email` |

Подключение коннектора — два шага: сабчарт и элемент `globalServers` в
конфигурации api (плюс опциональный Secret). Затем администратор тенанта
включает его у себя на странице «MCP-серверы» → «Встроенные коннекторы»
(opt-in) и вводит реквизиты в «Настроить»: токен бота и чаты для Telegram,
платформенный или собственный SMTP и allowlist адресатов для почты
(AIV-2337 §6). Реквизитов тенантов в чартах, values и argo-репо нет.

### 1. Secret — опционален, вне git

Credentials в values и в argo-репо не попадают никогда. Если Secret нужен, он
создаётся руками в namespace релиза и подключается сабчартом по имени
(`envFrom.secretRef`; пустой `existingSecret` — `envFrom` не рендерится):

```bash
# только если нужен платформенный SMTP с авторизацией:
kubectl -n <namespace> create secret generic aivo-mcp-email \
  --from-literal=SMTP_USER='<user>' \
  --from-literal=SMTP_PASSWORD='<password>'
```

Опциональный ключ `MCP_AUTH_TOKEN` в Secret любого коннектора заставляет его
требовать `Authorization: Bearer <token>` на `/mcp` — тогда тот же заголовок
указывается в `authHeaders` (шаг 3). Основной барьер — NetworkPolicy сабчарта
(`networkPolicy.enabled`), токен — второй.

### 2. Сабчарт

```yaml
mcp-telegram:
  enabled: true

mcp-email:
  enabled: true
  # платформенный SMTP — опционально; без него тенанты задают свой в UI
  existingSecret: aivo-mcp-email
  config:
    SMTP_HOST: smtp.example.com
    SMTP_PORT: "587"
    EMAIL_FROM: "Aivo <noreply@example.com>"
```

Полный список ключей `config` — в `charts/mcp-telegram/README.md` и
`charts/mcp-email/README.md`.

### 3. `globalServers` в конфигурации api

```yaml
config:
  config:
    services:
      api:
        mcp:
          globalServers:
            - slug: telegram
              name: Telegram
              description: Send a message to the team's Telegram chats
              url: http://<release>-mcp-telegram:8931/mcp
            - slug: email
              name: Email
              url: http://<release>-mcp-email:8931/mcp
              # только если в Secret коннектора задан MCP_AUTH_TOKEN:
              # authHeaders:
              #   Authorization: "Bearer <token>"
```

При старте api заводит по строке на элемент в глобальном слое
`mcp_servers`; элемент, убранный из списка, выключается, но не удаляется.
Хост из `url` глобального коннектора api allowlist'ит **неявно** — дублировать
`<release>-mcp-telegram` в `mcp.egress.allowHosts` не нужно (но можно). Пустой
список не рендерится вовсе.

`authHeaders` попадают в ConfigMap конфигурации api как есть — значит, и в
argo-репо. Поэтому `MCP_AUTH_TOKEN` — второй барьер, а не секрет уровня
пароля SMTP: в argo-репо его кладут только туда, где репо закрыто.
