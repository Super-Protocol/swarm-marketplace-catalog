# mcp-email

Подчарт зонтичного чарта `aivo`: поставляемый MCP-коннектор «Почта — отправить
письмо» через SMTP (AIV-2337). Один под и ClusterIP Service
`<release>-mcp-email:8931`; api ходит к нему по streamable-HTTP внутри кластера
(`POST /mcp`) как к глобальному коннектору. Тул один —
`mcp_email_send_message`.

По умолчанию выключен. Как его включить и подключить к api — в
`aivo/README.md`, раздел «Поставляемые MCP-коннекторы». Все ключи — в
`values.yaml`, с комментарием у каждого.

## Что рендерит

| Объект | Когда | Зачем |
|---|---|---|
| `Deployment` | `enabled: true` | 1 реплика, uid 1001, `readOnlyRootFilesystem`, пробы на `GET /healthz` |
| `Service` | `enabled: true` | ClusterIP `<release>-mcp-email`, порт 8931 |
| `NetworkPolicy` | `networkPolicy.enabled: true` | вход только от подов api (`retail-ai-back`, `retail-ai-worker`) этого релиза |
| `Pod` (hook `helm test`) | `helm test` | `wget` на `/healthz` через Service |

## Платформенный SMTP — опционален

С v2.1 (AIV-2337 §6) SMTP и allowlist получателей — настройки **тенанта**:
администратор в админке тенанта («MCP-серверы» → «Встроенные коннекторы» →
«Настроить») выбирает «платформенный SMTP» или задаёт собственный (хост, порт,
логин, пароль, отправитель) и список разрешённых адресатов. В values, Secret'ах
и argo-репо реквизитов тенантов нет.

Чарт задаёт только **платформенный** SMTP — им пользуются тенанты, выбравшие
его. Не задан (`config.SMTP_HOST` пуст) — коннектор всё равно стартует, а
тенантам с режимом «платформенный» отвечает `platform_smtp_not_configured`:
они настраивают свой SMTP в UI.

### Secret

Тоже опционален. Пароль (и логин) платформенного SMTP в values не попадают —
Secret создаётся вне git и подключается целиком через `envFrom.secretRef`:

```bash
kubectl -n <namespace> create secret generic aivo-mcp-email \
  --from-literal=SMTP_USER='<user>' \
  --from-literal=SMTP_PASSWORD='<password>'
# опционально второй барьер после NetworkPolicy:
#   --from-literal=MCP_AUTH_TOKEN='<random>'
```

```yaml
mcp-email:
  enabled: true
  existingSecret: aivo-mcp-email   # пусто — envFrom не рендерится вовсе
```

После ротации пароля под сам не перезапускается:
`kubectl -n <namespace> rollout restart deployment/<release>-mcp-email`.

### Несекретная конфигурация

`config` — карта env, контракт AIV-2337 §6.9. Пустое значение не рендерится, и
действует дефолт коннектора.

| Ключ | Дефолт коннектора |
|---|---|
| `SMTP_HOST` | — (пусто = платформенного SMTP нет) |
| `SMTP_PORT` | `587` |
| `SMTP_SECURE` | `false` (STARTTLS; `true` — implicit TLS/465) |
| `SMTP_TLS_REJECT_UNAUTHORIZED` | `true` |
| `EMAIL_FROM` | — (`Name <addr>`, обязателен при `SMTP_HOST`) |
| `EMAIL_REPLY_TO` | — |
| `SMTP_PLATFORM_ALLOW_PRIVATE_HOST` | `false` (`true` — платформенный `SMTP_HOST` освобождён от запрета приватных адресов, который действует для SMTP тенантов) |
| `RATE_LIMIT_PER_MINUTE` | `30` (на тенанта) |

Невалидная конфигурация — процесс выходит с кодом 1, под в CrashLoopBackOff,
причина в логе пода. Корпоративный CA SMTP-сервера — через `global.caCert`
зонтичного чарта (`NODE_EXTRA_CA_CERTS`), а не `SMTP_TLS_REJECT_UNAUTHORIZED`.

## NetworkPolicy

Выключена по умолчанию, как `retail-ai.back.networkPolicy`: работает только на
CNI с поддержкой NetworkPolicy (Canal на RKE2), а пробы kubelet идут с узла и
могут потребовать `ipBlock` в `networkPolicy.extraIngress`. Включённая без
источников (`fromApi: false` и пустой `extraFrom`) — рендер падает: пустой
`from` открывает порт всем, а не никому. Под `helm test` в число источников не
входит — при включённой ограде тест соединения падает.
