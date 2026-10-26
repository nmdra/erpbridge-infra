#!/usr/bin/env bash
set -euo pipefail

: "${DNS_NAME:?DNS_NAME is required}"
: "${DNS_TARGET:?DNS_TARGET is required}"
: "${DNS_VERIFY_ID:?DNS_VERIFY_ID is required}"

DNS_WAIT_TIMEOUT_SECONDS="${DNS_WAIT_TIMEOUT_SECONDS:-300}"
DNS_POLL_SECONDS="${DNS_POLL_SECONDS:-10}"
[[ "$DNS_WAIT_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] || { echo "DNS_WAIT_TIMEOUT_SECONDS must be an integer" >&2; exit 2; }
[[ "$DNS_POLL_SECONDS" =~ ^[0-9]+$ ]] || { echo "DNS_POLL_SECONDS must be an integer" >&2; exit 2; }

command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }

DNS_TARGET="${DNS_TARGET%.}"
query_dns() {
  local record_type="$1"
  local resolver="$2"
  local record_name="$DNS_NAME"
  if [[ "$record_type" == "TXT" ]]; then
    record_name="asuid.$DNS_NAME"
  fi
  curl --fail --silent --show-error --retry 2 --max-time 10 \
    -H 'accept: application/dns-json' \
    --get "$resolver" \
    --data-urlencode "name=$record_name" \
    --data-urlencode "type=$record_type"
}

cname_matches() {
  jq -e --arg expected "$DNS_TARGET" '
    [.Answer[]?.data | rtrimstr(".") | select(. == $expected)] | length > 0
  ' >/dev/null
}

txt_matches() {
  jq -e --arg expected "$DNS_VERIFY_ID" '
    [.Answer[]?.data | sub("^\\\""; "") | sub("\\\"$"; "") | select(. == $expected)] | length > 0
  ' >/dev/null
}

deadline=$((SECONDS + DNS_WAIT_TIMEOUT_SECONDS))
while (( SECONDS <= deadline )); do
  # A recursive resolver can retain NXDOMAIN for the zone's full negative TTL.
  # Accept only matching CNAME and TXT values from either public resolver.
  for resolver in 'https://cloudflare-dns.com/dns-query' 'https://dns.google/resolve'; do
    cname_answer="$(query_dns CNAME "$resolver" || true)"
    txt_answer="$(query_dns TXT "$resolver" || true)"
    if cname_matches <<<"$cname_answer" && txt_matches <<<"$txt_answer"; then
      printf 'DNS validation records are visible for %s via %s\n' "$DNS_NAME" "$resolver"
      exit 0
    fi
  done
  sleep "$DNS_POLL_SECONDS"
done

echo "DNS validation records were not visible before the timeout for $DNS_NAME" >&2
exit 1
