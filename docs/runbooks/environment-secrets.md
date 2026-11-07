# Управление секретами

Инструкция для сопровождающего проекта: изменить секрет приложения, восстановить его версию,
отозвать доступ или получить административный SSH-ключ. Обычный деплой получает секреты автоматически.
Устройство доступа описано в [архитектуре](../architecture.md#secrets-and-ssh-access),
ключи и типы значений приложения — в [manifest](../../deploy/environment-secrets.json).

Выполняйте команды из корня checkout под собственной учётной записью `yc` с доступом к нужному
секрету. Не выводите значения, не включайте `set -x`, не помещайте секреты в Git, аргументы команд,
логи или комментарии. Временные файлы храните вне репозитория и облачной синхронизации с правами
`0600`; редактор не должен создавать резервные копии. Изменения IAM и учётных данных выполняются
в рамках согласованной задачи.

## Изменить секрет приложения

1. Получите ID из manifest и сохраните ID текущей версии для возможного восстановления:

   ```sh
   SECRET_ID=$(.venv/bin/python -c 'import json; print(json.load(open("deploy/environment-secrets.json"))["lockbox"]["secret_id"])')
   yc lockbox secret get --id "$SECRET_ID" --format json
   ```

   Запишите `current_version.id` в переменную `BASE_VERSION_ID`.

2. Подготовьте файл с изменяемыми полями:

   ```sh
   umask 077
   PAYLOAD_FILE=$(mktemp "${TMPDIR:-/tmp}/findme-lockbox.XXXXXX")
   "${EDITOR:?Укажите локальный редактор}" "$PAYLOAD_FILE"
   ```

   Формат: JSON-массив `[{"key":"ИМЯ_ПОЛЯ","text_value":"новое значение"}]`.
   Для бинарного поля используйте `binary_value` со значением в base64. Имена и типы берите из
   manifest. Неизменяемые поля копировать не нужно: они наследуются из базовой версии.

3. Создайте версию и удалите временный файл, в том числе при ошибке команды:

   ```sh
   yc lockbox secret add-version --id "$SECRET_ID" \
     --base-version-id "${BASE_VERSION_ID:?Укажите текущую версию}" \
     --payload - --format json < "$PAYLOAD_FILE"
   rm -f -- "$PAYLOAD_FILE"
   ```

   При прерывании процедуры также удалите файл. Если создание версии завершилось ошибкой,
   остановитесь и разберитесь с ней до деплоя.

4. Проверьте чтение новой версии и проекцию приложения без изменения ВМ:

   ```sh
   .venv/bin/python scripts/run-with-environment-secrets.py \
     --consumer deploy --identity yc \
     -- .venv/bin/python scripts/verify-environment-secret-projection.py deploy
   ```

   Успех: команда завершилась с кодом `0`, в выводе есть новая `version_id` и
   `[environment-secret-projection] consumer=deploy status=ok`.
   Это проверка получения и схемы, а не работоспособности нового пароля у внешнего сервиса.

5. Выполните релиз через штатный пайплайн и проверьте затронутую функцию. Работающие контейнеры
   не подхватывают новую версию сами. При ротации сначала настройте новые учётные данные у их
   поставщика, а старые отзывайте после успешного переключения.

## Восстановить предыдущую версию

1. Получите список версий и запишите ID подходящей версии в `RESTORE_VERSION_ID`.
   Используйте `SECRET_ID` из предыдущего раздела:

   ```sh
   yc lockbox secret list-versions --id "$SECRET_ID" --format json
   ```

2. Создайте новую текущую версию из выбранной без изменения значений:

   ```sh
   printf '[]' | yc lockbox secret add-version --id "$SECRET_ID" \
     --base-version-id "${RESTORE_VERSION_ID:?Укажите версию для восстановления}" \
     --payload - --format json
   ```

3. Повторите проверку чтения и релиз из предыдущего раздела. Восстановление версии не возвращает
   действие паролям или ключам, уже отозванным у поставщика.

## Отозвать доступ

1. Определите секрет и учётную запись, потерявшую право доступа:

   ```sh
   yc lockbox secret list-access-bindings --id "$SECRET_ID" --format json
   ```

2. Для пользователя удалите его право чтения именно на этом секрете:

   ```sh
   yc lockbox secret remove-access-binding --id "$SECRET_ID" \
     --user-account-id "${USER_ACCOUNT_ID:?Укажите ID пользователя}" \
     --role lockbox.payloadViewer --format json
   ```

   Для CI определите нужный federated credential через `github_oidc.service_account_id` из
   manifest и удалите только его:

   ```sh
   yc iam workload-identity federated-credential list --service-account-id "$SERVICE_ACCOUNT_ID" --format json
   yc iam workload-identity federated-credential delete --id "$FEDERATED_CREDENTIAL_ID" --format json
   ```

3. Проверьте, что отозванная учётная запись больше не может получить секрет, а разрешённая —
   может. Если доступ сохранился, проверьте наследуемые права на каталог и облако.
   Отзыв доступа к Lockbox не обезвреживает уже скопированные пароли: при утечке замените их
   у поставщиков и обновите секрет. При потере своей учётной записи обратитесь к другому
   администратору облака.

<a id="ssh-keys-infrastructure"></a>
## Административный SSH-доступ

Все пайплайны используют одну техническую пару `deploy`; оператор — отдельную пару `petrnikitin`.
Источники и потребители заданы в `ssh_credentials` файла
[`deploy/environment-secrets.json`](../../deploy/environment-secrets.json). Приватные ключи
получает общий resolver; административный секрет не доступен service accounts CI.
Оператору нужны `lockbox.viewer` и `lockbox.payloadViewer` на `findme-photo-vm-admin`
(`e6qes463bl6r09r72580`).

После создания ВМ Infrastructure регистрирует проверенный открытый host key в её metadata
`findme-ssh-host-key`. Для обычного подключения берите текущий адрес и эту запись через
аутентифицированный API ЯО; повторно читать serial output не требуется. Если запись отсутствует
или не проходит проверку OpenSSH, завершите регистрацию ВМ штатным Infrastructure workflow.
Не подтверждайте новый ключ через отключение `StrictHostKeyChecking`.

Укажите `VM_ID` нужной ВМ и задайте `INFRA_ADMIN_SSH_PUBLIC_KEY` значением одноимённой variable
GitHub Environment `infrastructure`. На своей защищённой рабочей станции выполните:

```sh
umask 077
VM_INFO=$(yc compute instance get --id "$VM_ID" --full --format json)
export VM_IP=$(jq -er '.network_interfaces[0].primary_v4_address.one_to_one_nat.address' <<< "$VM_INFO")
export VM_KNOWN_HOSTS_FILE=$(mktemp "${TMPDIR:-/tmp}/findme-known-hosts.XXXXXX")
HOST_KEY=$(jq -er '.metadata["findme-ssh-host-key"] | select(length > 0)' <<< "$VM_INFO")
printf '%s %s\n' "$VM_IP" "$HOST_KEY" > "$VM_KNOWN_HOSTS_FILE"
ssh-keygen -l -E sha256 -f "$VM_KNOWN_HOSTS_FILE"
INFRA_ADMIN_SSH_PUBLIC_KEY="$INFRA_ADMIN_SSH_PUBLIC_KEY" \
  .venv/bin/python scripts/run-with-environment-secrets.py \
  --consumer operator-admin-key --identity yc -- sh -c \
  'exec ssh -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes -o UserKnownHostsFile="$VM_KNOWN_HOSTS_FILE" -i "$ADMIN_SSH_KEY_FILE" "petrnikitin@$VM_IP"'
rm -f -- "$VM_KNOWN_HOSTS_FILE"
```

При ошибке проверки остановитесь; не продолжайте SSH. Resolver сверяет пользователя и пару с
ожидаемым публичным ключом, создаёт приватный файл `0600` только на время дочерней команды и
удаляет его при завершении. Не копируйте этот файл в постоянное хранилище или GitHub.
