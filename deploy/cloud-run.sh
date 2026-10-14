#!/usr/bin/env bash
# Deploy PyQxel to Google Cloud Run: the API as a service that scales to zero, and the
# screener refresh as a job Cloud Scheduler runs after each US trading day.
#
# Run from the repository root after the one-time setup under "Google Cloud Run" in
# README.md, which creates the project and stores the secrets:
#
#   PROJECT_ID=my-project SEC_USER_AGENT="PyQxel you@example.com" deploy/cloud-run.sh
#
# Safe to rerun: every step creates what is missing and updates what exists. Secrets are
# read by Cloud Run from Secret Manager; none pass through this script.
#
# Optional settings, with defaults:
#   REGION=us-central1        Cloud Run, Artifact Registry and Scheduler region
#   SERVICE=pyqxel            service name; the screener job is "$SERVICE-screener"
#   MAX_INSTANCES=2           cap on concurrent instances, which bounds the bill
#   CORS_ORIGINS='["https://app.example"]'   browser origins allowed to call the API
#   SCREENER_SCHEDULE="30 18 * * 1-5"        cron, New York time; "" skips scheduling
#   SCREENER_FMP_REQUESTS_PER_MINUTE=300     the screener's FMP plan's rate limit

set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID to your Google Cloud project ID.}"
SEC_USER_AGENT="${SEC_USER_AGENT:?Set SEC_USER_AGENT to your name and email, e.g. 'PyQxel you@example.com'.}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-pyqxel}"
MAX_INSTANCES="${MAX_INSTANCES:-2}"
CORS_ORIGINS="${CORS_ORIGINS:-}"
SCREENER_SCHEDULE="${SCREENER_SCHEDULE-30 18 * * 1-5}"
SCREENER_FMP_REQUESTS_PER_MINUTE="${SCREENER_FMP_REQUESTS_PER_MINUTE:-300}"

REPOSITORY="$SERVICE"
JOB="$SERVICE-screener"
RUN_ACCOUNT="$SERVICE-run@$PROJECT_ID.iam.gserviceaccount.com"
SCHEDULER_ACCOUNT="$SERVICE-scheduler@$PROJECT_ID.iam.gserviceaccount.com"
IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$REPOSITORY/$SERVICE:$(git rev-parse --short HEAD)-$(date -u +%Y%m%d%H%M%S)"

# Secret Manager name -> environment variable. The first two are required.
SECRET_NAMES=(pyqxel-database-url pyqxel-credentials-key pyqxel-redis-url)
SECRET_VARIABLES=(DATABASE_URL CREDENTIALS_ENCRYPTION_KEY REDIS_URL)
# The screener's licensed FMP key, given to the screener job only, never to the service.
SCREENER_SECRET=pyqxel-screener-fmp-key

gc() { gcloud --project "$PROJECT_ID" --quiet "$@"; }
step() { printf '\n==> %s\n' "$*"; }

step "Enabling APIs"
gc services enable run.googleapis.com artifactregistry.googleapis.com \
    cloudbuild.googleapis.com secretmanager.googleapis.com cloudscheduler.googleapis.com

step "Checking secrets"
secrets=()
for i in "${!SECRET_NAMES[@]}"; do
    name="${SECRET_NAMES[$i]}"
    if gc secrets describe "$name" >/dev/null 2>&1; then
        secrets+=("${SECRET_VARIABLES[$i]}=$name:latest")
    elif [[ $i -lt 2 ]]; then
        echo "Missing secret $name; create it as README.md describes." >&2
        exit 1
    else
        echo "No $name secret: running without Redis, so each instance keeps its own caches and rate limits."
    fi
done
secret_flags="$(IFS=,; echo "${secrets[*]}")"
job_secret_flags="$secret_flags"
screener_secrets=()
if gc secrets describe "$SCREENER_SECRET" >/dev/null 2>&1; then
    screener_secrets+=("SCREENER_FMP_API_KEY=$SCREENER_SECRET:latest")
    job_secret_flags+=",${screener_secrets[0]}"
else
    echo "No $SCREENER_SECRET secret: screener refreshes will fail until it is created." >&2
fi

step "Service accounts"
for account in "$RUN_ACCOUNT" "$SCHEDULER_ACCOUNT"; do
    if ! gc iam service-accounts describe "$account" >/dev/null 2>&1; then
        gc iam service-accounts create "${account%%@*}" --display-name "PyQxel ${account%%@*}"
    fi
done
for flag in "${secrets[@]}" ${screener_secrets[@]+"${screener_secrets[@]}"}; do
    name="${flag#*=}"
    gc secrets add-iam-policy-binding "${name%:latest}" \
        --member "serviceAccount:$RUN_ACCOUNT" --role roles/secretmanager.secretAccessor \
        >/dev/null
done
# Cloud Build runs as the Compute Engine default account in new projects, which no longer
# holds these roles by default.
project_number="$(gc projects describe "$PROJECT_ID" --format 'value(projectNumber)')"
build_account="$project_number-compute@developer.gserviceaccount.com"
for role in roles/artifactregistry.writer roles/logging.logWriter roles/storage.objectViewer; do
    gc projects add-iam-policy-binding "$PROJECT_ID" \
        --member "serviceAccount:$build_account" --role "$role" --condition None >/dev/null
done

step "Artifact Registry"
if ! gc artifacts repositories describe "$REPOSITORY" --location "$REGION" >/dev/null 2>&1; then
    gc artifacts repositories create "$REPOSITORY" --location "$REGION" \
        --repository-format docker --description "PyQxel images"
fi
# Keep only the newest image: the free tier stores 0.5 GB and the image is about 0.33 GB.
policy="$(mktemp)"
trap 'rm -f "$policy"' EXIT
cat >"$policy" <<'JSON'
[
  {"name": "keep-newest", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 1}},
  {"name": "delete-older", "action": {"type": "Delete"}, "condition": {"tagState": "any"}}
]
JSON
gc artifacts repositories set-cleanup-policies "$REPOSITORY" --location "$REGION" \
    --policy "$policy" --no-dry-run >/dev/null

step "Building $IMAGE"
gc builds submit --config deploy/cloudbuild.yaml --substitutions "_IMAGE=$IMAGE" .

# "^|^" makes | the separator, since CORS_ORIGINS is a JSON list with commas.
env_vars="^|^YFINANCE_FALLBACK=false|DATABASE_PGBOUNCER=true|SEC_USER_AGENT=$SEC_USER_AGENT"
if [[ -n "$CORS_ORIGINS" ]]; then
    env_vars+="|CORS_ORIGINS=$CORS_ORIGINS"
fi

step "Deploying the $SERVICE service"
# Scales to zero when idle; billed only while handling requests. The startup probe keeps a
# new revision from taking traffic until its database answers and is migrated. Requests
# may last an hour, for streamed analyses and the quote WebSocket.
gc run deploy "$SERVICE" \
    --region "$REGION" \
    --image "$IMAGE" \
    --service-account "$RUN_ACCOUNT" \
    --allow-unauthenticated \
    --cpu 1 --memory 2Gi \
    --concurrency 40 --timeout 3600 \
    --min-instances 0 --max-instances "$MAX_INSTANCES" \
    --cpu-boost \
    --set-env-vars "$env_vars" \
    --set-secrets "$secret_flags" \
    --startup-probe "httpGet.path=/health/ready,periodSeconds=5,timeoutSeconds=4,failureThreshold=24" \
    --liveness-probe "httpGet.path=/health,periodSeconds=30,timeoutSeconds=5,failureThreshold=3"

step "Deploying the $JOB job"
# Cloud Run's disk is memory, and refreshing fundamentals downloads a 1.3 GB archive.
gc run jobs deploy "$JOB" \
    --region "$REGION" \
    --image "$IMAGE" \
    --service-account "$RUN_ACCOUNT" \
    --cpu 2 --memory 4Gi \
    --task-timeout 3600 --max-retries 1 \
    --command python --args=-m,app.jobs.screener \
    --set-env-vars "$env_vars|SCREENER_FMP_REQUESTS_PER_MINUTE=$SCREENER_FMP_REQUESTS_PER_MINUTE" \
    --set-secrets "$job_secret_flags"

if [[ -n "$SCREENER_SCHEDULE" ]]; then
    step "Scheduling the screener: $SCREENER_SCHEDULE (New York time)"
    gc run jobs add-iam-policy-binding "$JOB" --region "$REGION" \
        --member "serviceAccount:$SCHEDULER_ACCOUNT" --role roles/run.invoker >/dev/null
    action=create
    if gc scheduler jobs describe "$JOB" --location "$REGION" >/dev/null 2>&1; then
        action=update
    fi
    gc scheduler jobs "$action" http "$JOB" \
        --location "$REGION" \
        --schedule "$SCREENER_SCHEDULE" --time-zone America/New_York \
        --uri "https://run.googleapis.com/v2/projects/$PROJECT_ID/locations/$REGION/jobs/$JOB:run" \
        --http-method POST \
        --oauth-service-account-email "$SCHEDULER_ACCOUNT"
fi

url="$(gc run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)')"
step "Deployed: $url"
curl -s "$url/health/ready"
echo
