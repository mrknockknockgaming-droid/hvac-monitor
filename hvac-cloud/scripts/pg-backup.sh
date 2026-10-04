#!/bin/sh
# Daily database backup for docker-compose.prod.yml: a compressed pg_dump into backups-pg/,
# keeping BACKUP_KEEP_DAYS days. Copy that folder off the server too (see DEPLOY.md).
while true; do
  f="/backups/hvac-$(date -u +%Y%m%d-%H%M).sql.gz"
  if pg_dump -h db -U hvac -d hvac | gzip > "$f.part"; then
    mv "$f.part" "$f"
    echo "$(date -u) backup written: $f ($(du -h "$f" | cut -f1))"
  else
    rm -f "$f.part"
    echo "$(date -u) backup FAILED"
  fi
  find /backups -name 'hvac-*.sql.gz' -mtime +"${BACKUP_KEEP_DAYS:-14}" -delete
  sleep 86400
done
