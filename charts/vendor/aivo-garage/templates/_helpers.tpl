{{/* Полное имя релиза: имя релиза как есть, чтобы адрес хранилища совпадал с
     тем, что инженер пишет в values платформы (endpoint: http://aivo-garage:3900). */}}
{{- define "garage.fullname" -}}
{{- default .Release.Name .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "garage.labels" -}}
app.kubernetes.io/name: garage
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/* Отбор пода сервера — и только его.

     `component: server` здесь не для порядка. Job инициализации несёт те же
     `name` и `instance` (это один релиз), и без третьей метки он попадал под
     селектор headless-сервиса: DNS отдавал ему в ответ на `aivo-garage-headless`
     ЕГО СОБСТВЕННЫЙ адрес, а не адрес сервера. Job уходил в бесконечное
     «connection refused» на самого себя — при зелёном с виду релизе. */}}
{{- define "garage.selectorLabels" -}}
app.kubernetes.io/name: garage
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: server
{{- end -}}

{{/* Содержимое garage.toml. Лежит в секрете целиком, а не в ConfigMap, потому
     что несёт rpc_secret и admin_token.

     replication_factor = 1 — узел один. Значение по умолчанию у Garage 3, и с
     ним одиночный узел не набирает кворум ни на одну запись.

     rpc_public_addr = 127.0.0.1 — адрес, который узел объявляет соседям.
     Соседей у него нет, а обращаться к самому себе по адресу пода он не
     обязан: этот адрес меняется на каждом перезапуске, а идентификатор узла в
     раскладке — нет.

     В кластерном режиме фактор репликации — из values, а rpc_public_addr не
     пишется вовсе: файл конфигурации у всех подов общий, а адрес у каждого
     свой. Сервер берёт адрес пода с интерфейса сам (предупреждение «Using
     autodetected rpc_public_addr» в журнале — ожидаемое). Что адрес пода
     меняется на перезапуске, здесь не мешает: соседей контейнер `peer` ищет
     по DNS-именам подов, а не по запомненным адресам. */}}
{{- define "garage.toml" -}}
# Конфигурация сгенерирована чартом garage; правки здесь переживут ровно до
# следующего `helm upgrade`. Менять — в values релиза.
metadata_dir = "/var/lib/garage/meta"
data_dir = "/var/lib/garage/data"
db_engine = "lmdb"

{{ if .Values.cluster.enabled -}}
replication_factor = {{ int .Values.cluster.replicationFactor }}

rpc_bind_addr = "[::]:3901"
{{- else -}}
replication_factor = 1

rpc_bind_addr = "[::]:3901"
rpc_public_addr = "127.0.0.1:3901"
{{- end }}
{{/* LOCAL DEVIATION (SUP-711): rpc_secret must be 32 BYTES OF HEX — 64 hex
     characters — and Garage refuses to start on anything else:
     «Invalid RPC secret key: expected 32 bytes of random hex». The value this
     listing supplies is the deployment's generated internal credential, which
     is 32 printable characters and not hex, and the marketplace's expression
     language has no hash function to convert it with — so the conversion has
     to happen here or the server never boots (it did not: every 4.0.x
     deployment had `analyst-garage` in CrashLoopBackOff and therefore no
     object storage at all).

     A value that is already 64 hex characters is used as given, so an operator
     who supplies a proper key keeps it; anything else is hashed, which is
     deterministic (the same deployment renders the same key on every
     reconfigure) and keeps the entropy of what was supplied. The whole file is
     a Secret, so neither form reaches the attested snapshot. */}}
{{- $rpcSecret := .Values.rpcSecret | toString -}}
{{- if not (regexMatch "^[0-9a-fA-F]{64}$" $rpcSecret) -}}
{{- $rpcSecret = sha256sum $rpcSecret -}}
{{- end -}}
rpc_secret = {{ $rpcSecret | quote }}

[s3_api]
s3_region = {{ .Values.region | quote }}
api_bind_addr = "[::]:{{ .Values.service.port }}"

[admin]
api_bind_addr = "[::]:{{ .Values.adminPort }}"
admin_token = {{ .Values.adminToken | quote }}
metrics_token = {{ .Values.adminToken | quote }}
{{- end -}}

{{/* Адрес админ-API для Job'а. Headless-сервис, а не сервис S3: админ-порт
     сервисом платформы не публикуется, и Job должен достучаться до пода,
     который ещё не Ready (`publishNotReadyAddresses`). */}}
{{- define "garage.adminUrl" -}}
http://{{ include "garage.fullname" . }}-headless:{{ .Values.adminPort }}
{{- end -}}

{{/* Ёмкость узла в раскладке кластера, в байтах: cluster.capacity или размер
     тома. Garage принимает в API только число, а в values удобнее писать так
     же, как размер PVC, — суффиксы те же, что у Kubernetes. */}}
{{- define "garage.capacityBytes" -}}
{{- $raw := toString (default .Values.persistence.size .Values.cluster.capacity) -}}
{{- if not (regexMatch "^[0-9]+(Ki|Mi|Gi|Ti|Pi|k|K|M|G|T|P)?$" $raw) -}}
{{- fail (printf "garage: ёмкость узла %q не разобрана — нужно число байт или число с суффиксом Ki/Mi/Gi/Ti/Pi или k/M/G/T/P (cluster.capacity или persistence.size)" $raw) -}}
{{- end -}}
{{- $units := dict "" 1 "k" 1000 "K" 1000 "M" 1000000 "G" 1000000000 "T" 1000000000000 "P" 1000000000000000 "Ki" 1024 "Mi" 1048576 "Gi" 1073741824 "Ti" 1099511627776 "Pi" 1125899906842624 -}}
{{- $n := regexReplaceAll "^([0-9]+).*$" $raw "${1}" | atoi -}}
{{- $unit := regexReplaceAll "^[0-9]+" $raw "" -}}
{{- $bytes := mul $n (get $units $unit) -}}
{{- if le (int64 $bytes) 0 -}}
{{- fail "garage: ёмкость узла в кластерном режиме должна быть больше нуля" -}}
{{- end -}}
{{- printf "%d" (int64 $bytes) -}}
{{- end -}}

{{/* Проверки кластерного режима — при рендере, а не в журнале пода. */}}
{{- define "garage.validateCluster" -}}
{{- if .Values.cluster.enabled -}}
{{- $rf := int .Values.cluster.replicationFactor -}}
{{- $n := int .Values.cluster.replicas -}}
{{- if lt $rf 1 -}}
{{- fail "garage: cluster.replicationFactor должен быть не меньше 1" -}}
{{- end -}}
{{- if lt $n $rf -}}
{{- fail (printf "garage: cluster.replicas (%d) меньше cluster.replicationFactor (%d) — копий объекта больше, чем узлов, и раскладка не применится никогда" $n $rf) -}}
{{- end -}}
{{- if not (has .Values.cluster.podAntiAffinity (list "required" "preferred")) -}}
{{- fail (printf "garage: cluster.podAntiAffinity — required или preferred, а не %q" .Values.cluster.podAntiAffinity) -}}
{{- end -}}
{{- $_ := include "garage.capacityBytes" . -}}
{{- end -}}
{{- end -}}

{{/* Контейнер `peer` кластерного режима: соседи и раскладка, в цикле.

     Зачем отдельный цикл, а не bootstrap_peers в garage.toml и не Job.
     bootstrap_peers требует идентификатор узла, а он появляется только на
     первом старте пода — в конфигурацию, которую рендерит helm, его взять
     неоткуда. Job (хук) не годится по двум причинам: Ready под становится
     только после применения раскладки, а `--wait` не запускает post-install
     хуков, пока поды не Ready; и Job отрабатывает раз на установку, а соседей
     приходится находить заново после любого перезапуска — список, который
     сервер помнит, хранит АДРЕСА подов, и после одновременного перезапуска
     всех трёх (перезагрузка узлов, обесточивание) по ним не отвечает никто.

     Каждый проход:

       1. спрашивает у соседей по DNS-именам подов их идентификаторы и
          соединяет с ними свой узел (ConnectClusterNodes идемпотентен);
       2. заявляет свою роль в раскладке — зона = рабочий узел, ёмкость =
          размер тома, — если её нет или она другая (под переехал на другой
          узел);
       3. на нулевом поде — применяет раскладку, если в ней есть заявленные
          изменения. Первую версию — только когда роли заявили все узлы:
          иначе кластер встал бы на неполной раскладке и тут же её
          перекладывал. Применяет один под, а не любой: две версии с одним
          номером, применённые на разных узлах одновременно, Garage не
          разводит.

     Узел, ушедший насовсем (вышел из строя рабочий узел вместе с томом),
     цикл из раскладки НЕ убирает: снять узел — решение инженера (`garage
     layout remove`), а не таймаута. Пока его нет, кластер работает на двух
     оставшихся. */}}
{{- define "garage.peerScript" -}}
# Ответы админ-API печатаются с отступами, по одному полю в строке, —
# первого значения поля хватает, разбирать JSON целиком незачем.
field() { sed -n 's/^ *"'"$1"'": *"\{0,1\}\([^",]*\)"\{0,1\},\{0,1\}$/\1/p' | head -1; }
get() { wget -q -O - -T 10 --header="Authorization: Bearer $ADMIN_TOKEN" "$1"; }
post() { wget -q -O - -T 10 --header="Authorization: Bearer $ADMIN_TOKEN" --post-data="$2" "$1"; }
# Раскладка — строками «раздел id зона ёмкость»: role — применённая, staged —
# заявленная и ждущая применения (зона `-` — узел снимается).
roles() {
  awk '
    /"roles": \[/             { sec = "role" }
    /"stagedRoleChanges": \[/ { sec = "staged" }
    /"id":/          { id = $2; gsub(/[",]/, "", id) }
    /"zone":/        { zone = $2; gsub(/[",]/, "", zone) }
    /"capacity":/    { cap = $2; gsub(/[",]/, "", cap) }
    /"remove": true/ { zone = "-" }
    /^ *}/ { if (id != "" && sec != "") print sec, id, zone, cap; id = ""; zone = ""; cap = "" }
  '
}
LOCAL="http://127.0.0.1:$ADMIN_PORT"
ordinal="${POD_NAME##*-}"
sts="${POD_NAME%-*}"

until self="$(get "$LOCAL/v2/GetNodeInfo?node=self" 2>/dev/null | field nodeId)" && [ -n "$self" ]; do
  sleep 2
done
echo "узел $POD_NAME: $self, зона $NODE_NAME, ёмкость $CAPACITY"

while :; do
  # 1. Соседи — по DNS-именам подов: адрес пода меняется на каждом
  #    перезапуске, имя — нет. Headless-сервис публикует и не-Ready поды,
  #    поэтому имя резолвится с первой секунды.
  peers=""; seen=1; i=0
  while [ "$i" -lt "$REPLICAS" ]; do
    if [ "$i" != "$ordinal" ]; then
      host="$sts-$i.$HEADLESS"
      if id="$(get "http://$host:$ADMIN_PORT/v2/GetNodeInfo?node=self" 2>/dev/null | field nodeId)" && [ -n "$id" ]; then
        peers="$peers${peers:+,}\"$id@$host:$RPC_PORT\""
        seen=$((seen + 1))
      fi
    fi
    i=$((i + 1))
  done
  if [ -n "$peers" ] && ! post "$LOCAL/v2/ConnectClusterNodes" "[$peers]" >/dev/null 2>&1; then
    echo "не удалось соединиться с соседями (отвечают $seen из $REPLICAS)" >&2
  fi

  # 2. Своя роль: заявленная, если есть, иначе применённая.
  if layout="$(get "$LOCAL/v2/GetClusterLayout" 2>/dev/null)"; then
    have="$(printf '%s\n' "$layout" | roles | awk -v id="$self" '
      $2 == id && $1 == "staged" { s = $3 " " $4 }
      $2 == id && $1 == "role"   { r = $3 " " $4 }
      END { print (s != "" ? s : r) }')"
    if [ "$have" != "$NODE_NAME $CAPACITY" ]; then
      if post "$LOCAL/v2/UpdateClusterLayout" \
           "{\"roles\":[{\"id\":\"$self\",\"zone\":\"$NODE_NAME\",\"capacity\":$CAPACITY,\"tags\":[\"$POD_NAME\"]}]}" >/dev/null; then
        echo "роль заявлена: зона $NODE_NAME, ёмкость $CAPACITY (было: ${have:-нет})"
      else
        echo "не удалось заявить роль" >&2
      fi
    fi
  fi

  # 3. Применение — только на нулевом поде.
  if [ "$ordinal" = 0 ] && layout="$(get "$LOCAL/v2/GetClusterLayout" 2>/dev/null)"; then
    version="$(printf '%s\n' "$layout" | field version)"
    rows="$(printf '%s\n' "$layout" | roles)"
    staged="$(printf '%s\n' "$rows" | awk '$1 == "staged"' | wc -l)"
    nodes="$(printf '%s\n' "$rows" | awk '$3 != "-" && $2 != "" { print $2 }' | sort -u | wc -l)"
    if [ "$staged" -gt 0 ] && { [ "$version" -gt 0 ] || [ "$nodes" -ge "$REPLICAS" ]; }; then
      if post "$LOCAL/v2/ApplyClusterLayout" "{\"version\":$((version + 1))}" >/dev/null; then
        echo "раскладка применена: версия $((version + 1)), узлов $nodes"
      else
        echo "раскладка версии $((version + 1)) не применилась" >&2
      fi
    fi
  fi

  if wget -q -O /dev/null -T 5 "$LOCAL/health" 2>/dev/null; then
    sleep "$INTERVAL"
  else
    sleep 5
  fi
done
{{- end -}}
