# mcp-telegram

Подчарт зонтичного чарта `aivo`: поставляемый MCP-коннектор «Telegram-бот —
отправить сообщение» (AIV-2337). Один под и ClusterIP Service
`<release>-mcp-telegram:8931`; api ходит к нему по streamable-HTTP внутри
кластера (`POST /mcp`) как к глобальному коннектору. Тул один —
`mcp_telegram_send_message`.

По умолчанию выключен. Как его включить и подключить к api — в
`aivo/README.md`, раздел «Поставляемые MCP-коннекторы». Все ключи — в
`values.yaml`, с комментарием у каждого.

## Что рендерит

| Объект | Когда | Зачем |
|---|---|---|
| `Deployment` | `enabled: true` | 1 реплика, uid 1001, `readOnlyRootFilesystem`, пробы на `GET /healthz` |
| `Service` | `enabled: true` | ClusterIP `<release>-mcp-telegram`, порт 8931 |
| `NetworkPolicy` | `networkPolicy.enabled: true` | вход только от подов api (`retail-ai-back`, `retail-ai-worker`) этого релиза |
| `Pod` (hook `helm test`) | `helm test` | `wget` на `/healthz` через Service |

## Реквизиты тенантов — не в чарте

С v2.1 (AIV-2337 §6) токен бота, чаты и чат по умолчанию — настройки
**тенанта**: администратор вводит их в админке тенанта («MCP-серверы» →
«Встроенные коннекторы» → «Настроить»), api передаёт их коннектору в каждом
запросе. В values, Secret'ах и argo-репо их нет; коннектор без них стартует и
отвечает `/healthz`.

## Secret — опционален

Единственный ключ, который коннектор читает из Secret, — `MCP_AUTH_TOKEN`
(второй барьер после NetworkPolicy: `/mcp` требует
`Authorization: Bearer <token>`, api шлёт его через
`globalServers[].authHeaders`). Нужен — создаётся вне git и подключается
целиком через `envFrom.secretRef`:

```bash
kubectl -n <namespace> create secret generic aivo-mcp-telegram \
  --from-literal=MCP_AUTH_TOKEN='<random>'
```

```yaml
mcp-telegram:
  enabled: true
  existingSecret: aivo-mcp-telegram   # пусто — envFrom не рендерится вовсе
```

После ротации ключа под сам не перезапускается:
`kubectl -n <namespace> rollout restart deployment/<release>-mcp-telegram`.

## Несекретная конфигурация

`config` — карта env уровня оператора, контракт AIV-2337 §6.9. Пустое значение
не рендерится, и действует дефолт коннектора.

| Ключ | Дефолт коннектора |
|---|---|
| `TELEGRAM_API_BASE_URL` | `https://api.telegram.org` (тенант не меняет) |
| `TELEGRAM_DISABLE_LINK_PREVIEW` | `true` (дефолт настройки тенанта) |
| `RATE_LIMIT_PER_MINUTE` | `30` (на тенанта) |

Невалидная конфигурация — процесс выходит с кодом 1, под в CrashLoopBackOff,
причина в логе пода.

## NetworkPolicy

Выключена по умолчанию, как `retail-ai.back.networkPolicy`: работает только на
CNI с поддержкой NetworkPolicy (Canal на RKE2), а пробы kubelet идут с узла и
могут потребовать `ipBlock` в `networkPolicy.extraIngress`. Включённая без
источников (`fromApi: false` и пустой `extraFrom`) — рендер падает: пустой
`from` открывает порт всем, а не никому. Под `helm test` в число источников не
входит — при включённой ограде тест соединения падает.
